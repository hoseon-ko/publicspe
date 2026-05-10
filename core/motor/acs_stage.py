"""
core/motor/acs_stage.py
ACS SPiiPlus 6축 키네마틱 스테이지 컨트롤러.

DLL: 프로젝트 루트/ACS.SPiiPlusNET.dll
통신: Ethernet TCP (기본 포트 700) 또는 시뮬레이터
축 매핑 (키네마틱 calPos 순서):
  idx 0 → ACSC_AXIS_1 → Y1
  idx 1 → ACSC_AXIS_2 → Z1
  idx 2 → ACSC_AXIS_3 → X1
  idx 3 → ACSC_AXIS_4 → Z2
  idx 4 → ACSC_AXIS_5 → Y2
  idx 5 → ACSC_AXIS_6 → Z3
"""

from __future__ import annotations

import sys
import os
import builtins
import logging
from pathlib import Path
from typing import Optional
import threading

from core.logger import dev_logger
from PyQt6.QtCore import QObject, QThread, pyqtSignal

log = logging.getLogger(__name__)

# DLL은 프로젝트 루트에 위치 (core/motor → core → project root)
DLL_PATH = str(Path(__file__).resolve().parent.parent.parent)

_ACS_OK = False
_ACS_IMPORT_ERROR: Optional[str] = None
_Api = None
_AxisEnum = None

_MotionFlags = None  # ACS.SPiiPlusNET.MotionFlags — 로드되면 교체

try:
    import clr
    if DLL_PATH not in sys.path:
        sys.path.append(DLL_PATH)
    os.add_dll_directory(DLL_PATH)
    clr.AddReference("ACS.SPiiPlusNET")
    from ACS.SPiiPlusNET import Api as _Api, Axis as _AxisEnum
    try:
        from ACS.SPiiPlusNET import MotionFlags as _MotionFlags
    except Exception:
        pass  # SDK 버전에 따라 없을 수 있음 — int 0 으로 fallback
    _ACS_OK = True
except Exception as e:
    _ACS_IMPORT_ERROR = str(e)

AXIS_LABELS = ["Y1", "Z1", "X1", "Z2", "Y2", "Z3"]
DEFAULT_PORT = 700

# GetMotorState() 결과의 LSB가 ACSC_MST_ENABLE(모터 활성화) 비트.
# ACS SDK 헤더의 ACSC_MST_ENABLE = 0x00000001 정의를 그대로 사용.
# GetMotorState() 결과 비트 정의
_MST_ENABLE = 0x01  # 모터 활성화 (Servo ON)
_MST_INPOS  = 0x10  # In-Position (이동 완료)


def is_available() -> bool:
    return _ACS_OK


def _axis_enum(idx: int):
    """0-based 인덱스를 ACS .NET Axis enum 으로 변환.
    SDK가 미로딩 상태(테스트 환경)인 경우 정수 그대로 반환 — Api 호출 시 어차피 실패함.
    """
    names = [
        "ACSC_AXIS_1", "ACSC_AXIS_2", "ACSC_AXIS_3",
        "ACSC_AXIS_4", "ACSC_AXIS_5", "ACSC_AXIS_6",
    ]
    if _AxisEnum is not None and 0 <= idx < len(names):
        return getattr(_AxisEnum, names[idx])
    return idx


class _PollingWorker(QObject):
    """300 ms 간격으로 6축 위치 + 모터 상태를 폴링."""

    positions_updated = pyqtSignal(list)  # list[float] × 6
    states_updated    = pyqtSignal(list)  # list[bool]  × 6  (True = enabled)
    connection_lost   = pyqtSignal()

    def __init__(self, api):
        super().__init__()
        self._api = api
        self._running = False

    def run(self):
        self._running = True
        fail = 0
        while self._running:
            positions: list[float] = []
            states: list[bool] = []
            try:
                for i in range(6):
                    ax = _axis_enum(i)
                    positions.append(float(self._api.GetFPosition(ax)))
                    
                    # 상세 상태 비트 체크
                    mstate = int(self._api.GetMotorState(ax))
                    states.append({
                        "enabled": bool(mstate & _MST_ENABLE),
                        "in_pos":  bool(mstate & _MST_INPOS)
                    })
                fail = 0
                self.positions_updated.emit(positions)
                self.states_updated.emit(states)
            except Exception as e:
                fail += 1
                log.debug(f"[ACS] poll error ({fail}): {e}")
                if fail >= 5:
                    self.connection_lost.emit()
                    break
            QThread.msleep(300)

    def stop(self):
        self._running = False


class AcsStageController:
    """
    ACS SPiiPlus 6축 키네마틱 스테이지 컨트롤러.

    사용법:
        ctrl = AcsStageController()
        ctrl.connect("10.0.0.100")          # Ethernet TCP
        ctrl.connect_simulator()            # 또는 시뮬레이터
        ctrl.enable_all()
        ctrl.move_to(0, 10.5)              # Y1 → 10.5 mm (비동기)
        ctrl.move_to(0, 10.5, wait=True)   # 완료까지 블로킹
        ctrl.stop_all()
        ctrl.disconnect()

    폴링:
        ctrl.start_polling(on_positions, on_states, on_lost)
        ctrl.stop_polling()
    """

    def __init__(self):
        self._api = None
        self._connected = False
        self._simulator = False

        self._worker: Optional[_PollingWorker] = None
        self._thread: Optional[QThread] = None
        self._cmd_lock = threading.Lock()

        self.dry_run = False
        
        # [Phase 6] ACS 모터 소프트 리밋 강제 적용
        try:
            from core.motor.kinematic_calc import DEFAULT_PLUS_LIMITS, DEFAULT_MINUS_LIMITS
            self.plus_limits = DEFAULT_PLUS_LIMITS.copy()
            self.minus_limits = DEFAULT_MINUS_LIMITS.copy()
        except Exception:
            self.plus_limits = [9999.0] * 6
            self.minus_limits = [-9999.0] * 6

    # ── 속성 ─────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_simulator(self) -> bool:
        return self._simulator

    # ── 연결 ─────────────────────────────────────────────────────────

    def connect(self, ip: str, port: int = DEFAULT_PORT) -> None:
        if not _ACS_OK:
            raise RuntimeError(f"ACS DLL 로드 실패: {_ACS_IMPORT_ERROR}")
        try:
            self._api = _Api()
            self._api.OpenCommEthernetTCP(ip, port)
            self._connected = True
            self._simulator = False
            dev_logger.info(f"ACS Connected: {ip}:{port}")
        except Exception as e:
            dev_logger.error(f"ACS Connection Failed: {e}")
            raise ConnectionError(f"ACS 연결 실패: {e}")

    def connect_simulator(self) -> None:
        if not _ACS_OK:
            raise RuntimeError(f"ACS DLL 로드 실패: {_ACS_IMPORT_ERROR}")
        try:
            self._api = _Api()
            self._api.OpenCommSimulator()
            self._connected = True
            self._simulator = True
            log.info("[ACS] Simulator connected")
        except Exception as e:
            self._api = None
            self._connected = False
            raise ConnectionError(f"[ACS] Simulator failed: {e}")

    def disconnect(self):
        self.stop_polling()
        if self._api and self._connected:
            try:
                self._api.CloseComm()
            except Exception:
                pass
        self._api = None
        self._connected = False
        log.info("[ACS] Disconnected")

    # ── 위치 조회 ─────────────────────────────────────────────────────

    def get_position(self, axis: int) -> float:
        """0-based axis index → 현재 피드백 위치 (mm)."""
        self._require_connected()
        return float(self._api.GetFPosition(_axis_enum(axis)))

    def get_all_positions(self) -> list[float]:
        return [self.get_position(i) for i in range(6)]

    # ── 이동 ─────────────────────────────────────────────────────────

    def move_to(self, axis: int, target_mm: float, wait: bool = False) -> None:
        """절대 이동. wait=True 면 완료까지 블로킹 (최대 30초)."""
        self._require_connected()
        
        if target_mm > self.plus_limits[axis] or target_mm < self.minus_limits[axis]:
            raise ValueError(f"Target for Axis{axis} is out of bounds: {target_mm:.4f}")

        if not self._cmd_lock.acquire(blocking=False):
            raise RuntimeError("Motion already in progress. Command ignored.")

        try:
            if self.dry_run:
                log.info(f"[ACS DRY-RUN] Axis{axis}({AXIS_LABELS[axis]}) → {target_mm:.4f} mm")
                return
                
            ax = _axis_enum(axis)
            # pythonnet 오버로드 해석:
            # ToPoint(MotionFlags, Axis, double) 또는 ToPoint(int, Axis, double) 중
            # SDK 버전에 따라 첫 인자 타입이 다름.
            # _MotionFlags 로드 성공 시 enum 타입으로 전달, 아니면 int 그대로.
            flags = _MotionFlags(0) if _MotionFlags is not None else int(0)
            target_double = builtins.float(target_mm)  # numpy scalar → 확실한 Python float
            log.debug(
                f"[ACS] ToPoint args: flags={flags!r}({type(flags).__name__}), "
                f"ax={ax!r}({type(ax).__name__}), target={target_double}({type(target_double).__name__})"
            )
            self._api.ToPoint(flags, ax, target_double)
            if wait:
                self._api.WaitMotionEnd(ax, 30000)
                log.info(f"[ACS] Move_to {target_mm:.2f} 완료")
        except Exception as e:
            raise RuntimeError(f"ACS move_to failed: {e}")
        finally:
            self._cmd_lock.release()

    def move_by(self, axis: int, delta_mm: float, wait: bool = False) -> None:
        """상대 이동."""
        self._require_connected()
        if self.dry_run:
            log.info(f"[ACS DRY-RUN] Axis{axis}({AXIS_LABELS[axis]}) Δ{delta_mm:+.4f} mm")
            return
            
        current = self.get_position(axis)
        self.move_to(axis, current + delta_mm, wait=wait)

    # ── Enable / Disable ─────────────────────────────────────────────

    def enable_motor(self, axis: int):
        self._require_connected()
        self._api.Enable(_axis_enum(axis))

    def disable_motor(self, axis: int):
        self._require_connected()
        self._api.Disable(_axis_enum(axis))

    def enable_all(self):
        for i in range(6):
            try:
                self.enable_motor(i)
            except Exception as e:
                log.warning(f"[ACS] enable axis{i}: {e}")

    def disable_all(self):
        for i in range(6):
            try:
                self.disable_motor(i)
            except Exception as e:
                log.warning(f"[ACS] disable axis{i}: {e}")

    def wait_in_position_all(self, timeout_ms: int = 30000) -> None:
        """모든 축의 이동 완료(In-Position)를 순차 대기."""
        self._require_connected()
        for i in range(6):
            self._api.WaitMotionEnd(_axis_enum(i), timeout_ms)

    # ── 정지 ─────────────────────────────────────────────────────────

    def halt(self, axis: int):
        self._require_connected()
        self._api.Halt(_axis_enum(axis))

    def stop_all(self):
        if not self._connected or self._api is None:
            return
        for i in range(6):
            try:
                self._api.Halt(_axis_enum(i))
            except Exception as e:
                log.warning(f"[ACS] halt axis{i}: {e}")

    # ── 폴링 ─────────────────────────────────────────────────────────

    def start_polling(self, on_positions, on_states, on_lost) -> None:
        self._require_connected()
        self.stop_polling()
        self._thread = QThread()
        self._worker = _PollingWorker(self._api)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.positions_updated.connect(on_positions)
        self._worker.states_updated.connect(on_states)
        self._worker.connection_lost.connect(on_lost)
        self._thread.start()

    def stop_polling(self):
        if self._worker:
            self._worker.stop()
        if self._thread:
            self._thread.quit()
            if not self._thread.wait(3000):
                self._thread.terminate()
        self._worker = None
        self._thread = None

    # ── 내부 ─────────────────────────────────────────────────────────

    def _require_connected(self):
        if not self._connected or self._api is None:
            raise RuntimeError("ACS 스테이지가 연결되지 않았습니다")
