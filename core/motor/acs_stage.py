"""
core/motor/acs_stage.py
ACS SPiiPlus 6축 키네마틱 스테이지 컨트롤러.

- 모든 Write 명령(Enable, Move, Stop 등)은 단일 워커 스레드(AcsWorker)에서 순차적으로 처리됩니다.
- Read 명령(Position, State Polling)은 타이머를 통해 주기적으로 수행됩니다.
"""

from __future__ import annotations

import sys
import os
import builtins
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from core.logger import dev_logger
from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot, QTimer, Qt, QMetaObject, Q_ARG

log = logging.getLogger(__name__)

# DLL 경로 설정
DLL_PATH = str(Path(__file__).resolve().parent.parent.parent)

_ACS_OK = False
_ACS_IMPORT_ERROR: Optional[str] = None
_Api = None
_AxisEnum = None
_MotionFlags = None

try:
    import clr
    if DLL_PATH not in sys.path:
        sys.path.append(DLL_PATH)
    os.add_dll_directory(DLL_PATH)
    clr.AddReference("ACS.SPiiPlusNET")
    from ACS.SPiiPlusNET import Api as _Api, Axis as _AxisEnum
    try:
        from ACS.SPiiPlusNET import MotionFlags as _MotionFlags
    except:
        pass
    _ACS_OK = True
except Exception as e:
    _ACS_IMPORT_ERROR = str(e)

AXIS_LABELS = ["Y1", "Z1", "X1", "Z2", "Y2", "Z3"]
DEFAULT_PORT = 700

# Motor State Bits
_MST_ENABLE   = 0x01
_MST_INMOTION = 0x02
_MST_INPOS    = 0x10

def is_available() -> bool:
    return _ACS_OK

def _axis_enum(idx: int):
    mapping = [0, 1, 4, 5, 8, 9]
    if 0 <= idx < len(mapping):
        axis_num = mapping[idx]
        if _AxisEnum is not None:
            try: return getattr(_AxisEnum, f"ACSC_AXIS_{axis_num}")
            except AttributeError: return axis_num
        return axis_num
    return idx

class AcsWorker(QObject):
    """ACS 하드웨어 통신 전담 워커 (단일 스레드 상주)"""
    positions_updated = pyqtSignal(list)
    states_updated    = pyqtSignal(list)
    connection_lost   = pyqtSignal()

    def __init__(self, api):
        super().__init__()
        self._api = api
        self._timer = None
        self._is_polling = False

    @pyqtSlot()
    def setup(self):
        self._timer = QTimer()
        self._timer.timeout.connect(self._poll)
        self._timer.setInterval(200)
        self._timer.start()
        self._is_polling = True

    def _poll(self):
        if not self._is_polling: return
        try:
            positions = []
            states = []
            for i in range(6):
                ax = _axis_enum(i)
                positions.append(float(self._api.GetFPosition(ax)))
                mstate = int(self._api.GetMotorState(ax))
                states.append({
                    "enabled": bool(mstate & _MST_ENABLE),
                    "moving":  bool(mstate & _MST_INMOTION),
                    "in_pos":  bool(mstate & _MST_INPOS)
                })
            self.positions_updated.emit(positions)
            self.states_updated.emit(states)
        except Exception as e:
            log.debug(f"[ACS Worker] Poll error: {e}")

    @pyqtSlot(int, bool)
    def set_enable(self, axis: int, enable: bool):
        try:
            ax = _axis_enum(axis)
            mstate = int(self._api.GetMotorState(ax))
            # 이동 중이면 먼저 멈춤 (bit 0x02: In Motion)
            if mstate & _MST_INMOTION:
                self._api.Halt(ax)
                
            if enable: self._api.Enable(ax)
            else: self._api.Disable(ax)
        except Exception as e:
            log.error(f"[ACS Worker] Enable error: {e}")

    @pyqtSlot()
    def set_enable_all(self):
        for i in range(6): self.set_enable(i, True)

    @pyqtSlot()
    def set_disable_all(self):
        for i in range(6): self.set_enable(i, False)

    @pyqtSlot(int, float)
    def move_to(self, axis: int, target: float):
        try:
            ax = _axis_enum(axis)
            flags = _MotionFlags(0) if _MotionFlags is not None else 0
            self._api.ToPoint(flags, ax, float(target))
        except Exception as e:
            log.error(f"[ACS Worker] Move error: {e}")

    @pyqtSlot(int)
    def stop_axis(self, axis: int):
        try: self._api.Halt(_axis_enum(axis))
        except: pass

    @pyqtSlot()
    def stop_all(self):
        for i in range(6): self.stop_axis(i)

    def stop(self):
        self._is_polling = False
        if self._timer: self._timer.stop()


class AcsStageController(QObject):
    """ACS SPiiPlus 제어용 상위 인터페이스 (Thread-Safe)"""

    def __init__(self):
        super().__init__()
        self._api = None
        self._connected = False
        self._simulator = False
        self._worker: Optional[AcsWorker] = None
        self._thread: Optional[QThread] = None
        self.dry_run = False
        
        # 소프트 리밋 초기화
        from core.motor.kinematic_calc import DEFAULT_PLUS_LIMITS, DEFAULT_MINUS_LIMITS
        self.plus_limits = DEFAULT_PLUS_LIMITS.copy()
        self.minus_limits = DEFAULT_MINUS_LIMITS.copy()

    # ── 연결 ─────────────────────────────────────────────────────────

    def connect(self, ip: str, port: int = DEFAULT_PORT):
        if not _ACS_OK: raise RuntimeError(f"DLL Load Failed: {_ACS_IMPORT_ERROR}")
        self._api = _Api()
        self._api.OpenCommEthernetTCP(ip, port)
        self._connected = True
        self._simulator = False
        dev_logger.info(f"ACS Connected: {ip}:{port}")

    def connect_simulator(self):
        if not _ACS_OK: raise RuntimeError(f"DLL Load Failed: {_ACS_IMPORT_ERROR}")
        self._api = _Api()
        self._api.OpenCommSimulator()
        self._connected = True
        self._simulator = True

    def disconnect(self):
        self.stop_polling()
        if self._api and self._connected:
            try: self._api.CloseComm()
            except: pass
        self._api = None
        self._connected = False

    @property
    def is_connected(self) -> bool: return self._connected

    # ── 제어 명령 (비동기 위임) ───────────────────────────────────────

    def _invoke(self, method: str, *args):
        if self._worker:
            QMetaObject.invokeMethod(self._worker, method, 
                                     Qt.ConnectionType.QueuedConnection,
                                     *[Q_ARG(type(a), a) for a in args])

    def enable_all(self): self._invoke("set_enable_all")
    def disable_all(self): self._invoke("set_disable_all")
    def stop_all(self): self._invoke("stop_all")
    def halt(self, axis: int): self._invoke("stop_axis", axis)

    def move_to(self, axis: int, target_mm: float, wait: bool = False):
        if target_mm > self.plus_limits[axis] or target_mm < self.minus_limits[axis]:
            raise ValueError(f"Limit Violation: Axis{axis}")
        if self.dry_run: return
        
        self._invoke("move_to", axis, float(target_mm))
        if wait: self.wait_in_position_all()

    def move_by(self, axis: int, delta_mm: float, wait: bool = False):
        current = self.get_position(axis)
        self.move_to(axis, current + delta_mm, wait=wait)

    # ── 상태 조회 (직접 호출 허용 - Read Only) ────────────────────────

    def get_position(self, axis: int) -> float:
        if not self._connected: return 0.0
        return float(self._api.GetFPosition(_axis_enum(axis)))

    def is_enabled(self, axis: int) -> bool:
        if not self._connected: return False
        return bool(int(self._api.GetMotorState(_axis_enum(axis))) & _MST_ENABLE)

    def wait_for_enabled_all(self, timeout_ms: int = 2000) -> bool:
        start = time.time()
        while (time.time() - start) * 1000 < timeout_ms:
            if all(self.is_enabled(i) for i in range(6)): return True
            time.sleep(0.05)
        return False

    def wait_in_position_all(self, timeout_ms: int = 30000):
        if not self._connected: return
        for i in range(6):
            try: self._api.WaitMotionEnd(_axis_enum(i), timeout_ms)
            except: pass

    # ── 워커 생명주기 ────────────────────────────────────────────────

    def start_polling(self, on_positions, on_states, on_lost):
        if not self._connected: return
        self.stop_polling()
        self._thread = QThread()
        self._worker = AcsWorker(self._api)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.setup)
        self._worker.positions_updated.connect(on_positions)
        self._worker.states_updated.connect(on_states)
        self._worker.connection_lost.connect(on_lost)
        self._thread.start()

    def stop_polling(self):
        if self._worker:
            self._worker.stop()
            self._worker.deleteLater()
        if self._thread:
            self._thread.quit()
            self._thread.wait(2000)
        self._worker = None
        self._thread = None
