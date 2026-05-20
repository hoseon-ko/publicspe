import numpy as np

def calc_function_1(data: np.ndarray) -> float:
    """Option 1: Mean (평균)"""
    return float(np.mean(data))

def calc_function_2(data: np.ndarray) -> float:
    """Option 2: Min (최솟값)"""
    return float(np.min(data))

def calc_function_3(data: np.ndarray) -> float:
    """Option 3: Max (최댓값)"""
    return float(np.max(data))

def calc_function_4(data: np.ndarray) -> float:
    """Option 4: Standard Deviation (표준편차)"""
    return float(np.std(data))

def calc_function_5(data: np.ndarray) -> float:
    """Option 5: Sum (합계)"""
    return float(np.sum(data))

def calc_function_6(data: np.ndarray) -> float:
    """Option 6: Median (중앙값)"""
    return float(np.median(data))

def calc_function_7(data: np.ndarray) -> float:
    """Option 7: Variance (분산)"""
    return float(np.var(data))

def calc_function_8(data: np.ndarray) -> float:
    """Option 8: 90th Percentile (90% 백분위수)"""
    return float(np.percentile(data, 90))

def calc_function_9(data: np.ndarray) -> float:
    """Option 9: 10th Percentile (10% 백분위수)"""
    return float(np.percentile(data, 10))

def calc_function_10(data: np.ndarray) -> float:
    """Option 10: 50th Percentile (50% 백분위수)"""
    return float(np.percentile(data, 50))
