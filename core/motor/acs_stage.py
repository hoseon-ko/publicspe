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
import socket
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
System = None
Enum = None
_Api = None
_AxisEnum = None
_MotionFlags = None

try:
    import clr
    import System as _System
    from System import Enum as _Enum
    if DLL_PATH not in sys.path:
        sys.path.append(DLL_PATH)
    os.add_dll_directory(DLL_PATH)
    clr.AddReference("ACS.SPiiPlusNET")
    from ACS.SPiiPlusNET import Api as _Api, Axis as _AxisEnum
    try:
        from ACS.SPiiPlusNET import MotionFlags as _MotionFlags
    except:
        pass
    System = _System
    Enum = _Enum
    _ACS_OK = True
except Exception as e:
    _ACS_IMPORT_ERROR = str(e)

AXIS_LABELS = ["Y1", "Z1", "X1", "Z2", "Y2", "Z3"]
DEFAULT_PORT = 700

# Motor State Bits (ACS.SPiiPlusNET.MotorStates enum 실제값)
_MST_ENABLE   = 0x01
_MST_INPOS    = 0x10
_MST_MOVE      = 0x20   # ACSC_MST_MOVE
_MST_ACC       = 0x40   # ACSC_MST_ACC

# 구동 중 판단: MOVE 또는 ACC 비트
_MST_ANY_MOTION = _MST_MOVE | _MST_ACC  # 0x60

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
    """ACS 하드웨어 통신 담당 워커 (단일 스레드 상주)"""
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
        # 1. API 객체 생성 및 연결은 반드시 워커 스레드 내에서 실행!
        try:
            log.info(f"[ACS Worker] setup() start @ thread={int(self.thread().currentThreadId())}")
            self._api = _Api()
            conn_type, ip, port = self._conn_params
            log.info(f"[ACS Worker] Connecting: {conn_type} {ip}:{port}")
            if conn_type != "simulator" and ip:
                try:
                    with socket.create_connection((ip, int(port)), timeout=1.5):
                        pass
                except Exception as sock_err:
                    log.warning(f"[ACS Worker] TCP preflight failed for {ip}:{port}: {sock_err}")
                    self.connection_lost.emit()
                    return
            self._api.OpenCommSimulator() if conn_type == "simulator" else self._api.OpenCommEthernetTCP(ip, port)
            log.info(f"[ACS Worker] Connected via {conn_type}")
            
            # [Safety] 연결 직후 에러 클리어
            for i in range(6):
                try:
                    self._api.FaultClear(_axis_enum(i))
                    log.debug(f"[ACS Worker] FaultClear Axis {i} OK")
                except Exception as fe:
                    log.warning(f"[ACS Worker] FaultClear Axis {i} failed: {fe}")
            log.info("[ACS Worker] Initial FaultClear all axes done")

        except Exception as e:
            log.error(f"[ACS Worker] Connection failed: {e}")
            self.connection_lost.emit()
            return

        # 2. 폴링 타이머 시작
        try:
            self._timer = QTimer()
            self._timer.timeout.connect(self._poll)
            self._timer.setInterval(200)
            self._timer.start()
            self._is_polling = True
            log.info("[ACS Worker] Polling timer started")
        except Exception as e:
            log.error(f"[ACS Worker] Timer start failed: {e}\n{traceback.format_exc()}")

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
                    "moving":  bool(mstate & _MST_ANY_MOTION),
                    "in_pos":  bool(mstate & _MST_INPOS)
                })
            self.positions_updated.emit(positions)
            self.states_updated.emit(states)
        except Exception as e:
            log.debug(f"[ACS Worker] Poll error: {e}")

    def _wait_axis_stopped(self, ax, timeout: float = 2.0):
        """모든 모션 비트(_MST_ANY_MOTION)가 꺼질 때까지 폴링 대기(최대 timeout초)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                mst = int(self._api.GetMotorState(ax))
                if not (mst & _MST_ANY_MOTION):
                    return
            except:
                return
            time.sleep(0.02)
        log.warning(f"[ACS Worker] Axis stop wait timed out ({timeout}s)")

    @pyqtSlot(int, bool)
    def set_enable(self, axis: int, enable: bool):
        try:
            ax = _axis_enum(axis)
            mstate = int(self._api.GetMotorState(ax))
            is_enabled = bool(mstate & _MST_ENABLE)

            if is_enabled == enable:
                return

            if enable:
                # MOVE/ACC 중이면 Enable 불가하므로 사전 정지 대기(최대 3초)
                deadline = time.time() + 3.0
                while time.time() < deadline:
                    mst = int(self._api.GetMotorState(ax))
                    if not (mst & _MST_ANY_MOTION):
                        break
                    time.sleep(0.05)
                else:
                    log.warning(f"[ACS Worker] Axis {axis}: still in motion after 3s, attempting Enable anyway")

                try:
                    self._api.FaultClear(ax)
                except:
                    pass
                self._api.Enable(ax)
            else:
                self._api.Disable(ax)

        except Exception as e:
            log.error(f"[ACS Worker] Enable error (Axis {axis}): {e}")

    @pyqtSlot()
    def set_enable_all(self):
        #self._is_polling = False
        try:
            self._do_enable_all()
        finally:
            self._is_polling = True

    def _do_enable_all(self):
        # KillAll → MOVE/ACC 소멸 대기 → FaultClear → Enable
        try:
            #self._api.KillAll()
            pass
        except:
            pass

        deadline = time.time() + 3.0
        while time.time() < deadline:
            all_stopped = all(
                not (int(self._api.GetMotorState(_axis_enum(i))) & _MST_ANY_MOTION)
                for i in range(6)
            )
            if all_stopped:
                break
            time.sleep(0.05)

     # 1. 6개 축 + 종결자(NONE) 1개 = 총 7개 크기 배열 생성
        raw_indices = [0, 1, 4, 5, 8, 9]
        axis_array = System.Array.CreateInstance(_AxisEnum, len(raw_indices) + 1)
        
        # 2. 실제 축 데이터 채우기
        for i, val in enumerate(raw_indices):
            ax_obj = Enum.ToObject(_AxisEnum, val)
            axis_array.SetValue(ax_obj, i)
            
        # 3. 핵심: 마지막 칸에 ACSC_NONE (-1) 주입
        # C# 코드의 result[result.Length - 1] = Axis.ACSC_NONE; 부분
        none_obj = Enum.ToObject(_AxisEnum, -1) # 보통 ACSC_NONE은 -1입니다.
        axis_array.SetValue(none_obj, len(raw_indices))

        log.info(f"[ACS Worker] Attempting EnableM with type-safe array")
        time.sleep(0.1)  # EnableM 직전 잠시 대기
        self._api.EnableM(axis_array)

        # _poll()이 꺼진 동안 상태를 직접 emit
        # try:
        #     states = []
        #     for i in range(6):
        #         mst = int(self._api.GetMotorState(_axis_enum(i)))
        #         states.append({
        #             "enabled": bool(mst & _MST_ENABLE),
        #             "moving":  bool(mst & _MST_ANY_MOTION),
        #             "in_pos":  bool(mst & _MST_INPOS)
        #         })
        #     self.states_updated.emit(states)
        # except Exception as e:
        #     log.debug(f"[ACS Worker] Post-enable state emit error: {e}")

    @pyqtSlot()
    def set_disable_all(self):
        self._is_polling = False
        try:
            for i in range(6):
                self.set_enable(i, False)
        finally:
            self._is_polling = True

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

    @pyqtSlot()
    def stop(self):
        self._is_polling = False
        if self._timer:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None


class AcsStageController(QObject):
    """ACS SPiiPlus 제어용 상위 인터페이스 (Thread-Safe)"""
    positions_updated = pyqtSignal(list)
    states_updated    = pyqtSignal(list)
    connection_lost   = pyqtSignal()

    # 워커 명령용 내부 시그널 (QueuedConnection으로 Worker Thread에 전달)
    _cmd_enable      = pyqtSignal(int, bool)
    _cmd_enable_all   = pyqtSignal()
    _cmd_disable_all = pyqtSignal()
    _cmd_move_to      = pyqtSignal(int, float)
    _cmd_stop_axis   = pyqtSignal(int)
    _cmd_stop_all    = pyqtSignal()

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

    # 연결 제어 섹션
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

    @property
    def is_simulator(self) -> bool: return self._simulator

    # 제어 명령 (시그널을 통한 워커 슬롯 호출, QueuedConnection)
    def enable_all(self):  self._cmd_enable_all.emit()
    def disable_all(self): self._cmd_disable_all.emit()
    def stop_all(self):    self._cmd_stop_all.emit()
    def halt(self, axis: int): self._cmd_stop_axis.emit(axis)

    def move_to(self, axis: int, target_mm: float, wait: bool = False):
        if target_mm > self.plus_limits[axis] or target_mm < self.minus_limits[axis]:
            raise ValueError(f"Limit Violation: Axis{axis}")
        if self.dry_run: return

        self._cmd_move_to.emit(axis, float(target_mm))
        if wait: self.wait_in_position_all()

    def move_by(self, axis: int, delta_mm: float, wait: bool = False):
        current = self.get_position(axis)
        self.move_to(axis, current + delta_mm, wait=wait)

    # 상태 조회 (캐시 데이터 사용 - 하드웨어 직접 접근 금지)
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
            # 메인 스레드에서 호출될 경우에만 UI 이벤트 처리
            if QThread.currentThread() == main_thread:
                QApplication.processEvents()
            time.sleep(0.05)
        return False

    def wait_in_position_all(self, timeout_ms: int = 30000):
        """하드웨어 API 직접 호출 없이 캐시된 moving 상태를 체크하며 대기"""
        start = time.time()
        main_thread = QApplication.instance().thread()
        while (time.time() - start) * 1000 < timeout_ms:
            if not any(self.is_moving(i) for i in range(6)):
                return
            if QThread.currentThread() == main_thread:
                QApplication.processEvents()
            time.sleep(0.05)

    # 워커 생명주기 관리
    def start_polling(self, on_positions=None, on_states=None, on_lost=None):
        if not self._connected: return

        # UniqueConnection: 단일 슬롯에 대한 중복 연결 방지
        _UC = Qt.ConnectionType.UniqueConnection
        if on_positions: self.positions_updated.connect(on_positions, _UC)
        if on_states:    self.states_updated.connect(on_states,       _UC)
        if on_lost:      self.connection_lost.connect(on_lost,        _UC)

        if self._thread and self._thread.isRunning():
            return

        self._thread = QThread()
        self._worker = AcsWorker()
        self._worker.set_connection_params(*self._conn_info)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.setup)

        # 워커 -> 컨트롤러 (데이터 스트림)
        self._worker.positions_updated.connect(self._update_positions)
        self._worker.states_updated.connect(self._update_states)
        self._worker.positions_updated.connect(self.positions_updated.emit)
        self._worker.states_updated.connect(self.states_updated.emit)
        self._worker.connection_lost.connect(self.connection_lost.emit)

        # 컨트롤러 -> 워커 (명령 다운스트림)
        _QC = Qt.ConnectionType.QueuedConnection
        self._cmd_enable.connect(self._worker.set_enable,           _QC)
        self._cmd_enable_all.connect(self._worker.set_enable_all,   _QC)
        self._cmd_disable_all.connect(self._worker.set_disable_all, _QC)
        self._cmd_move_to.connect(self._worker.move_to,             _QC)
        self._cmd_stop_axis.connect(self._worker.stop_axis,         _QC)
        self._cmd_stop_all.connect(self._worker.stop_all,           _QC)

        self._thread.start()

    def stop_polling(self):
        if self._thread and self._thread.isRunning():
            if self._worker:
                # 시그널 연결 해제
                try:
                    self._cmd_enable.disconnect(self._worker.set_enable)
                    self._cmd_enable_all.disconnect(self._worker.set_enable_all)
                    self._cmd_disable_all.disconnect(self._worker.set_disable_all)
                    self._cmd_move_to.disconnect(self._worker.move_to)
                    self._cmd_stop_axis.disconnect(self._worker.stop_axis)
                    self._cmd_stop_all.disconnect(self._worker.stop_all)
                except Exception:
                    pass
                
                QMetaObject.invokeMethod(self._worker, "stop",
                                         Qt.ConnectionType.QueuedConnection)
            self._thread.quit()
            if not self._thread.wait(3000):
                log.warning("[ACS] Worker thread did not stop in time - forcing terminate")
                self._thread.terminate()
                self._thread.wait(1000)
        self._worker = None
        self._thread = None
