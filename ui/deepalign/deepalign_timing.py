"""DeepAlign 시간 계산 보조 파일.

이 파일은 acquire 흐름에서 사용하는 작은 순수 함수를 모아둡니다.
주요 역할은 다음과 같습니다.
- 프레임별 elapsed time clamp
- 프레임 진행률과 전체 진행률을 하나의 ratio로 계산
- remain/ETA 시간 문자열 포맷
"""

from __future__ import annotations


def clamp_frame_elapsed(now_mono: float, frame_started_at: float, expected_s: float) -> float:
    expected = max(0.001, float(expected_s))
    elapsed = max(0.0, float(now_mono) - float(frame_started_at))
    return min(elapsed, expected)


def overall_progress_ratio(completed: int, total: int, in_frame_ratio: float) -> float:
    safe_total = max(1, int(total))
    safe_completed = max(0, min(int(completed), safe_total))
    safe_in_frame = max(0.0, min(1.0, float(in_frame_ratio)))
    return (safe_completed + safe_in_frame) / safe_total


def format_hms(seconds: float) -> str:
    s = max(0, int(seconds))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"