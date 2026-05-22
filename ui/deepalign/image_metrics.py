import time  # [임시 계측]

import numpy as np
from ui.deepalign import calc_functions
from ui.deepalign._perf_probe import perf_tick  # [임시 계측]

class ImageMetrics:
    """이미지 혹은 ROI 배열로부터 밝기 8종·대비 9종을 계산하고 캐싱하는 구조체."""

    def __init__(self, sample: np.ndarray, bg_2d: np.ndarray | None = None):
        # 2D 공간 구조를 유지한 채 float64로 보관 (Laplacian·패치·프로파일 연산 필요)
        self.sample_2d = sample.astype(np.float64)
        self.has_data = self.sample_2d.size > 0 and bool(np.any(np.isfinite(self.sample_2d)))
        # 링 BG 픽셀 (1D flat). None 이면 calc_function_12/13/14 가 자동 추정.
        self.bg_flat = bg_2d.ravel().astype(np.float64) if bg_2d is not None else None
        self._cache = {}

    def _get_or_compute(self, key: str, func_name: str) -> float:
        if not self.has_data:
            return 0.0
        if key not in self._cache:
            try:
                func = getattr(calc_functions, func_name)
                self._cache[key] = float(func(self.sample_2d))
            except Exception as e:
                import sys
                print(f"Error computing {func_name}: {e}", file=sys.stderr)
                self._cache[key] = 0.0
        return self._cache[key]

    def _get_or_compute_with_bg(self, key: str, func_name: str) -> float:
        """bg_flat 을 bg_arr 인자로 전달하는 전용 헬퍼 (opt12/13/14 전용)."""
        if not self.has_data:
            return 0.0
        if key not in self._cache:
            try:
                func = getattr(calc_functions, func_name)
                self._cache[key] = float(func(self.sample_2d, bg_arr=self.bg_flat))
            except Exception as e:
                import sys
                print(f"Error computing {func_name}: {e}", file=sys.stderr)
                self._cache[key] = 0.0
        return self._cache[key]

    # (key, func_name, uses_bg)
    _OPT_SPEC = [
        ("opt1",  "calc_function_1",  False), ("opt2",  "calc_function_2",  False),
        ("opt3",  "calc_function_3",  False), ("opt4",  "calc_function_4",  False),
        ("opt5",  "calc_function_5",  False), ("opt6",  "calc_function_6",  False),
        ("opt7",  "calc_function_7",  False), ("opt8",  "calc_function_8",  False),
        ("opt9",  "calc_function_9",  False), ("opt10", "calc_function_10", False),
        ("opt11", "calc_function_11", False),
        ("opt12", "calc_function_12", True),  ("opt13", "calc_function_13", True),
        ("opt14", "calc_function_14", True),
        ("opt15", "calc_function_15", False), ("opt16", "calc_function_16", False),
        ("opt17", "calc_function_17", False),
    ]

    def to_dict(self, profile: bool = False) -> dict:
        """UI (ProcStatsPlot 등) 에 넘겨주기 위한 딕셔너리 반환 (opt1~opt17).

        profile=True 이면 [임시 계측] 각 calc_function 소요시간을 perf_tick 으로 기록.
        """
        out = {}
        for key, func_name, uses_bg in self._OPT_SPEC:
            if profile:  # [임시 계측]
                _t0 = time.perf_counter()
            if uses_bg:
                out[key] = self._get_or_compute_with_bg(key, func_name)
            else:
                out[key] = self._get_or_compute(key, func_name)
            if profile:  # [임시 계측]
                perf_tick(f"metric.{key}", (time.perf_counter() - _t0) * 1000.0)
        return out
