"""
core/motor/picomotor.py
Newport Picomotor 8742 컨트롤러 래퍼.

pythonnet(clr)과 DeviceIOLib/CmdLib8742 DLL이 없는 환경에서도
import는 성공하며, connect() 시점에 RuntimeError를 올린다.
"""

from __future__ import annotations

import sys
import time
import threading
from typing import List, Optional

from core.logger import dev_logger
from PyQt6.QtCore import QObject, QThread, pyqtSignal

_PICO_OK = False
_PICO_IMPORT_ERROR: Optional[str] = None

DLL_PATH = r"D:\차세대설비기술\고호선"

try:
    import clr
    if DLL_PATH not in sys.path:
        sys.path.append(DLL_PATH)
    clr.AddReference("DeviceIOLib")
    clr.AddReference("CmdLib8742")
    from Newport.DeviceIOLib import DeviceIOLib
    from NewFocus.PicomotorApp import CmdLib8742
    _PICO_OK = True
except Exception as e:
    _PICO_IMPORT_ERROR = str(e)


def is_available() -> bool:
    return _PICO_OK


class _PollingWorker(QObject):
    """300ms 간격으로 4축 포지션을 폴링하고 연결 끊김을 감지한다."""
    positions_updated = pyqtSignal(list)   # [pos1, pos2, pos3, pos4] (None = 오류)
    connection_lost   = pyqtSignal()       # 5회 연속 전체 None

    def __init__(self, cmdlib, device_key, master_addr):
        super().__init__()
        self._cmdlib    = cmdlib
        self._device_key= device_key
        self._master    = master_addr
        self._running   = False

    def run(self):
        self._running = True
        fail_count = 0
        while self._running:
            positions: List[Optional[int]] = []
            for motor in range(1, 5):
                try:
                    ok, pos = self._cmdlib.GetPosition(
                        self._device_key, self._master, motor, 0)
                    positions.append(pos if ok else None)
                except Exception:
                    positions.append(None)

            if all(p is None for p in positions):
                fail_count += 1
                if fail_count >= 5:
                    self.connection_lost.emit()
                    break
            else:
                fail_count = 0

            self.positions_updated.emit(positions)
            QThread.msleep(300)

    def stop(self):
        self._running = False


class PicomotorController:
    """
    Newport Picomotor 8742 USB 컨트롤러.

    사용법:
        ctrl = PicomotorController()
        ctrl.connect()                              # USB 탐색 + 열기
        ctrl.move_relative(motor=1, steps=100)
        ctrl.zero(motor=2)
        ctrl.stop_all()
        ctrl.disconnect()

    폴링:
        ctrl.start_polling(on_positions, on_lost)  # 시그널 → 슬롯
        ctrl.stop_polling()
    """

    def __init__(self):
        self._deviceIO = None
        self._cmdlib   = None
        self._key      = None
        self._master   = None
        self._connected = False

        self._worker: Optional[_PollingWorker] = None
        self._thread: Optional[QThread]        = None

    # ── 연결 ─────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> str:
        """연결하고 모델 문자열을 반환한다."""
        if not _PICO_OK:
            raise RuntimeError(f"Picomotor DLL을 불러올 수 없습니다: {_PICO_IMPORT_ERROR}")

        self._deviceIO = DeviceIOLib()
        self._deviceIO.SetUSBProductID(0x4000)
        self._deviceIO.DiscoverDevices(1, 1000)

        count = self._deviceIO.GetDeviceCount()
        if count == 0:
            self._deviceIO.Shutdown()
            self._deviceIO = None
            raise RuntimeError("Picomotor 장치를 찾을 수 없습니다. USB 연결과 PicomotorApp 종료를 확인하세요")

        keys = self._deviceIO.GetDeviceKeys()
        self._key = keys[0]
        self._cmdlib = CmdLib8742(self._deviceIO)

        if not self._cmdlib.Open(self._key):
            raise RuntimeError("Picomotor 장치 열기 실패")

        self._master = self._cmdlib.GetMasterDeviceAddress(self._key)
        model = self._cmdlib.GetModelSerial(self._key, self._master)
        self._connected = True
        dev_logger.info(f"Picomotor Connected: {model}")
        return str(model)

    def disconnect(self) -> None:
        self.stop_polling()
        try:
            if self._cmdlib and self._key:
                self._cmdlib.Close(self._key)
            if self._deviceIO:
                self._deviceIO.Shutdown()
        except Exception:
            pass
        self._cmdlib    = None
        self._deviceIO  = None
        self._key       = None
        self._master    = None
        self._connected = False

    # ── 모터 제어 ─────────────────────────────────────────────────────

    def move_relative(self, motor: int, steps: int) -> bool:
        """상대 이동. 성공 여부 반환."""
        self._require_connected()
        return bool(self._cmdlib.RelativeMove(self._key, self._master, motor, steps))

    def zero(self, motor: int) -> None:
        """현재 위치를 0으로 설정."""
        self._require_connected()
        self._cmdlib.SetZeroPosition(self._key, self._master, motor)

    def get_position(self, motor: int) -> Optional[int]:
        """현재 위치 반환. 실패 시 None."""
        self._require_connected()
        try:
            ok, pos = self._cmdlib.GetPosition(self._key, self._master, motor, 0)
            return pos if ok else None
        except Exception:
            return None

    def get_all_positions(self) -> List[Optional[int]]:
        """4축 모두 위치 조회."""
        return [self.get_position(m) for m in range(1, 5)]

    def stop_all(self) -> None:
        """모든 축 즉시 정지."""
        self._require_connected()
        self._cmdlib.AbortMotion(self._key, self._master)

    def wait_motion_done(self, motor: int, timeout_ms: int = 10000,
                         poll_ms: int = 50, stable_n: int = 3) -> None:
        """위치 안정성 기반 정지 판정.

        poll_ms 간격으로 GetPosition 폴링 → 같은 값이 연속 stable_n회 관측되면
        정지로 간주. timeout_ms 만료 시 TimeoutError.
        8742 SDK에 명시적 motion-done 쿼리가 없어 위치 stability 로 대체.
        """
        self._require_connected()
        deadline = time.perf_counter() + max(0, int(timeout_ms)) / 1000.0
        stable = 0
        last: Optional[int] = None
        while True:
            try:
                ok, pos = self._cmdlib.GetPosition(self._key, self._master, int(motor), 0)
            except Exception as exc:
                raise RuntimeError(f"Picomotor M{motor} GetPosition 실패: {exc}") from exc
            if ok:
                if last is not None and pos == last:
                    stable += 1
                    if stable >= max(1, int(stable_n)):
                        return
                else:
                    stable = 1
                last = pos
            if time.perf_counter() >= deadline:
                raise TimeoutError(
                    f"Picomotor M{motor} move timeout after {int(timeout_ms)}ms"
                )
            time.sleep(max(0.001, int(poll_ms) / 1000.0))

    # ── 폴링 ─────────────────────────────────────────────────────────

    def start_polling(
        self,
        on_positions,   # Callable[[list], None]
        on_lost,        # Callable[[], None]
    ) -> None:
        """백그라운드 폴링 시작. 시그널 연결용 QObject 워커를 스레드에서 실행."""
        self._require_connected()
        self.stop_polling()

        self._thread = QThread()
        self._worker = _PollingWorker(self._cmdlib, self._key, self._master)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.positions_updated.connect(on_positions)
        self._worker.connection_lost.connect(on_lost)
        self._thread.start()

    def stop_polling(self) -> None:
        if self._worker:
            self._worker.stop()
        if self._thread:
            self._thread.quit()
            if not self._thread.wait(3000):
                self._thread.terminate()
        self._worker = None
        self._thread = None

    # ── 내부 ──────────────────────────────────────────────────────────

    def _require_connected(self):
        if not self._connected:
            raise RuntimeError("Picomotor가 연결되지 않았습니다")
