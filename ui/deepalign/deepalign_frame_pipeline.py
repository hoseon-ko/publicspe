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

import time  # [임시 계측]

import numpy as np
from PyQt6.QtCore import Qt, QThread, pyqtSlot, QSize
from PyQt6.QtWidgets import QListWidgetItem
from PyQt6.QtGui import QImage, QPixmap, QIcon

from ui.deepalign.deepalign_workers import _FrameConvertWorker, _convert_raw_to_rgb
from ui.deepalign._perf_probe import perf_tick  # [임시 계측]
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
                    source: str = "live", skip_calc: bool = False) -> None:
        """raw 프레임을 변환 워커에 제출.

        cmap/vmin/vmax를 메인 스레드에서 읽어 task에 담고 워커에 전달한다.
        변환 완료 후 _on_frame_converted()가 메인 스레드에서 호출된다.

        drop_if_busy=True 이면 워커가 이전 프레임을 처리 중일 때 새 프레임을 버린다
        (live 스트림의 backpressure용).

        source ∈ {"snap","live","acquire"} — ProcStatsPlot 트리거 필터링용.
        skip_calc=True 이면 프레임의 연산(proc, stats) 부하를 스킵한다.
        """
        if not hasattr(self, "_frame_convert_worker"):
            return
        worker = self._frame_convert_worker
        if drop_if_busy and worker.busy:
            return

        _perf_t0 = time.perf_counter()  # [임시 계측]

        cmap = ""
        vmin = 0.0
        vmax = 65535.0
        if hasattr(self, "cam_viewer") and self.cam_viewer is not None:
            cmap = self.cam_viewer.current_cmap or ""
            vmin = self.cam_viewer.display_vmin
            vmax = self.cam_viewer.display_vmax

        # Gather settings from GUI thread
        bg_enabled = bool(getattr(self, '_bg_enabled', False))
        bg_frame = getattr(self, '_bg_frame', None)
        
        proc_enabled = bool(getattr(self, '_proc_enabled', False))
        if skip_calc:
            proc_enabled = False

        proc_mode = int(getattr(self, '_proc_mode', 1))
        proc_region = str(getattr(self, '_proc_region', 'full'))
        proc_image = getattr(self, '_proc_image', None)
        proc_bg_mode = str(getattr(self, '_proc_bg_mode', 'ring'))
        
        spin_gap = getattr(self, 'spin_bg_gap', None)
        spin_thick = getattr(self, 'spin_bg_thickness', None)
        bg_gap = int(spin_gap.value()) if spin_gap is not None else 2
        bg_thickness = int(spin_thick.value()) if spin_thick is not None else 10
        spin_pitch = getattr(self, 'spin_pitch_nm', None)
        pitch_nm = float(spin_pitch.value()) if spin_pitch is not None else 72.0
        
        sig_roi_rect = self._get_sig_roi_rect()
        bg_roi_rect = self._get_bg_box_roi_rect()

        worker.submit({
            "raw": raw,
            "cmap": cmap,
            "vmin": vmin,
            "vmax": vmax,
            "gallery_label": gallery_label,
            "source": source,
            
            "bg_enabled": bg_enabled,
            "bg_frame": bg_frame,
            
            "proc_enabled": proc_enabled,
            "proc_mode": proc_mode,
            "proc_region": proc_region,
            "proc_image": proc_image,
            "proc_bg_mode": proc_bg_mode,
            "bg_gap": bg_gap,
            "bg_thickness": bg_thickness,
            "pitch_nm": pitch_nm,
            "sig_roi_rect": sig_roi_rect,
            "bg_roi_rect": bg_roi_rect,
        })

        if source == "live":  # [임시 계측]
            perf_tick("main.push_frame", (time.perf_counter() - _perf_t0) * 1000.0)

    def _get_proc_roi_metadata(self) -> dict:
        """현재 proc ROI / BG 설정을 SPE extra_metadata 호환 dict로 반환.
        save_spe() 의 extra_metadata["ProcROI"] 에 그대로 전달하면 됩니다."""
        meta: dict[str, str] = {}

        sig = self._get_sig_roi_rect()
        if sig is not None:
            x0, y0, x1, y1 = sig
            meta["SigX"]      = str(int(round(x0)))
            meta["SigY"]      = str(int(round(y0)))
            meta["SigWidth"]  = str(int(round(x1 - x0)))
            meta["SigHeight"] = str(int(round(y1 - y0)))

        bg_mode = getattr(self, '_proc_bg_mode', 'ring')
        meta["BgMode"] = bg_mode

        if bg_mode == 'ring':
            gap_spin   = getattr(self, 'spin_bg_gap',       None)
            thick_spin = getattr(self, 'spin_bg_thickness', None)
            meta["BgGap"]       = str(gap_spin.value()   if gap_spin   else 2)
            meta["BgThickness"] = str(thick_spin.value() if thick_spin else 10)
        elif bg_mode == 'manual':
            bg = self._get_bg_box_roi_rect()
            if bg is not None:
                bx0, by0, bx1, by1 = bg
                meta["BgX"]      = str(int(round(bx0)))
                meta["BgY"]      = str(int(round(by0)))
                meta["BgWidth"]  = str(int(round(bx1 - bx0)))
                meta["BgHeight"] = str(int(round(by1 - by0)))

        region = getattr(self, '_proc_region', 'full')
        meta["Region"] = region
        meta["Mode"]   = str(getattr(self, '_proc_mode', 1))

        return meta

    def _get_sig_roi_rect(self) -> tuple[float, float, float, float] | None:
        """신호 ROI 좌표 (x0,y0,x1,y1) 반환. proc_signal 전용 아이템 기반. 없으면 None."""
        try:
            rect = self.cam_viewer.view.interactions.get_proc_roi('signal')
        except AttributeError:
            return None
        if rect is None:
            return None
        return (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height())

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
            rect = self._get_sig_roi_rect()  # (x0,y0,x1,y1) or None
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

        # BG 픽셀 추출 — 모드에 따라 분기
        bg_pixels = None
        bg_mode = getattr(self, '_proc_bg_mode', 'ring')
        if roi_slice is not None:
            if bg_mode == 'ring':
                try:
                    from ui.deepalign.roi_finder import extract_ring_pixels
                    rect = self._get_sig_roi_rect()
                    if rect is not None:
                        x0r, y0r, x1r, y1r = rect
                        sig_xywh = (
                            int(min(x0r, x1r)), int(min(y0r, y1r)),
                            int(abs(x1r - x0r)), int(abs(y1r - y0r)),
                        )
                        _gap_spin   = getattr(self, 'spin_bg_gap', None)
                        _thick_spin = getattr(self, 'spin_bg_thickness', None)
                        gap_px   = _gap_spin.value()   if _gap_spin   is not None else 2
                        thick_px = _thick_spin.value() if _thick_spin is not None else 10
                        ring = extract_ring_pixels(raw.astype(np.float64), sig_xywh,
                                                  gap=gap_px, thickness=thick_px)
                        if ring is not None and ring.size > 0:
                            bg_pixels = ring.reshape(1, -1)
                except Exception:
                    pass
            elif bg_mode == 'manual':
                try:
                    bg_rect = self._get_bg_box_roi_rect()
                    if bg_rect is not None:
                        bx0, by0, bx1, by1 = bg_rect
                        h, w = raw.shape[:2]
                        bx0i = max(0, int(round(min(bx0, bx1))))
                        bx1i = min(w, int(round(max(bx0, bx1))))
                        by0i = max(0, int(round(min(by0, by1))))
                        by1i = min(h, int(round(max(by0, by1))))
                        if bx1i > bx0i and by1i > by0i:
                            bg_pixels = raw[by0i:by1i, bx0i:bx1i].astype(np.float64)
                except Exception:
                    pass
            # bg_mode == 'none' → bg_pixels remains None (calc_functions 내부 하위 20% 추정)

        # 구조체(ImageMetrics)를 통해 통계 추출 및 캐싱
        spin_pitch = getattr(self, 'spin_pitch_nm', None)
        pitch_nm = float(spin_pitch.value()) if spin_pitch is not None else 72.0
        metrics = ImageMetrics(sample, bg_2d=bg_pixels, pitch_nm=pitch_nm)
        stats_dict = metrics.to_dict()
                
        calc_logger.info(
            f"Mode {mode} [{region}] | Opt1={stats_dict['opt1']:.4f}  Opt2={stats_dict['opt2']:.4f}  Opt3={stats_dict['opt3']:.4f}"
        )

        # 통계 저장 — snap/live/acquire 핸들러가 ProcStatsPlot.add_point_dict() 에 전달
        self._last_proc_stats = stats_dict
        return out

    def _init_ring_overlay(self) -> None:
        """Ring BG 오버레이 초기화. _wire_camera_actions()에서 호출."""
        from ui.deepalign.ring_bg_overlay import RingBGOverlay
        try:
            scene = self.cam_viewer.view._scene
            self._ring_overlay = RingBGOverlay(scene)
        except AttributeError:
            self._ring_overlay = None
            return
        try:
            # proc_roi_updated: 신호/BG ROI 그리기 완료 또는 이동 완료 시 발생
            self.cam_viewer.view.interactions.proc_roi_updated.connect(
                self._on_proc_roi_updated)
        except AttributeError:
            pass

    def _on_proc_roi_updated(self, mode: str, rect) -> None:
        """사용자가 proc ROI를 직접 그리거나 이동했을 때 호출 (silent 업데이트는 오지 않음).
        auto-refine 기준점(_orig_sig_roi_coarse)을 초기화한다."""
        if mode == 'signal':
            # 사용자가 새로 그렸거나 이동 → 기준점 리셋
            self._orig_sig_roi_coarse = None
            self._update_sig_roi_label(rect)
            btn = getattr(self, 'btn_auto_refine_roi', None)
            if btn is not None:
                roi_mode = (getattr(self, '_proc_enabled', False)
                            and getattr(self, '_proc_region', 'full') == 'roi')
                btn.setEnabled(roi_mode and rect is not None)
        elif mode == 'bg':
            lbl = getattr(self, 'lbl_bg_roi_status', None)
            if lbl is not None:
                if rect is not None:
                    lbl.setText(
                        f"({int(rect.x())},{int(rect.y())}) "
                        f"{int(rect.width())}×{int(rect.height())}")
                else:
                    lbl.setText("—")
        # 드로잉/이동 후 모드 해제
        try:
            self.cam_viewer.view.interactions.set_roi_mode(None)
        except AttributeError:
            pass
        self._refresh_ring_overlay()

    def _update_sig_roi_label(self, rect) -> None:
        lbl = getattr(self, 'lbl_sig_roi_status', None)
        if lbl is None:
            return
        if rect is not None:
            lbl.setText(
                f"Signal ROI: ({int(rect.x())},{int(rect.y())}) "
                f"{int(rect.width())}×{int(rect.height())}")
        else:
            lbl.setText("Signal ROI: —")

    def _refresh_ring_overlay(self) -> None:
        """BoxROI 변경 / BG 모드 변경 / Gap·Thickness 변경 시 호출."""
        overlay = getattr(self, '_ring_overlay', None)
        if overlay is None:
            return
        if not (getattr(self, '_proc_bg_mode', 'ring') == 'ring'
                and getattr(self, '_proc_enabled', False)
                and getattr(self, '_proc_region', 'full') == 'roi'):
            overlay.hide()
            return
        rect = self._get_sig_roi_rect()
        if rect is None:
            overlay.hide()
            return
        x0, y0, x1, y1 = rect
        gap_spin   = getattr(self, 'spin_bg_gap', None)
        thick_spin = getattr(self, 'spin_bg_thickness', None)
        gap   = gap_spin.value()   if gap_spin   is not None else 2
        thick = thick_spin.value() if thick_spin is not None else 10
        overlay.update(x0, y0, x1, y1, gap, thick)

    def _get_bg_box_roi_rect(self) -> tuple[float, float, float, float] | None:
        """BG ROI 좌표 (x0,y0,x1,y1) 반환. proc_bg 전용 아이템 기반. 없으면 None."""
        try:
            rect = self.cam_viewer.view.interactions.get_proc_roi('bg')
        except AttributeError:
            return None
        if rect is None:
            return None
        return (rect.x(), rect.y(), rect.x() + rect.width(), rect.y() + rect.height())

    def _auto_refine_roi(self) -> None:
        """신호 proc ROI를 현재 프레임의 밝은 패턴에 맞게 자동 정밀화.

        항상 사용자가 마지막으로 직접 그린/이동한 ROI(_orig_sig_roi_coarse)를 기준으로
        탐색하므로 반복 클릭해도 ROI가 무한 확장하지 않는다.
        """
        from ui.deepalign.roi_finder import find_pattern_roi
        from PyQt6.QtCore import QRectF

        raw = getattr(self, '_last_raw_frame', None)
        if raw is None:
            return

        # ── 기준점: 사용자가 그린 원본 ROI (auto-refine 결과가 아님) ──
        orig = getattr(self, '_orig_sig_roi_coarse', None)
        if orig is None:
            # 첫 번째 호출 — 현재 ROI를 원본으로 저장
            orig = self._get_sig_roi_rect()
            if orig is None:
                return
            self._orig_sig_roi_coarse = orig

        x0, y0, x1, y1 = orig
        coarse = (
            int(min(x0, x1)), int(min(y0, y1)),
            int(abs(x1 - x0)), int(abs(y1 - y0)),
        )

        thr_spin    = getattr(self, 'spin_refine_threshold', None)
        blur_spin   = getattr(self, 'spin_refine_blur',      None)
        margin_spin = getattr(self, 'spin_refine_margin',    None)
        expand_spin = getattr(self, 'spin_refine_expand',    None)
        thr    = thr_spin.value()    if thr_spin    is not None else 70.0
        blur   = blur_spin.value()   if blur_spin   is not None else 2.0
        margin = margin_spin.value() if margin_spin is not None else 5
        # expand: 원본 ROI 기준으로만 적용 → 반복해도 누적 안 됨
        expand = expand_spin.value() if expand_spin is not None else 0

        refined = find_pattern_roi(raw, coarse,
                                   blur_sigma=blur,
                                   threshold_pct=thr,
                                   margin=margin,
                                   search_expand=expand)
        if refined is None:
            return

        fx, fy, fw, fh = refined
        new_rect = QRectF(float(fx), float(fy), float(fw), float(fh))

        # silent=True: proc_roi_updated emit 안 함 → _orig_sig_roi_coarse 유지
        try:
            self.cam_viewer.view.interactions.set_proc_roi('signal', new_rect, silent=True)
        except AttributeError:
            return

        # 레이블 직접 갱신 (proc_roi_updated를 타지 않으므로)
        self._update_sig_roi_label(new_rect)
        self._refresh_ring_overlay()

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

    @pyqtSlot(dict)
    def _on_frame_converted(self, result: dict) -> None:
        """워커 변환 완료 — 메인 스레드에서 뷰어 갱신 + 갤러리 추가."""
        rgb = result["rgb"]
        raw_after_bg = result["raw_after_bg"]
        processed_raw = result["processed_raw"]
        stats_dict = result["stats_dict"]
        gallery_label = result["gallery_label"]
        source = result["source"]

        self._last_raw_frame = raw_after_bg
        self._last_proc_stats = stats_dict

        _perf_live = (source == "live")  # [임시 계측]
        _perf_t0 = time.perf_counter()  # [임시 계측]

        # Mode 1/2/3 통계가 생성되었으면 시계열 플롯에 추가
        if stats_dict is not None and hasattr(self, "proc_stats_panel"):
            try:
                self.proc_stats_panel.add_point_dict(source, stats_dict)
            except Exception as e:
                print(f"Error adding point to proc stats: {e}")

        _perf_t_plot = time.perf_counter()  # [임시 계측]
        self._on_live_frame_ready(rgb, processed_raw)
        if _perf_live:  # [임시 계측]
            _perf_t_view = time.perf_counter()
            perf_tick("main.add_point_dict", (_perf_t_plot - _perf_t0) * 1000.0)
            perf_tick("main.viewer_update", (_perf_t_view - _perf_t_plot) * 1000.0)
            perf_tick("main.on_converted_total", (_perf_t_view - _perf_t0) * 1000.0)

        if gallery_label:
            self._add_to_gallery(processed_raw, gallery_label, rgb=rgb)

        # LIVE 버튼 텍스트에 렌더링 FPS 표시 업데이트
        if getattr(self, "_hub_live_active", False) and hasattr(self, "cam_viewer") and self.cam_viewer is not None:
            fps = getattr(self.cam_viewer.viewer, "_current_fps", 0.0)
            if fps > 0.0:
                self._update_dash_label(self.btn_live_air, "LIVE", f"ON AIR ({fps:.1f} FPS)")
            else:
                self._update_dash_label(self.btn_live_air, "LIVE", "ON AIR")

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
