"""
core/motor/acs_stage.py
ACS SPiiPlus 6축 키네마틱 스테이지 컨트롤러.

- 모든 Write 명령(Enable, Move, Stop 등)은 단일 워커 스레드(AcsWorker)에서 순차적으로 처리됩니다.
- Read 명령(Position, State Polling)은 독립 상시 백그라운드 스레드(AcsPollingThread)에서 Lock을 잡고 주기적으로 수행됩니다.
- API 공유 시 상호배제를 보장하기 위해 _api_lock(threading.Lock)을 채용합니다.
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


class AcsPollingThread(QThread):
    """독립 상시 백그라운드 폴링(읽기) 스레드"""
    positions_updated = pyqtSignal(list)
    states_updated    = pyqtSignal(list)

    def __init__(self, api, api_lock, parent=None):
        super().__init__(parent)
        self._api = api
        self._api_lock = api_lock
        self._stopped = False
        self._is_polling = True
        self._interval_ms = 200

    def stop(self):
        self._stopped = True

    def set_polling(self, enable: bool):
        self._is_polling = enable

    def run(self):
        log.info("[ACS Polling Thread] Polling run loop started")
        while not self._stopped:
            if self._is_polling and self._api is not None:
                try:
                    positions = []
                    states = []
                    
                    with self._api_lock:
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
                    log.debug(f"[ACS Polling Thread] Poll error: {e}")
            self.msleep(self._interval_ms)
        log.info("[ACS Polling Thread] Polling run loop stopped")


class AcsWorker(QObject):
    """ACS 하드웨어 통신 담당 워커 (단일 스레드 상주 - 오직 쓰기 명령 전송만 처리)"""
    positions_updated = pyqtSignal(list)
    states_updated    = pyqtSignal(list)
    connection_lost   = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._api = None
        self._api_lock = None

    def set_api(self, api):
        self._api = api

    def set_api_lock(self, api_lock):
        self._api_lock = api_lock

    @pyqtSlot()
    def setup(self):
        # 1. 연결은 메인 스레드에서 완료되었으므로, 초기화 및 FaultClear만 수행!
        try:
            log.info(f"[ACS Worker] setup() start @ thread={int(self.thread().currentThreadId())}")
            if self._api is None:
                raise ValueError("API instance is not set")
            
            with self._api_lock:
                # [Safety] 연결 직후 에러 클리어
                for i in range(6):
                    try:
                        self._api.FaultClear(_axis_enum(i))
                        log.debug(f"[ACS Worker] FaultClear Axis {i} OK")
                    except Exception as fe:
                        log.warning(f"[ACS Worker] FaultClear Axis {i} failed: {fe}")
                log.info("[ACS Worker] Initial FaultClear all axes done")

        except Exception as e:
            log.error(f"[ACS Worker] Setup failed: {e}")
            self.connection_lost.emit()
            return

        # 2. 동기식 초기 1회 폴링 데이터 강제 취득 및 전송
        try:
            self._emit_current_states_sync()
            log.info("[ACS Worker] Connected setup successfully completed")
        except Exception as e:
            log.error(f"[ACS Worker] Initial sync poll failed: {e}")
            self.connection_lost.emit()
            return

    def _emit_current_states_sync(self):
        """현재 시점의 positions와 states를 락을 잡고 즉각 수집해 emit (캐시 업데이트용)"""
        if self._api is None: return
        positions = []
        states = []
        with self._api_lock:
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

    def _wait_axis_stopped(self, ax, timeout: float = 2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with self._api_lock:
                    mst = int(self._api.GetMotorState(ax))
                if not (mst & _MST_ANY_MOTION):
                    return
            except:
                return
            time.sleep(0.02)
        log.warning(f"[ACS Worker] Axis stop wait timed out ({timeout}s)")

    @pyqtSlot(int, bool)
    def set_enable(self, axis: int, enable: bool):
        if self._api is None: return
        try:
            ax = _axis_enum(axis)
            with self._api_lock:
                mstate = int(self._api.GetMotorState(ax))
            is_enabled = bool(mstate & _MST_ENABLE)

            if is_enabled == enable:
                return

            if enable:
                # MOVE/ACC 중이면 Enable 불가하므로 사전 정지 대기(최대 3초)
                deadline = time.time() + 3.0
                while time.time() < deadline:
                    with self._api_lock:
                        mst = int(self._api.GetMotorState(ax))
                    if not (mst & _MST_ANY_MOTION):
                        break
                    time.sleep(0.05)
                else:
                    log.warning(f"[ACS Worker] Axis {axis}: still in motion after 3s, attempting Enable anyway")

                with self._api_lock:
                    try:
                        self._api.FaultClear(ax)
                    except:
                        pass
                    self._api.Enable(ax)
            else:
                with self._api_lock:
                    self._api.Disable(ax)

        except Exception as e:
            log.error(f"[ACS Worker] Enable error (Axis {axis}): {e}")

    @pyqtSlot()
    def set_enable_all(self):
        try:
            self._do_enable_all()
        finally:
            # 일괄 ON 직후, 즉각 강제 상태 갱신을 통해 UI 및 wait_for_enabled_all 즉시 완료 보장
            self._emit_current_states_sync()

    def _do_enable_all(self):
        if self._api is None: return

        # 0. 먼저 모든 축에 Halt 명령 — ACS 내부 모션 버퍼까지 강제 클리어
        log.info("[ACS Worker] Halting all axes before EnableM...")
        with self._api_lock:
            for i in range(6):
                try:
                    self._api.Halt(_axis_enum(i))
                except Exception as he:
                    log.debug(f"[ACS Worker] Halt Axis {i} (ignored): {he}")

        # Halt 후 충분한 정착 대기 (ACS 내부 state machine 정리 시간)
        time.sleep(0.5)

        # 1. MOVE/ACC 비트가 완전히 꺼질 때까지 추가 대기 (최대 3초)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            all_stopped = True
            with self._api_lock:
                for i in range(6):
                    mst = int(self._api.GetMotorState(_axis_enum(i)))
                    if mst & _MST_ANY_MOTION:
                        all_stopped = False
                        break
            if all_stopped:
                break
            time.sleep(0.05)
        else:
            log.warning("[ACS Worker] Axes did not stop within 3s after Halt — proceeding anyway")

        # 2. 6축 FaultClear 선 복구 (E-Stop/Limit 트립 리셋)
        with self._api_lock:
            for i in range(6):
                try:
                    self._api.FaultClear(_axis_enum(i))
                except Exception as fe:
                    log.debug(f"[ACS Worker] FaultClear Axis {i} (ignored): {fe}")

        # FaultClear 후 안정화 대기
        time.sleep(0.2)

        # 3. 6개 축 + 종결자(NONE) 1개 = 총 7개 크기 배열 생성
        raw_indices = [0, 1, 4, 5, 8, 9]
        axis_array = System.Array.CreateInstance(_AxisEnum, len(raw_indices) + 1)
        
        # 4. 실제 축 데이터 채우기
        for i, val in enumerate(raw_indices):
            ax_obj = Enum.ToObject(_AxisEnum, val)
            axis_array.SetValue(ax_obj, i)
            
        # 5. 마지막 칸에 ACSC_NONE (-1) 주입
        none_obj = Enum.ToObject(_AxisEnum, -1)
        axis_array.SetValue(none_obj, len(raw_indices))

        log.info("[ACS Worker] Attempting EnableM with type-safe array")
        
        with self._api_lock:
            self._api.EnableM(axis_array)

    @pyqtSlot()
    def set_disable_all(self):
        try:
            for i in range(6):
                self.set_enable(i, False)
        finally:
            self._emit_current_states_sync()

    @pyqtSlot(int, float)
    def move_to(self, axis: int, target: float):
        if self._api is None: return
        try:
            ax = _axis_enum(axis)
            flags = _MotionFlags(0) if _MotionFlags is not None else 0
            with self._api_lock:
                self._api.ToPoint(flags, ax, float(target))
        except Exception as e:
            log.error(f"[ACS Worker] Move error: {e}")

    @pyqtSlot(int)
    def stop_axis(self, axis: int):
        if self._api is None: return
        try:
            ax = _axis_enum(axis)
            with self._api_lock:
                self._api.Halt(ax)
        except:
            pass

    @pyqtSlot()
    def stop_all(self):
        for i in range(6): self.stop_axis(i)

    @pyqtSlot()
    def stop(self):
        """워커 정지용 더미 슬롯 (API 정리는 컨트롤러가 담당)"""
        self._api = None


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
        self._poll_thread: Optional[AcsPollingThread] = None
        self._api_lock = threading.Lock()
        self.dry_run = False
        
        self._axes      = list(range(6))
        
        # 캐시된 하드웨어 상태
        self._last_positions = [0.0] * 6
        self._last_states    = [{"enabled": False, "moving": False, "in_pos": False} for _ in range(6)]
        
        # 동기식 초기 연결 대기용 플래그
        self._first_poll_done = False
        self._connection_failed = False
        
        # 소프트 리밋 초기화
        from core.motor.kinematic_calc import DEFAULT_PLUS_LIMITS, DEFAULT_MINUS_LIMITS
        self.plus_limits = DEFAULT_PLUS_LIMITS.copy()
        self.minus_limits = DEFAULT_MINUS_LIMITS.copy()

    # 연결 제어 섹션
    def connect(self, ip: str, port: int = DEFAULT_PORT):
        if not _ACS_OK: raise RuntimeError(f"DLL Load Failed: {_ACS_IMPORT_ERROR}")
        self.stop_polling() # 기존 세션 및 API 리소스 강제 종료
        
        try:
            # TCP 예비 접속 검증 (Preflight) 안정성 개선
            try:
                with socket.create_connection((ip, int(port)), timeout=3.0):
                    pass
                time.sleep(0.5) # TIME_WAIT 우회
            except Exception as sock_err:
                raise RuntimeError(f"TCP preflight failed for {ip}:{port}: {sock_err}")

            api = _Api()
            api.OpenCommEthernetTCP(ip, port)
            self._api = api
            self._connected = True
            self._simulator = False
            self._conn_info = ("ethernet", ip, port)
            dev_logger.info(f"ACS Connected via Ethernet to {ip}:{port}")
        except Exception as e:
            self._connected = False
            self._api = None
            raise RuntimeError(f"ACS Connection failed: {e}")

    def connect_simulator(self):
        if not _ACS_OK: raise RuntimeError(f"DLL Load Failed: {_ACS_IMPORT_ERROR}")
        self.stop_polling()
        
        try:
            api = _Api()
            api.OpenCommSimulator()
            self._api = api
            self._connected = True
            self._simulator = True
            self._conn_info = ("simulator", None, None)
            dev_logger.info("ACS Connected to Simulator")
        except Exception as e:
            self._connected = False
            self._api = None
            raise RuntimeError(f"ACS Simulator Connection failed: {e}")

    def disconnect(self):
        self.stop_polling()
        self._connected = False

    @property
    def is_connected(self) -> bool: return self._connected

    @property
    def is_simulator(self) -> bool: return self._simulator

    # 제어 명령 (시그널을 통한 워커 슬롯 호출, QueuedConnection)
    def enable_all(self):
        if self._poll_thread is not None:
            self._poll_thread.set_polling(False)
        self._cmd_enable_all.emit()

    def disable_all(self):
        if self._poll_thread is not None:
            self._poll_thread.set_polling(False)
        self._cmd_disable_all.emit()

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
        self._first_poll_done = True

    def _update_states(self, states):
        self._last_states = states

    def _on_worker_connection_lost(self):
        self._connection_failed = True

    def get_position(self, axis: int) -> float:
        if 0 <= axis < 6:
            return self._last_positions[axis]
        return 0.0

    def get_positions(self) -> list[float]:
        return list(self._last_positions)

    def get_axis_states(self) -> list[dict]:
        return list(self._last_states)

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
        app = QApplication.instance()
        main_thread = app.thread() if app is not None else None
        try:
            while (time.time() - start) * 1000 < timeout_ms:
                if all(self.is_enabled(i) for i in range(6)):
                    return True
                # 메인 스레드에서 호출될 경우에만 UI 이벤트 처리
                if main_thread is not None and QThread.currentThread() == main_thread:
                    app.processEvents()
                time.sleep(0.05)
            return False
        finally:
            # 대기 완료 후, 상시 폴링 스레드의 통신 동작을 반드시 복원
            if self._poll_thread is not None:
                self._poll_thread.set_polling(True)

    def wait_in_position_all(self, timeout_ms: int = 30000):
        # 캐시 갱신 대기 (polling 주기 200ms + 여유)
        time.sleep(0.25)

        start = time.time()
        app = QApplication.instance()
        main_thread = app.thread() if app is not None else None
        while (time.time() - start) * 1000 < timeout_ms:
            if not any(self.is_moving(i) for i in range(6)):
                return
            if main_thread is not None and QThread.currentThread() == main_thread:
                app.processEvents()
            time.sleep(0.05)
        raise TimeoutError(f"ACS wait_in_position_all timeout after {int(timeout_ms)}ms")

    # 워커 생명주기 관리
    def start_polling(self, on_positions=None, on_states=None, on_lost=None):
        if not self._connected or self._api is None: return

        # UniqueConnection: 단일 슬롯에 대한 중복 연결 방지
        _UC = Qt.ConnectionType.UniqueConnection
        if on_positions: self.positions_updated.connect(on_positions, _UC)
        if on_states:    self.states_updated.connect(on_states,       _UC)
        if on_lost:      self.connection_lost.connect(on_lost,        _UC)

        # 1. 명령 워커 스레드 기동
        if not (self._thread and self._thread.isRunning()):
            self._first_poll_done = False
            self._connection_failed = False

            self._thread = QThread()
            self._worker = AcsWorker()
            self._worker.set_api(self._api)
            self._worker.set_api_lock(self._api_lock)
            self._worker.moveToThread(self._thread)
            
            self._thread.finished.connect(self._worker.deleteLater)
            self._thread.started.connect(self._worker.setup)

            # 워커 -> 컨트롤러 (데이터 스트림)
            self._worker.positions_updated.connect(self._update_positions)
            self._worker.states_updated.connect(self._update_states)
            self._worker.positions_updated.connect(self.positions_updated.emit)
            self._worker.states_updated.connect(self.states_updated.emit)
            self._worker.connection_lost.connect(self._on_worker_connection_lost)
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

            # GUI 스레드 블로킹 대기 (첫 폴링 완료 또는 연결 실패 시까지 최대 4.0초)
            from PyQt6.QtWidgets import QApplication
            import time
            start_t = time.time()
            main_thread = QApplication.instance().thread() if QApplication.instance() else None
            while not self._first_poll_done and not self._connection_failed and (time.time() - start_t) < 4.0:
                if main_thread and QThread.currentThread() == main_thread:
                    QApplication.processEvents()
                time.sleep(0.01)

            if self._connection_failed:
                self.stop_polling()
                raise RuntimeError("ACS hardware connection failed (setup error)")
            if not self._first_poll_done:
                self.stop_polling()
                raise RuntimeError("ACS hardware connection timed out waiting for first poll")

        # 2. 상시 백그라운드 폴링 스레드 기동
        if self._poll_thread is None:
            self._poll_thread = AcsPollingThread(self._api, self._api_lock)
            self._poll_thread.positions_updated.connect(self._update_positions)
            self._poll_thread.states_updated.connect(self._update_states)
            self._poll_thread.positions_updated.connect(self.positions_updated.emit)
            self._poll_thread.states_updated.connect(self.states_updated.emit)
            self._poll_thread.start()
            dev_logger.info("[ACS] Polling thread successfully started")

    def stop_polling(self):
        # 1. 상시 폴링 스레드 정지
        if self._poll_thread is not None:
            self._poll_thread.stop()
            self._poll_thread.wait(2000)
            self._poll_thread = None
            dev_logger.info("[ACS] Polling thread stopped")

        # 2. 명령 스레드 정지
        if self._thread and self._thread.isRunning():
            dev_logger.info("[ACS] Stopping command thread...")
            if self._worker:
                # 명령 다운스트림 신호 해제
                for sig in (self._cmd_enable, self._cmd_enable_all,
                            self._cmd_disable_all, self._cmd_move_to,
                            self._cmd_stop_axis, self._cmd_stop_all):
                    try: sig.disconnect()
                    except Exception: pass

                # 워커 → 컨트롤러 신호 해제
                for sig_name in ("positions_updated", "states_updated", "connection_lost"):
                    sig = getattr(self._worker, sig_name, None)
                    if sig is not None:
                        try: sig.disconnect()
                        except Exception: pass

                # 워커의 _api 참조를 끊어 진행 중인 .NET 호출이 끝나면 즉시 종료
                self._worker._api = None

            # 이벤트 루프 종료 요청 후 최대 4초 대기
            self._thread.quit()
            if not self._thread.wait(4000):
                dev_logger.warning("[ACS] Command thread quit timeout 4s - forcing terminate")
                self._thread.terminate()   # 최후 수단: 강제 종료
                self._thread.wait(1000)    # terminate 완료 대기
            else:
                dev_logger.info("[ACS] Command thread stopped safely")

        self._worker = None
        self._thread = None

        # 3. API 리소스 정리 (메인 스레드에서 CloseComm 수행)
        if self._api is not None:
            with self._api_lock:
                try:
                    self._api.CloseComm()
                except Exception:
                    pass
                self._api = None


# ──────────────────────────────────────────────────────────────────────────────
# KinematicMoveWorker — canonical 7-step servo ON → move → servo OFF 시퀀스
# ──────────────────────────────────────────────────────────────────────────────

class KinematicMoveWorker(QThread):
    """7-step servo ON → move → servo OFF 시퀀스를 GUI 스레드 밖에서 실행."""

    log      = pyqtSignal(str)
    finished = pyqtSignal()
    error    = pyqtSignal(str)

    _SERVO_ON_MS   = 1000   # 서보 ON 확인 대기
    _SETTLE_MS     = 500    # In-Position 후 정착 대기
    _INPOS_TIMEOUT = 30000  # WaitMotionEnd 타임아웃 (ms)

    def __init__(self, ctrl, targets, limits_plus, limits_minus,
                 settle_ms: int = 500, dry: bool = False):
        super().__init__()
        self._ctrl = ctrl
        self._targets = targets
        self._limits_plus = limits_plus
        self._limits_minus = limits_minus
        self._settle_ms = settle_ms
        self._dry = dry
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            if self._dry:
                self.log.emit("[DRY RUN] 서보 ON → 이동 → 서보 OFF 시뮬레이션")
                self.msleep(300)
                if self._stop_requested: return
                self.log.emit("[DRY RUN] KINEMATIC MOVE 완료")
                self.finished.emit()
                return

            if self._stop_requested: return
            self.log.emit("[ACS] ① Servo ON")
            self._ctrl.enable_all()

            if self._stop_requested: return
            self.log.emit("[ACS] ② Servo ON 상태 확인 대기...")
            if not self._ctrl.wait_for_enabled_all(timeout_ms=self._SERVO_ON_MS):
                raise RuntimeError("Servo ON 확인 실패 (Timeout)")
            self.log.emit("[ACS] ② Servo ON 확인 완료")

            if self._stop_requested: return
            motor_names = ["Y1", "Z1", "X1", "Z2", "Y2", "Z3"]
            self.log.emit("[ACS] ③ Move 명령 전송 (전축 동시)")
            for i, (name, target, p_lim, n_lim) in enumerate(zip(motor_names, self._targets, self._limits_plus, self._limits_minus)):
                if self._stop_requested: return
                self.log.emit(f"   [ACS] [{name}] POS: {target:+.4f} [PLIM: {p_lim:+.4f}, NLIM: {n_lim:+.4f}]")
                self._ctrl.move_to(i, float(target), wait=False)

            if self._stop_requested: return
            self.log.emit("[ACS] ④ In-Position 완료 대기")
            self._ctrl.wait_in_position_all(timeout_ms=self._INPOS_TIMEOUT)

            self.log.emit(f"[ACS] ⑤ Settle Time 대기 ({self._settle_ms} ms)")
            self.msleep(self._settle_ms)

            self.log.emit("[ACS] ⑥ Servo OFF (락 걸기)")
            self._ctrl.disable_all()

            self.log.emit("[KINEMATICS] ⑦ KINEMATIC MOVE 완료")
            self.finished.emit()

        except Exception as e:
            self.error.emit(str(e))
