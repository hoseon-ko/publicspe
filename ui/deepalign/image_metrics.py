import numpy as np
from ui.deepalign import calc_functions

class ImageMetrics:
    """이미지 혹은 ROI 배열로부터 다양한 통계치를 계산하고 캐싱하는 구조체."""
    
    def __init__(self, sample: np.ndarray):
        self.sample = sample
        self.sample_f = sample.astype(np.float32)
        # NaN 값 제외한 유효 샘플만 추출
        self.valid_sample = self.sample_f[~np.isnan(self.sample_f)]
        self.has_data = self.valid_sample.size > 0
        self._cache = {}

    def _get_or_compute(self, key: str, func_name: str):
        if not self.has_data:
            return 0.0
        if key not in self._cache:
            try:
                # calc_functions 모듈에서 동적으로 함수를 가져와 호출
                func = getattr(calc_functions, func_name)
                self._cache[key] = float(func(self.valid_sample))
            except Exception as e:
                import sys
                print(f"Error computing {func_name}: {e}", file=sys.stderr)
                self._cache[key] = 0.0
        return self._cache[key]

    def to_dict(self) -> dict:
        """UI (ProcStatsPlot 등) 에 넘겨주기 위한 딕셔너리 반환"""
        return {
            "opt1": self._get_or_compute("opt1", "calc_function_1"),
            "opt2": self._get_or_compute("opt2", "calc_function_2"),
            "opt3": self._get_or_compute("opt3", "calc_function_3"),
            "opt4": self._get_or_compute("opt4", "calc_function_4"),
            "opt5": self._get_or_compute("opt5", "calc_function_5"),
            "opt6": self._get_or_compute("opt6", "calc_function_6"),
            "opt7": self._get_or_compute("opt7", "calc_function_7"),
            "opt8": self._get_or_compute("opt8", "calc_function_8"),
            "opt9": self._get_or_compute("opt9", "calc_function_9"),
            "opt10": self._get_or_compute("opt10", "calc_function_10"),
        }
