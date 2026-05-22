"""
core/camera/hikvision.py
HIKVISION MVS SDK 기반 카메라 구현체.

실제 MVS SDK(MvCameraControl_class)가 없는 환경에서도
import는 성공하며, connect() 시점에 ImportError를 올린다.
"""

from __future__ import annotations
from core.logger import dev_logger

import sys
import threading
import time
from ctypes import cast, c_ubyte, c_void_p, POINTER, c_bool
from typing import Any, Callable, List, Optional

import numpy as np
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from core.camera.base import BaseCamera, CameraCapabilities, NotSupportedError

# MVS SDK는 선택적 의존성
MVS_PATH = r"C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport"
MVS_DLL_PATH = r"C:\Program Files (x86)\Common Files\MVS\Runtime\Win64_x64"
_MVS_OK = False
_MVS_IMPORT_ERROR: Optional[str] = None
FrameInfoCallBack2: Optional[Any] = None

try:
    import os
    if MVS_PATH not in sys.path:
        sys.path.append(MVS_PATH)
    # MVS SDK는 winmode=0(레거시) 방식으로 DLL을 로드하므로 PATH에 직접 추가해야 함
    # (os.add_dll_directory는 winmode=0에서 무효)
    if MVS_DLL_PATH not in os.environ.get("PATH", ""):
        os.environ["PATH"] = MVS_DLL_PATH + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(MVS_DLL_PATH)
    from MvCameraControl_class import (
        MvCamera, MV_CC_DEVICE_INFO_LIST, MV_CC_DEVICE_INFO,
        MV_GIGE_DEVICE, MV_USB_DEVICE, MV_FRAME_OUT, MVCC_FLOATVALUE,
        get_platform_functype
    )
    import ctypes
    _MVS_OK = True
    
    # 콜백 함수 타입 정의
    _fun_ctype = get_platform_functype()
    FrameInfoCallBack2 = _fun_ctype(None, POINTER(MV_FRAME_OUT), c_void_p, c_bool)
except Exception as e:
    _MVS_IMPORT_ERROR = str(e)
    FrameInfoCallBack2 = None


def is_available() -> bool:
    return _MVS_OK


def list_devices() -> List[str]:
    """연결 가능한 HIKVISION 카메라 목록을 문자열 리스트로 반환."""
    if not _MVS_OK:
        return []
    dev_list = MV_CC_DEVICE_INFO_LIST()
    MvCamera.MV_CC_EnumDevices(MV_GIGE_DEVICE | MV_USB_DEVICE, dev_list)
    result = []
    for i in range(dev_list.nDeviceNum):
        dev = cast(dev_list.pDeviceInfo[i], POINTER(MV_CC_DEVICE_INFO)).contents
        if dev.nTLayerType == MV_USB_DEVICE:
            name = bytes(dev.SpecialInfo.stUsb3VInfo.chModelName).decode("utf-8", errors="ignore").strip('\x00')
        elif dev.nTLayerType == MV_GIGE_DEVICE:
            name = bytes(dev.SpecialInfo.stGigEInfo.chModelName).decode("utf-8", errors="ignore").strip('\x00')
        else:
            name = "Unknown Device"
        result.append(f"[{i}] {name}")
    return result


class HikvisionLiveDispatcher(QObject):
    """C++ 콜백 스레드에서 수신한 프레임을 GUI 스레드로 안전하게 전달하기 위한 QObject."""
    frame_ready = pyqtSignal(np.ndarray)


class HikvisionCamera(BaseCamera):
    """
    HIKVISION MVS SDK 카메라.

    사용법:
        cam = HikvisionCamera(device_index=0)
        cam.connect()
        cam.start_live(lambda frame: ...)
        cam.stop_live()
        cam.disconnect()
    """

    def __init__(self, device_index: int = 0):
        self._device_index = device_index
        self._cam: Optional[Any] = None
        self._dev_list: Optional[Any] = None
        self._worker: Optional[Any] = None
        self._thread: Optional[Any] = None
        self._grabbing = False
        self._connected = False
        self._model = "HIKVISION"
        self._serial = ""
        self._sdk_lock = threading.Lock()
        self._fps_range: tuple[float, float] = (0.1, 1000.0)
        self._exposure_range_ms: tuple[float, float] = (0.01, 1000.0)
        
        # 콜백 방식용 객체 및 함수 포인터 GC 방지 참조
        self._dispatcher = HikvisionLiveDispatcher()
        self._callback_fn = None

    # ── BaseCamera 구현 ───────────────────────────────────────────────

    @property
    def capabilities(self) -> CameraCapabilities:
        return CameraCapabilities(
            has_roi=False,
            exposure_range_ms=self._exposure_range_ms,
            has_fps_control=True,
            fps_range=self._fps_range,
            has_binarize=True,
            has_log_scale=True,
            has_bg_subtraction=True,
            has_centroid=True,
        )

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        if not _MVS_OK:
            raise RuntimeError(f"MVS SDK를 불러올 수 없습니다: {_MVS_IMPORT_ERROR}")

        # SDK 글로벌 초기화 (여러번 호출해도 무방하거나 첫 연결시 필수)
        MvCamera.MV_CC_Initialize()

        self._dev_list = MV_CC_DEVICE_INFO_LIST()
        MvCamera.MV_CC_EnumDevices(MV_GIGE_DEVICE | MV_USB_DEVICE, self._dev_list)

        if self._dev_list.nDeviceNum == 0:
            raise RuntimeError("연결된 HIKVISION 카메라가 없습니다")
        if self._device_index >= self._dev_list.nDeviceNum:
            raise RuntimeError(f"카메라 인덱스 {self._device_index}가 범위를 벗어났습니다 (총 {self._dev_list.nDeviceNum}개)")

        dev = cast(
            self._dev_list.pDeviceInfo[self._device_index],
            POINTER(MV_CC_DEVICE_INFO)
        ).contents

        # 모델/시리얼 추출
        if dev.nTLayerType == MV_USB_DEVICE:
            info = dev.SpecialInfo.stUsb3VInfo
            self._model = bytes(info.chModelName).decode("utf-8", errors="ignore").strip('\x00')
            self._serial = bytes(info.chSerialNumber).decode("utf-8", errors="ignore").strip('\x00')
        elif dev.nTLayerType == MV_GIGE_DEVICE:
            info = dev.SpecialInfo.stGigEInfo
            self._model = bytes(info.chModelName).decode("utf-8", errors="ignore").strip('\x00')
            self._serial = ""

        self._cam = MvCamera()
        if self._cam.MV_CC_CreateHandle(dev) != 0:
            raise RuntimeError("카메라 핸들 생성 실패")
        if self._cam.MV_CC_OpenDevice() != 0:
            raise RuntimeError("카메라 열기 실패")

        # GigE 카메라의 경우 패킷 사이즈 최적화 필수 (누락시 패킷 로스/프레임 드랍 발생)
        if dev.nTLayerType == MV_GIGE_DEVICE:
            nPacketSize = self._cam.MV_CC_GetOptimalPacketSize()
            if int(nPacketSize) > 0:
                ret = self._cam.MV_CC_SetIntValue("GevSCPSPacketSize", nPacketSize)
                if ret != 0:
                    dev_logger.warning(f"GigE 패킷 사이즈 설정 실패: {ret}")

        # UserSetLoad(Default) — 일부 모델(GO-8105M 등)은 이 호출 없이는
        # ExposureTime 등 파라미터 노드가 GenICam ACCESS 오류를 반환함
        self._cam.MV_CC_SetEnumValue("UserSetSelector", 0)
        self._cam.MV_CC_SetCommandValue("UserSetLoad")

        # 카메라 실제 범위 읽기 (하드코딩 대신 GenICam 노드 기준)
        fval = MVCC_FLOATVALUE()
        if self._cam.MV_CC_GetFloatValue("ExposureTime", fval) == 0 and fval.fMax > fval.fMin:
            self._exposure_range_ms = (fval.fMin / 1000.0, fval.fMax / 1000.0)
        if self._cam.MV_CC_GetFloatValue("AcquisitionFrameRate", fval) == 0 and fval.fMax > fval.fMin:
            self._fps_range = (max(0.1, fval.fMin), fval.fMax)

        self._connected = True

    def disconnect(self) -> None:
        if self._grabbing:
            self.stop_live()
        if self._cam is not None:
            try:
                self._cam.MV_CC_CloseDevice()
                self._cam.MV_CC_DestroyHandle()  # 필수 리소스 해제
            except Exception:
                pass
        self._connected = False
        self._cam = None
        
        try:
            MvCamera.MV_CC_Finalize()  # SDK 글로벌 리소스 해제
        except:
            pass

    def get_exposure_ms(self) -> float:
        if not self._connected or self._cam is None:
            raise RuntimeError("카메라가 연결되지 않았습니다")
        val = MVCC_FLOATVALUE()
        with self._sdk_lock:
            ret = self._cam.MV_CC_GetFloatValue("ExposureTime", val)
        if ret == 0:
            return val.fCurValue / 1000.0  # us → ms
        raise RuntimeError("ExposureTime 읽기 실패")

    def set_exposure_ms(self, ms: float) -> float:
        if not self._connected or self._cam is None:
            raise RuntimeError("카메라가 연결되지 않았습니다")
        with self._sdk_lock:
            self._cam.MV_CC_SetEnumValue("ExposureAuto", 0)
            self._cam.MV_CC_SetFloatValue("ExposureTime", ms * 1000.0)
        return self.get_exposure_ms()

    def snap(self) -> np.ndarray:
        if not self._connected or self._cam is None:
            raise RuntimeError("카메라가 연결되지 않았습니다")
        # live 스트림이 없을 때만 StartGrabbing/StopGrabbing을 직접 관리한다.
        # _grabbing=True이면 _AcquisitionWorker가 이미 스트림을 열고 있으므로
        # StartGrabbing을 다시 호출하면 SDK 상태가 꼬이고, StopGrabbing은
        # live 스트림을 죽인다. 두 경우 모두 _sdk_lock으로 GetImageBuffer를 직렬화한다.
        was_grabbing = self._grabbing
        if not was_grabbing:
            # 단일 프레임 모드로 변경 (1 = SingleFrame)
            self._cam.MV_CC_SetEnumValue("AcquisitionMode", 1)
            self._cam.MV_CC_SetEnumValue("TriggerMode", 1)      # Trigger On
            self._cam.MV_CC_SetEnumValue("TriggerSource", 7)    # Software Trigger
            ret = self._cam.MV_CC_StartGrabbing()
            if ret != 0:
                raise RuntimeError(f"StartGrabbing 실패 (에러코드: {ret})")
            ret = self._cam.MV_CC_SetCommandValue("TriggerSoftware")
            if ret != 0:
                print(f"소프트웨어 트리거 송신 실패: {ret}")
        try:
            out = MV_FRAME_OUT()
            with self._sdk_lock:
                ret = self._cam.MV_CC_GetImageBuffer(out, 3000)
                if ret != 0:
                    raise RuntimeError(f"프레임 취득 실패 (에러코드: {ret})")
                
                p_addr = out.pBufAddr
                if not p_addr:
                    self._cam.MV_CC_FreeImageBuffer(out)
                    raise RuntimeError("프레임 버퍼 포인터가 NULL입니다.")
                    
                pBuf = cast(p_addr, c_void_p).value
                if pBuf is None:
                    self._cam.MV_CC_FreeImageBuffer(out)
                    raise RuntimeError("프레임 버퍼 주소가 올바르지 않습니다.")
                    
                h, w = out.stFrameInfo.nHeight, out.stFrameInfo.nWidth
                n = out.stFrameInfo.nFrameLen
                
                # 안전한 슬라이싱을 위해 크기 검증
                actual_pixels = h * w
                if n < actual_pixels:
                    self._cam.MV_CC_FreeImageBuffer(out)
                    raise RuntimeError(f"프레임 크기 부족: expected {actual_pixels}, got {n}")
                    
                raw = np.frombuffer(
                    (c_ubyte * n).from_address(pBuf), dtype=np.uint8
                )[:actual_pixels].reshape(h, w).copy()
                
                self._cam.MV_CC_FreeImageBuffer(out)
            return raw
        finally:
            if not was_grabbing:
                self._cam.MV_CC_StopGrabbing()

    def start_live(self, frame_cb: Callable[[np.ndarray], None]) -> None:
        if not self._connected or self._cam is None:
            raise RuntimeError("카메라가 연결되지 않았습니다")
        if self._grabbing:
            return

        if FrameInfoCallBack2 is None:
            raise RuntimeError("MVS SDK 콜백 타입을 정의할 수 없습니다.")

        # 1. 디스패처에 콜백 슬롯 연결
        self._dispatcher.frame_ready.connect(frame_cb)

        # 2. C++ SDK가 호출할 Python 콜백 함수 정의
        def _py_callback(pstFrame, pUser, bAutoFree):
            try:
                if not pstFrame:
                    return
                # POINTER(MV_FRAME_OUT)에서 실제 구조체 인스턴스 획득
                stFrame = cast(pstFrame, POINTER(MV_FRAME_OUT)).contents
                if not stFrame:
                    return

                p_addr = stFrame.pBufAddr
                if not p_addr:
                    return

                pBuf = cast(p_addr, c_void_p).value
                if not pBuf:
                    return

                h = stFrame.stFrameInfo.nHeight
                w = stFrame.stFrameInfo.nWidth
                n = stFrame.stFrameInfo.nFrameLen

                actual_pixels = h * w
                if n < actual_pixels:
                    return

                # 프레임 버퍼 데이터를 numpy 배열로 안전하게 복사
                raw = np.frombuffer(
                    (c_ubyte * n).from_address(pBuf), dtype=np.uint8
                )[:actual_pixels].reshape(h, w).copy()

                # 디스패처(GUI 스레드 소속)를 통해 프레임 시그널 방출
                self._dispatcher.frame_ready.emit(raw)
            except Exception as e:
                dev_logger.error(f"[HikvisionCamera] Callback error: {e}")

        # ctypes 콜백 함수로 래핑하고, GC 방지를 위해 인스턴스 변수에 저장
        self._callback_fn = FrameInfoCallBack2(_py_callback)

        # 3. SDK 설정 및 콜백 등록
        with self._sdk_lock:
            # 연속 모드 및 트리거 모드 Off 설정 (실시간으로 프레임을 SDK 스레드가 획득하여 푸시함)
            self._cam.MV_CC_SetEnumValue("AcquisitionMode", 2)  # Continuous
            self._cam.MV_CC_SetEnumValue("TriggerMode", 0)      # Trigger Off

            # 콜백 등록 (bAutoFree=True로 설정하여 콜백 완료 후 SDK가 버퍼를 자동으로 해제하도록 함)
            ret = self._cam.MV_CC_RegisterImageCallBackEx2(self._callback_fn, None, True)
            if ret != 0:
                try:
                    self._dispatcher.frame_ready.disconnect(frame_cb)
                except Exception:
                    pass
                self._callback_fn = None
                raise RuntimeError(f"콜백 등록 실패 (에러코드: {ret})")

            # 스트리밍 취득 시작
            ret = self._cam.MV_CC_StartGrabbing()
            if ret != 0:
                try:
                    self._dispatcher.frame_ready.disconnect(frame_cb)
                except Exception:
                    pass
                self._callback_fn = None
                raise RuntimeError(f"카메라 시작 실패 (에러코드: {ret})")

        self._grabbing = True

    def stop_live(self) -> None:
        if not self._grabbing:
            return

        with self._sdk_lock:
            if self._cam:
                try:
                    self._cam.MV_CC_StopGrabbing()
                except Exception as e:
                    dev_logger.error(f"[HikvisionCamera] StopGrabbing failed: {e}")

        # 디스패처 슬롯 연결 해제
        try:
            self._dispatcher.frame_ready.disconnect()
        except Exception:
            pass

        # 콜백 함수 포인터 제거 (GC 대상이 됨)
        self._callback_fn = None
        self._grabbing = False

    # ── FPS 제어 ──────────────────────────────────────────────────────

    def set_fps(self, fps: float) -> float:
        if not self._connected or self._cam is None:
            raise RuntimeError("카메라가 연결되지 않았습니다")
        with self._sdk_lock:
            self._cam.MV_CC_SetBoolValue("AcquisitionFrameRateEnable", True)
            self._cam.MV_CC_SetFloatValue("AcquisitionFrameRate", fps)
        return self.get_fps()

    def get_fps(self) -> float:
        if not self._connected or self._cam is None:
            raise RuntimeError("카메라가 연결되지 않았습니다")
        val = MVCC_FLOATVALUE()
        with self._sdk_lock:
            # ResultingFrameRate: 노출 등 제약을 반영한 실제 달성 가능 FPS
            # AcquisitionFrameRate: 사용자가 설정한 목표값 (제약 반영 안 됨)
            ret = self._cam.MV_CC_GetFloatValue("ResultingFrameRate", val)
            if ret != 0:
                ret = self._cam.MV_CC_GetFloatValue("AcquisitionFrameRate", val)
        if ret == 0:
            return val.fCurValue
        raise RuntimeError("AcquisitionFrameRate 읽기 실패")

    def disable_fps_lock(self) -> None:
        if self._cam:
            self._cam.MV_CC_SetBoolValue("AcquisitionFrameRateEnable", False)

    # ── 카메라 정보 ───────────────────────────────────────────────────

    def camera_name(self) -> str:
        return self._model or "HIKVISION"

    def camera_model(self) -> str:
        return self._model

    def camera_serial(self) -> str:
        return self._serial

    def get_raw_camera(self):
        """MVS MvCamera 인스턴스를 직접 반환 (고급 사용)."""
        return self._cam
