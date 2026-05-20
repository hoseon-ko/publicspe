"""헤드리스 카메라 스냅 검증 — Simulated 어댑터로 session_hub 풀스택 테스트."""
from __future__ import annotations

import os
import sys

# Windows COM mode (main.py와 동일)
if sys.platform == 'win32':
    os.environ["QT_COM_INIT"] = "0"
    if not hasattr(sys, 'coinit_flags'):
        sys.coinit_flags = 2

import numpy as np
from PyQt6.QtCore import QCoreApplication

from core.session.device_session_hub import DeviceSessionHub
from core.session.ownership import OWNER_DEEPALIGN
from core.hal.adapters import SimulatedCameraAdapter


def main() -> int:
    app = QCoreApplication(sys.argv)

    hub = DeviceSessionHub()
    hub.register_camera_hal("simulated", SimulatedCameraAdapter)
    hub.select_camera_vendor("simulated")

    # 시뮬레이션 카메라 스캔 & 연결
    devices = hub.scan_cameras()
    print(f"[1] scan_cameras → {len(devices)}개 발견")
    assert devices, "Simulated 카메라 디바이스가 0개"

    dev = devices[0]
    device_id = getattr(dev, "device_id", "")
    print(f"[2] connect_camera({device_id})")
    hub.connect_camera(str(device_id))

    # capabilities 확인
    caps = hub.camera_get_capabilities()
    print(f"[3] capabilities: temp={getattr(caps, 'has_temperature', None)}, "
          f"adc={getattr(caps, 'has_adc', None)}")

    # 단일 프레임 스냅
    frame = hub.snap(OWNER_DEEPALIGN)
    print(f"[4] snap() → shape={frame.shape}, dtype={frame.dtype}, "
          f"min={frame.min()}, max={frame.max()}, mean={frame.mean():.2f}")

    # 검증
    assert isinstance(frame, np.ndarray), "snap()이 ndarray를 반환하지 않음"
    assert frame.ndim == 2, f"2D 이미지여야 함 (got ndim={frame.ndim})"
    assert frame.size > 0, "빈 프레임"
    assert frame.max() > frame.min(), "균일 프레임 — 시뮬레이션 패턴 비어있음"

    # 두 번째 스냅으로 반복성 확인
    frame2 = hub.snap(OWNER_DEEPALIGN)
    print(f"[5] snap() #2 → shape={frame2.shape}, mean={frame2.mean():.2f}")

    print("\n[PASS] 카메라 스냅 풀스택 정상 동작")
    return 0


if __name__ == "__main__":
    sys.exit(main())
