"""DeepAlign 프레임 표시 파이프라인 파일.

이 파일은 들어오는 프레임을 DeepAlign viewer에 맞게 변환하고 밀어넣는 역할을 합니다.
주요 역할은 다음과 같습니다.
- raw 프레임을 변환 워커에 제출 (numpy 연산은 백그라운드 스레드)
- 변환 완료 후 메인 스레드에서만 viewer 갱신
- live/snap/acquire 프레임을 viewer에 반영
- LiveTab과 공유될 때 colormap/range 변경 동기화
- viewer 상태와 ROI dock/list 동기화
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import Qt, QThread, pyqtSlot, QSize
from PyQt6.QtWidgets import QListWidgetItem
from PyQt6.QtGui import QImage, QPixmap, QIcon

from ui.deepalign.deepalign_workers import _FrameConvertWorker, _convert_raw_to_rgb
from ui.deepalign.image_metrics import ImageMetrics


class FramePipelineMixin:

    def _init_frame_convert_worker(self) -> None:
        """프레임 변환 워커 스레드 초기화. __init__ 에서 한 번만 호출."""
        self._frame_convert_thread = QThread(self)
        self._frame_convert_worker = _FrameConvertWorker()
        self._frame_convert_worker.moveToThread(self._frame_convert_thread)
        # result_ready 수신측은 self(메인 스레드 QWidget) — Qt가 자동으로 QueuedConnection 적용
        self._frame_convert_worker.result_ready.connect(self._on_frame_converted)
        self._frame_convert_thread.start()

    def _on_live_frame_ready(self, rgb, raw):
        if not hasattr(self, "cam_viewer"):
            return
        if raw is not None:
            self.cam_viewer.set_source_image(raw)
        else:
            self.cam_viewer.set_source_image(rgb)
        self.cam_viewer.set_live_frame(rgb, fit=self._viewer_first_frame)
        self._viewer_first_frame = False

    def _push_frame(self, raw, gallery_label: str = "", drop_if_busy: bool = False,
                    source: str = "live") -> None:
        """raw 프레임을 변환 워커에 제출.

        cmap/vmin/vmax를 메인 스레드에서 읽어 task에 담고 워커에 전달한다.
        변환 완료 후 _on_frame_converted()가 메인 스레드에서 호출된다.

        drop_if_busy=True 이면 워커가 이전 프레임을 처리 중일 때 새 프레임을 버린다
        (live 스트림의 backpressure용).

        source ∈ {"snap","live","acquire"} — ProcStatsPlot 트리거 필터링용.
        """
        if not hasattr(self, "_frame_convert_worker"):
            return
        worker = self._frame_convert_worker
        if drop_if_busy and worker.busy:
            return

        cmap = ""
        vmin = 0.0
        vmax = 65535.0
        if hasattr(self, "cam_viewer") and self.cam_viewer is not None:
            cmap = self.cam_viewer.current_cmap or ""
            vmin = self.cam_viewer.display_vmin
            vmax = self.cam_viewer.display_vmax

        raw = self._apply_background_subtraction(raw)
        self._last_proc_stats = None
        raw = self._apply_proc_image(raw)
        # Mode 1/2 통계가 생성되었으면 시계열 플롯에 추가
        stats_dict = getattr(self, "_last_proc_stats", None)
        if stats_dict is not None and hasattr(self, "proc_stats_panel"):
            try:
                self.proc_stats_panel.add_point_dict(source, stats_dict)
            except Exception as e:
                print(f"Error adding point to proc stats: {e}")

        worker.submit({
            "raw": raw,
            "cmap": cmap,
            "vmin": vmin,
            "vmax": vmax,
            "gallery_label": gallery_label,
        })

    def _get_first_box_roi_rect(self) -> tuple[float, float, float, float] | None:
        """현재 viewer 의 첫 번째 Box ROI 좌표 (x0,y0,x1,y1) 반환. 없으면 None."""
        from ui.roi_items import BoxROI
        viewer = getattr(self, "cam_viewer", None)
        if viewer is None:
            return None
        try:
            rois = viewer.viewer.view.interactions._rois
        except AttributeError:
            return None
        for roi_id in sorted(rois.keys()):
            roi = rois[roi_id]
            if isinstance(roi, BoxROI):
                return (roi._x0, roi._y0, roi._x1, roi._y1)
        return None

    def _apply_proc_image(self, raw: np.ndarray) -> np.ndarray:
        """카메라 frame 처리 + 통계 계산.

        Mode 1: raw - proc  (차감)
        Mode 2: raw / proc  (나누기)
        Mode 3: raw 그대로 통과 + 통계만 계산 (proc image 불요)

        Region:
        - "full": 전체 이미지 대상
        - "roi" : 첫 번째 Box ROI 영역만 대상 (mask 적용)
        """
        from core.logger import calc_logger

        if not getattr(self, '_proc_enabled', False) or raw is None:
            return raw

        mode = getattr(self, '_proc_mode', 1)
        region = getattr(self, '_proc_region', 'full')

        # Mode 1/2 는 proc image 필수
        img = getattr(self, '_proc_image', None)
        if mode in (1, 2) and (img is None or img.shape != raw.shape):
            return raw

        # ROI mask 계산 (region == 'roi' 일 때만)
        roi_slice = None
        if region == "roi":
            rect = self._get_first_box_roi_rect()  # (x0,y0,x1,y1) or None
            if rect is not None:
                h, w = raw.shape[:2]
                x0, y0, x1, y1 = rect
                x0 = max(0, min(w, int(round(min(x0, x1)))))
                x1 = max(0, min(w, int(round(max(x0, x1)))))
                y0 = max(0, min(h, int(round(min(y0, y1)))))
                y1 = max(0, min(h, int(round(max(y0, y1)))))
                if x1 > x0 and y1 > y0:
                    roi_slice = (slice(y0, y1), slice(x0, x1))

        orig_dtype = raw.dtype

        # 1. 먼저 대상 영역 정의 (전체 또는 ROI)
        raw_roi = raw if roi_slice is None else raw[roi_slice]
        img_roi = None
        if img is not None:
            img_roi = img if roi_slice is None else img[roi_slice]

        if mode == 1:
            # ROI 영역에 대해서만 연산
            full = raw.astype(np.float32) - img.astype(np.float32)
            sample = raw_roi.astype(np.float32) - img_roi.astype(np.float32)
            
            if np.issubdtype(orig_dtype, np.unsignedinteger):
                full = np.clip(full, 0, np.iinfo(orig_dtype).max)
                sample = np.clip(sample, 0, np.iinfo(orig_dtype).max)
            out = full.astype(orig_dtype)

        elif mode == 2:
            # 필요한 영역만 계산하여 메모리 및 시간 절약
            denom = img_roi.astype(np.float32)
            denom[denom == 0] = np.nan
            sample = raw_roi.astype(np.float32) / denom
            
            # full 연산이 반드시 필요한 경우가 아니라면 생략 가능
            full = raw.astype(np.float32) / np.where(img == 0, np.nan, img) 
            out = full.astype(np.float32)

        elif mode == 3:
            out = raw
            sample = raw_roi

        else:
            return raw

        # 구조체(ImageMetrics)를 통해 통계 추출 및 캐싱
        metrics = ImageMetrics(sample)
        stats_dict = metrics.to_dict()
                
        calc_logger.info(
            f"Mode {mode} [{region}] | Opt1={stats_dict['opt1']:.4f}  Opt2={stats_dict['opt2']:.4f}  Opt3={stats_dict['opt3']:.4f}"
        )

        # 통계 저장 — snap/live/acquire 핸들러가 ProcStatsPlot.add_point_dict() 에 전달
        self._last_proc_stats = stats_dict
        return out

    def _apply_background_subtraction(self, raw: np.ndarray) -> np.ndarray:
        """배경 차감 활성화 상태이면 raw에서 _bg_frame을 뺀 값을 반환."""
        if not getattr(self, '_bg_enabled', False):
            return raw
        bg = getattr(self, '_bg_frame', None)
        if bg is None or raw is None or bg.shape != raw.shape:
            return raw
        result = raw.astype(np.int32) - bg.astype(np.int32)
        if np.issubdtype(raw.dtype, np.unsignedinteger):
            result = np.clip(result, 0, np.iinfo(raw.dtype).max)
        return result.astype(raw.dtype)

    @pyqtSlot(object, object, str)
    def _on_frame_converted(self, rgb, raw, gallery_label: str) -> None:
        """워커 변환 완료 — 메인 스레드에서 뷰어 갱신 + 갤러리 추가."""
        self._on_live_frame_ready(rgb, raw)
        if gallery_label:
            self._add_to_gallery(raw, gallery_label, rgb=rgb)

    def _on_cmap_changed_sync(self, cmap_name: str):
        if self._live_tab and hasattr(self._live_tab, "image_viewer"):
            cb = self._live_tab.image_viewer.cmap_combo
            idx = cb.findText(cmap_name, Qt.MatchFlag.MatchFixedString | Qt.MatchFlag.MatchContains)
            if idx < 0:
                idx = cb.findText(cmap_name, Qt.MatchFlag.MatchFixedString)

            if idx >= 0:
                cb.setCurrentIndex(idx)
            elif cmap_name.lower() == "off":
                cb.setCurrentText("Off")
            else:
                cb.setCurrentText(cmap_name)

    def _update_roi_list_from_viewer(self):
        if not hasattr(self, "roi_list") or not self.cam_viewer:
            return
        self.roi_list.clear()
        internal_list = self.cam_viewer.get_roi_list_widget()
        for i in range(internal_list.count()):
            src_item = internal_list.item(i)
            new_item = QListWidgetItem(src_item.text())
            new_item.setData(Qt.ItemDataRole.UserRole, src_item.data(Qt.ItemDataRole.UserRole))
            self.roi_list.addItem(new_item)

    def _on_roi_item_clicked(self, item):
        roi_id = item.data(Qt.ItemDataRole.UserRole)
        if roi_id is not None:
            self.cam_viewer.set_active_roi(roi_id, "profile")

    def _on_roi_del_clicked(self):
        item = self.roi_list.currentItem()
        if item:
            roi_id = item.data(Qt.ItemDataRole.UserRole)
            self.cam_viewer.delete_roi(roi_id)

    def _on_roi_clear_clicked(self):
        self.cam_viewer.delete_all_rois()

    def _add_to_gallery(self, raw: np.ndarray, label: str = "",
                        rgb: np.ndarray | None = None) -> None:
        """캡처된 프레임을 Analysis 탭의 갤러리에 추가한다.

        rgb를 넘기면 재변환 없이 재사용 (_on_frame_converted에서 전달).
        """
        if not hasattr(self, "list_an_gallery"):
            return
        try:
            if rgb is None:
                rgb = _convert_raw_to_rgb(raw, "", 0.0, 65535.0)

            h, w = rgb.shape[:2]
            THUMB = 120
            if h > THUMB or w > THUMB:
                scale = THUMB / max(h, w)
                nh, nw = max(1, int(h * scale)), max(1, int(w * scale))
                sy = max(1, h // nh)
                sx = max(1, w // nw)
                thumb = np.ascontiguousarray(rgb[::sy, ::sx, :])
                th, tw = thumb.shape[:2]
            else:
                thumb = np.ascontiguousarray(rgb)
                th, tw = h, w

            qimg = QImage(thumb.data, tw, th, 3 * tw, QImage.Format.Format_RGB888)
            pix = QPixmap.fromImage(qimg)

            if not label:
                label = f"Frame {self.list_an_gallery.count() + 1}"

            item = QListWidgetItem(QIcon(pix), label)
            item.setData(Qt.ItemDataRole.UserRole, raw)
            self.list_an_gallery.addItem(item)
            self.list_an_gallery.scrollToBottom()
        except Exception as e:
            print(f"Gallery error: {e}")
