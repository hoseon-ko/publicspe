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
from PyQt6.QtWidgets import QApplication

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

    def __init__(self):
        super().__init__()
        self._api = None
        self._conn_params = None # (type, ip, port)
        self._timer = None
        self._is_polling = False

    def set_connection_params(self, conn_type, ip=None, port=None):
        self._conn_params = (conn_type, ip, port)

    @pyqtSlot()
    def setup(self):
        # 1. API 객체 생성 및 연결을 반드시 이 스레드 내에서 수행!
        try:
            self._api = _Api()
            conn_type, ip, port = self._conn_params
            self._api.OpenCommSimulator() if conn_type == "simulator" else self._api.OpenCommEthernetTCP(ip, port)
            log.info(f"[ACS Worker] Connected via {conn_type}")
            
            # [Safety] 연결 직후 모든 축 초기화 (잔여 모션 종료 + 에러 클리어)
            try:
                for i in range(6):
                    ax = _axis_enum(i)
                    self._api.Kill(ax)
                    self._api.FaultAck(ax)
                log.info("[ACS Worker] Initial hardware reset done (Kill/FaultAck all axes)")
            except:
                pass
        except Exception as e:
            log.error(f"[ACS Worker] Connection failed: {e}")
            self.connection_lost.emit()
            return

        # 2. 타이머 시작
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
            is_enabled = bool(mstate & _MST_ENABLE)

            if is_enabled == enable:
                return

            was_polling = self._is_polling
            self._is_polling = False

            if enable:
                # 1. 에러 상태 초기화 (FaultAck)
                try:
                    self._api.FaultAck(ax)
                except:
                    pass

                # 2. Enable 시도 (최대 2회)
                success = False
                for attempt in range(2):
                    try:
                        self._api.Enable(ax)
                        success = True
                        break
                    except Exception as e:
                        err_msg = str(e)
                        if "motion is in progress" in err_msg.lower():
                            log.warning(f"[ACS Worker] Axis {axis} busy, killing motion and retrying... ({attempt+1}/2)")
                            try:
                                self._api.Kill(ax)
                                time.sleep(0.1)
                            except:
                                pass
                        else:
                            raise e # 다른 에러는 즉시 보고
                
                if not success:
                    log.error(f"[ACS Worker] Axis {axis} Enable failed after retries.")
            else: 
                self._api.Disable(ax)
                
            self._is_polling = was_polling
        except Exception as e:
            log.error(f"[ACS Worker] Enable error (Axis {axis}): {e}")
            self._is_polling = True

    @pyqtSlot()
    def set_enable_all(self):
        for i in range(6): 
            self.set_enable(i, True)
            time.sleep(0.05) # 지연 시간 확대 (10ms -> 50ms)

    @pyqtSlot()
    def set_disable_all(self):
        for i in range(6): 
            self.set_enable(i, False)

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
        
        self._axes      = list(range(6))
        
        # 캐시된 하드웨어 상태 (스레드 간 경합 방지용)
        self._last_positions = [0.0] * 6
        self._last_states    = [{"enabled": False, "moving": False, "in_pos": False}] * 6
        
        # 소프트 리밋 초기화
        from core.motor.kinematic_calc import DEFAULT_PLUS_LIMITS, DEFAULT_MINUS_LIMITS
        self.plus_limits = DEFAULT_PLUS_LIMITS.copy()
        self.minus_limits = DEFAULT_MINUS_LIMITS.copy()

    # ── 연결 ─────────────────────────────────────────────────────────

    def connect(self, ip: str, port: int = DEFAULT_PORT):
        if not _ACS_OK: raise RuntimeError(f"DLL Load Failed: {_ACS_IMPORT_ERROR}")
        self.stop_polling() # 기존 세션 강제 종료
        self._connected = True
        self._simulator = False
        self._conn_info = ("ethernet", ip, port)
        dev_logger.info(f"ACS Connection requested: {ip}:{port}")

    def connect_simulator(self):
        if not _ACS_OK: raise RuntimeError(f"DLL Load Failed: {_ACS_IMPORT_ERROR}")
        self._connected = True
        self._simulator = True
        self._conn_info = ("simulator", None, None)

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

    # ── 상태 조회 (캐시 데이터 사용 - 하드웨어 직접 접근 금지) ────────────────
    
    def _update_positions(self, positions):
        self._last_positions = positions

    def _update_states(self, states):
        self._last_states = states

    def get_position(self, axis: int) -> float:
        if 0 <= axis < 6:
            return self._last_positions[axis]
        return 0.0

    def is_enabled(self, axis: int) -> bool:
        if 0 <= axis < 6:
            return self._last_states[axis]["enabled"]
        return False

    def is_moving(self, axis: int) -> bool:
        if 0 <= axis < 6:
            return self._last_states[axis]["moving"]
        return False

    def wait_for_enabled_all(self, timeout_ms: int = 2000) -> bool:
        start = time.time()
        main_thread = QApplication.instance().thread()
        while (time.time() - start) * 1000 < timeout_ms:
            if all(self.is_enabled(i) for i in range(6)): 
                return True
            # 메인 스레드에서 호출된 경우에만 UI 이벤트 처리
            if QThread.currentThread() == main_thread:
                QApplication.processEvents()
            time.sleep(0.05)
        return False

    def wait_in_position_all(self, timeout_ms: int = 30000):
        """하드웨어 API 직접 호출 대신 캐시된 moving 상태를 체크하며 대기."""
        start = time.time()
        main_thread = QApplication.instance().thread()
        while (time.time() - start) * 1000 < timeout_ms:
            if not any(self.is_moving(i) for i in range(6)):
                return
            if QThread.currentThread() == main_thread:
                QApplication.processEvents()
            time.sleep(0.05)

    # ── 워커 생명주기 ────────────────────────────────────────────────

    def start_polling(self, on_positions, on_states, on_lost):
        if not self._connected: return
        self.stop_polling()
        self._thread = QThread()
        self._worker = AcsWorker()
        self._worker.set_connection_params(*self._conn_info)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.setup)
        
        # 내부 캐시 업데이트 연결
        self._worker.positions_updated.connect(self._update_positions)
        self._worker.states_updated.connect(self._update_states)
        
        # 외부 콜백 연결
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
            if not self._thread.wait(2000):
                log.warning("[ACS Stage] Thread didn't stop gracefully, terminating...")
                self._thread.terminate()
                self._thread.wait(500)
        self._worker = None
        self._thread = None
