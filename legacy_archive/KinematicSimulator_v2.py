"""
AlignStage Kinematic Move Simulator - Streamlit UI
실행: streamlit run KinematicSimulator_v2.py
"""

import sys
import numpy as np
import streamlit as st
import pandas as pd

sys.path.append(r"D:\source\AMMI_ICM\ICM\ESOL.AlignStageAlgorithm")
from AlignStageAlgorithm import CalculateBallPositionPivot

st.set_page_config(page_title="AlignStage Kinematic Simulator", layout="wide")
st.title("🔧 AlignStage Kinematic Simulator")
st.caption("C# `CalculatePosition()` 로직 재현 — 실제 장비 없이 calPos 및 인터락 확인")

# =============================================================================
# 기본값 (XML 설정 기준)
# =============================================================================
# --- Pivot ---
_PIVOT_X = -280.4054
_PIVOT_Y =  940.34
_PIVOT_Z = 1525.1744
_BEAM_Z_PATH_DEGREE = 4.0  # degree (입사각 기본값)

# --- Stage Setup Position ---
# Stage1: X=5.3867, Y=0, Z=796
# Stage2: X=-346.3333, Y=0, Z=-398
# Stage3: X=340.9467, Y=0, Z=-398
_SP = {
    "sp1X":  5.3867,    "sp1Y": 0.0,   "sp1Z":  796.0,
    "sp2X": -346.3333,  "sp2Y": 0.0,   "sp2Z": -398.0,
    "sp3X":  340.9467,  "sp3Y": 0.0,   "sp3Z": -398.0,
}

# --- Stage Setup Encoder Position ---
# Stage1: X=1277.5,  Y=-1513.68, Z=0
# Stage2: X=0,       Y=-1592.31, Z=804.45
# Stage3: X=1052.41, Y=-1433.52, Z=0
_EP = {
    "ep1X": 1277.5,    "ep1Y": -1513.68, "ep1Z": 0.0,
    "ep2X": 0.0,       "ep2Y": -1592.31, "ep2Z": 804.45,
    "ep3X": 1052.41,   "ep3Y": -1433.52, "ep3Z": 0.0,
}

# --- Direction Mapping ---
# Stage1: X=1,  Y=1,  Z=1
# Stage2: X=1,  Y=1,  Z=1
# Stage3: X=-1, Y=1,  Z=1
_DM = {
    "dm1X":  1, "dm1Y": 1, "dm1Z":  1,
    "dm2X":  1, "dm2Y": 1, "dm2Z":  1,
    "dm3X": -1, "dm3Y": 1, "dm3Z":  1,
}

# --- Axis Mapping (Slave=1 / Master=0) ---
# Stage1: MappingX1=0, MappingY1=0, MappingZ1=1  → Z1 Slave
# Stage2: MappingX2=1, MappingY2=0, MappingZ2=0  → X2 Slave
# Stage3: MappingX3=0, MappingY3=0, MappingZ3=1  → Z3 Slave
_SM = {
    "sm1X": 0, "sm1Y": 0, "sm1Z": 1,
    "sm2X": 1, "sm2Y": 0, "sm2Z": 0,
    "sm3X": 0, "sm3Y": 0, "sm3Z": 1,
}

# --- Motor Soft Limits ---
# [Y1(Ax0), Z1(Ax1), X1(Ax4→Z2방향), Z2(Ax5), Y2(Ax8), Z3(Ax9)]
# C# AxisNum 순서: Ax0=Y1, Ax1=Z1, Ax4=X1(Stage2 master), Ax5=Z2(Stage2 slave),
#                 Ax8=Y2(Stage3 master), Ax9=Z3(Stage3 slave)
# 주의: C# calPos 인덱스
#   calPos[0] = Ax0(Y1)   enc[0,0] = EncoderY1  = -1513.68 → Stage1 master(Y)
#   calPos[1] = Ax1(Z1)   enc[0,1] = EncoderZ1  =       0  → Stage1 slave(Z)
#   calPos[2] = Ax4(X1)   enc[1,2] = EncoderZ2  =  804.45  → Stage2 master(Z)  ← 주목
#   calPos[3] = Ax5(Z2)   enc[1,1] = EncoderY2  = -1592.31 → Stage2 slave(Y)   ← 주목
#   calPos[4] = Ax8(Y2)   enc[2,0] = EncoderX3  = 1052.41  → Stage3 master(X)  ← 주목
#   calPos[5] = Ax9(Z3)   enc[2,1] = EncoderY3  = -1433.52 → Stage3 slave(Y)   ← 주목
_PLUS_LIMITS  = [1287.5,  -1503.68,  814.45,  -1582.31,  1062.41,  -1423.52]
_MINUS_LIMITS = [1267.5,  -1523.68,  794.45,  -1602.31,  1042.41,  -1443.52]

# =============================================================================
# Sidebar: CONFIG
# =============================================================================
with st.sidebar:
    st.header("⚙️ CONFIG")

    st.subheader("Pivot Position (mm)")
    pivot_x = st.number_input("Pivot X + Offset", value=_PIVOT_X, format="%.4f", key="pvx")
    pivot_y = st.number_input("Pivot Y + Offset", value=_PIVOT_Y, format="%.4f", key="pvy")
    pivot_z = st.number_input("Pivot Z + Offset", value=_PIVOT_Z, format="%.4f", key="pvz")

    st.subheader("Beam Z Path Degree (degree)")
    beam_z_path_degree = st.number_input("BeamZPathDegree (deg, 기본 4도)", value=_BEAM_Z_PATH_DEGREE, format="%.4f", key="beam_deg")

    st.subheader("Stage Setup Position (mm)")
    cols = st.columns(3)
    sp = {}
    for s in range(1, 4):
        for i, ax in enumerate(["X", "Y", "Z"]):
            key = f"sp{s}{ax}"
            sp[key] = cols[i].number_input(
                f"Stage{s} {ax}", value=_SP[key], format="%.4f", key=key)

    st.subheader("Stage Setup Encoder Position (mm)")
    ep = {}
    for s in range(1, 4):
        cols2 = st.columns(3)
        for i, ax in enumerate(["X", "Y", "Z"]):
            key = f"ep{s}{ax}"
            ep[key] = cols2[i].number_input(
                f"Enc{s} {ax}", value=_EP[key], format="%.4f", key=key)

    st.subheader("Direction Mapping (+1 / -1)")
    dm = {}
    for s in range(1, 4):
        cols3 = st.columns(3)
        for i, ax in enumerate(["X", "Y", "Z"]):
            key = f"dm{s}{ax}"
            dm[key] = cols3[i].selectbox(
                f"Dir{s}{ax}", [1, -1],
                index=0 if _DM[key] == 1 else 1,
                key=key)

    st.subheader("Axis Mapping (Slave=1 / Master=0)")
    st.caption("실제로 움직이지 않는 축 = Slave(1),  움직이는 축 = Master(0)")
    # Stage1: Z1=Slave(1), Y1=Master(0), X1=Master(0)
    # Stage2: X2=Slave(1), Y2=Master(0), Z2=Master(0)
    # Stage3: Z3=Slave(1), Y3=Master(0), X3=Master(0)
    sm = {}
    for s in range(1, 4):
        cols6 = st.columns(3)
        for i, ax in enumerate(["X", "Y", "Z"]):
            key = f"sm{s}{ax}"
            sm[key] = cols6[i].selectbox(
                f"Map{s} {ax}",
                options=[0, 1],
                index=_SM[key],
                format_func=lambda x: "Slave(1)" if x == 1 else "Master(0)",
                key=key)

    st.subheader("Motor Soft Limits (mm)")
    motor_names = ["Y1(Ax0)", "Z1(Ax1)", "X1(Ax4)", "Z2(Ax5)", "Y2(Ax8)", "Z3(Ax9)"]
    plus_limits  = []
    minus_limits = []
    for idx, name in enumerate(motor_names):
        c1, c2 = st.columns(2)
        plus_limits.append( c1.number_input(f"{name} +", value=_PLUS_LIMITS[idx],  format="%.3f", key=f"pl_{name}"))
        minus_limits.append(c2.number_input(f"{name} -", value=_MINUS_LIMITS[idx], format="%.3f", key=f"ml_{name}"))

# =============================================================================
# Build Config Arrays
# =============================================================================
PIVOT = np.array([pivot_x, pivot_y, pivot_z])

STAGE_SETUP_POS = np.array([
    sp["sp1X"], sp["sp1Y"], sp["sp1Z"],
    sp["sp2X"], sp["sp2Y"], sp["sp2Z"],
    sp["sp3X"], sp["sp3Y"], sp["sp3Z"],
], dtype=float)

STAGE_SETUP_ENCODER_POS = np.array([
    [ep["ep1X"], ep["ep1Y"], ep["ep1Z"]],
    [ep["ep2X"], ep["ep2Y"], ep["ep2Z"]],
    [ep["ep3X"], ep["ep3Y"], ep["ep3Z"]],
], dtype=float)

STAGE_DIRECTION = np.array([
    [dm["dm1X"], dm["dm1Y"], dm["dm1Z"]],
    [dm["dm2X"], dm["dm2Y"], dm["dm2Z"]],
    [dm["dm3X"], dm["dm3Y"], dm["dm3Z"]],
], dtype=float)

# C# mappingMasterStage = mappingSlaveStage (동일한 값)
MAPPING_MASTER = np.array([
    sm["sm1X"], sm["sm1Y"], sm["sm1Z"],
    sm["sm2X"], sm["sm2Y"], sm["sm2Z"],
    sm["sm3X"], sm["sm3Y"], sm["sm3Z"],
], dtype=float)
MAPPING_SLAVE = MAPPING_MASTER.copy()

# =============================================================================
# Core Logic
# =============================================================================
def calculate_position(trans_pos, rotate_pos_mrad, pivot_override=None):
    """pivot_override가 있으면 그 pivot을 사용 (Path Move / Beam Angle용)"""
    try:
        mapping_trans  = np.array(trans_pos, dtype=float)
        mapping_rotate = np.array(rotate_pos_mrad, dtype=float) / 1000.0
        ssp  = STAGE_SETUP_POS.copy()
        piv  = pivot_override if pivot_override is not None else PIVOT

        ball_pos_raw = CalculateBallPositionPivot(
            mapping_rotate, mapping_trans,
            ssp, ssp, MAPPING_MASTER, ssp, MAPPING_SLAVE, ssp,
            piv
        )

        result_ball        = ball_pos_raw.reshape(3, 3)
        stage_setup_pos3x3 = STAGE_SETUP_POS.reshape(3, 3)

        final = np.zeros((3, 2))
        final[0, 0] = result_ball[0, 0] - stage_setup_pos3x3[0, 0]  # Stage1 [0,0]
        final[0, 1] = result_ball[0, 1] - stage_setup_pos3x3[0, 1]  # Stage1 [0,1]
        final[1, 0] = result_ball[1, 2] - stage_setup_pos3x3[1, 2]  # Stage2 [1,2]
        final[1, 1] = result_ball[1, 1] - stage_setup_pos3x3[1, 1]  # Stage2 [1,1]
        final[2, 0] = result_ball[2, 0] - stage_setup_pos3x3[2, 0]  # Stage3 [2,0]
        final[2, 1] = result_ball[2, 1] - stage_setup_pos3x3[2, 1]  # Stage3 [2,1]

        enc = STAGE_SETUP_ENCODER_POS
        d   = STAGE_DIRECTION

        # C# calPos 계산 그대로:
        # calPos[0] = enc[0,0] + final[0,0]*d[0,0]  → Y1(Ax0)  enc=EncoderY1=-1513.68
        # calPos[1] = enc[0,1] + final[0,1]*d[0,1]  → Z1(Ax1)  enc=EncoderZ1=0
        # calPos[2] = enc[1,2] + final[1,0]*d[1,2]  → X1(Ax4)  enc=EncoderZ2=804.45
        # calPos[3] = enc[1,1] + final[1,1]*d[1,1]  → Z2(Ax5)  enc=EncoderY2=-1592.31
        # calPos[4] = enc[2,0] + final[2,0]*d[2,0]  → Y2(Ax8)  enc=EncoderX3=1052.41
        # calPos[5] = enc[2,1] + final[2,1]*d[2,1]  → Z3(Ax9)  enc=EncoderY3=-1433.52
        cal_pos = np.array([
            enc[0,0] + final[0,0] * d[0,0],
            enc[0,1] + final[0,1] * d[0,1],
            enc[1,2] + final[1,0] * d[1,2],
            enc[1,1] + final[1,1] * d[1,1],
            enc[2,0] + final[2,0] * d[2,0],
            enc[2,1] + final[2,1] * d[2,1],
        ])
        return cal_pos, result_ball
    except Exception as e:
        return None, str(e)


def calc_beam_pivot(beam_angle_mrad):
    """Beam Angle(mrad) → pivot 보정 위치 반환
    C# CalculateBeamPathPosition 동일:
      pivotY += (mrad/1000) * sin(BeamZPathDeg_rad) * -1
      pivotZ += (mrad/1000) * cos(BeamZPathDeg_rad)
    """
    bz_rad   = beam_z_path_degree * (np.pi / 180.0)
    bpath_mm = beam_angle_mrad / 1000.0
    return np.array([
        PIVOT[0],
        PIVOT[1] + bpath_mm * np.sin(bz_rad) * -1,
        PIVOT[2] + bpath_mm * np.cos(bz_rad),
    ])


def calc_effective_pivot_z(trans_z, pivot_override=None):
    pivot = pivot_override if pivot_override is not None else PIVOT
    return pivot[2] + float(trans_z)


def calc_effective_pivot_y(trans_z, pivot_override=None):
    pivot = pivot_override if pivot_override is not None else PIVOT
    effective_pivot_z = calc_effective_pivot_z(trans_z, pivot_override=pivot)
    angle_rad = beam_z_path_degree * (np.pi / 180.0)
    y_offset_from_z = -effective_pivot_z * np.tan(angle_rad)
    effective_pivot_y = pivot[1] + y_offset_from_z
    return effective_pivot_y, y_offset_from_z, effective_pivot_z


def check_interlock(cal_pos):
    violations = []
    for pos, name, plus, minus in zip(cal_pos, motor_names, plus_limits, minus_limits):
        if pos > plus:
            violations.append(f"⚠ {name}: {pos:.5f} > PlusLimit {plus}")
        if pos < minus:
            violations.append(f"⚠ {name}: {pos:.5f} < MinusLimit {minus}")
    return len(violations) == 0, violations


def show_cal_result(trans, rotate):
    cal, ball = calculate_position(trans, rotate)
    if cal is None:
        st.error(f"계산 실패: {ball}")
        return

    ok, violations = check_interlock(cal)

    st.markdown("##### 볼 위치 (3D, mm)")
    ball_df = pd.DataFrame(ball, columns=["X", "Y", "Z"],
                           index=["Stage1(Y1/Z1)", "Stage2(X1/Z2)", "Stage3(Y2/Z3)"])
    st.dataframe(ball_df.style.format("{:.5f}"), use_container_width=True)

    st.markdown("##### 모터 CalPos (mm)")
    rows = []
    for name, pos, plus, minus in zip(motor_names, cal, plus_limits, minus_limits):
        status = "✅ OK" if minus <= pos <= plus else "❌ LIMIT"
        rows.append({"Motor": name, "CalPos": round(pos, 5),
                     "MinusLimit": minus, "PlusLimit": plus, "상태": status})
    result_df = pd.DataFrame(rows)

    def highlight_limit(row):
        color = "background-color: #ffcccc" if "LIMIT" in row["상태"] else ""
        return [color] * len(row)

    st.dataframe(result_df.style.apply(highlight_limit, axis=1), use_container_width=True)

    if ok:
        st.success("✅ 인터락 통과 → 이동 가능")
    else:
        st.error("❌ 인터락 위반 → 이동 불가")
        for v in violations:
            st.warning(v)
    return cal

# =============================================================================
# Tab Layout
# =============================================================================
tab1, tab2, tab3, tab4 = st.tabs(["🎯 단일 입력", "📊 축 스캔", "🧪 프리셋 테스트", "🔀 Path Move (Beam Angle)"])

with tab1:
    st.subheader("Trans / Rotate 입력")
    c1, c2, c3 = st.columns(3)
    tx = c1.number_input("Trans X (mm)",    value=0.0, format="%.4f", key="tx")
    ty = c2.number_input("Trans Y (mm)",    value=0.0, format="%.4f", key="ty")
    tz = c3.number_input("Trans Z (mm)",    value=0.0, format="%.4f", key="tz")
    effective_pivot_y, y_offset_from_z, effective_pivot_z = calc_effective_pivot_y(tz)
    st.info(
        f"기준 Pivot Z: {PIVOT[2]:.4f} mm | Trans Z: {tz:+.4f} mm | Pivot Z + Trans Z: {effective_pivot_z:.4f} mm"
    )
    st.info(
        f"입사각 {beam_z_path_degree:.1f}° 기준 Pivot Y: {effective_pivot_y:.4f} mm "
        f"(기준 대비 Δ{effective_pivot_y - PIVOT[1]:+.4f} mm, Z 기준 Y 오프셋 {y_offset_from_z:+.4f} mm)"
    )
    c4, c5, c6 = st.columns(3)
    rx = c4.number_input("Rotate Rx (mrad)", value=0.0, format="%.4f", key="rx")
    ry = c5.number_input("Rotate Ry (mrad)", value=0.0, format="%.4f", key="ry")
    rz = c6.number_input("Rotate Rz (mrad)", value=0.0, format="%.4f", key="rz")

    if st.button("🔍 계산", key="btn_single", type="primary"):
        show_cal_result([tx, ty, tz], [rx, ry, rz])

with tab2:
    st.subheader("축 범위 스캔 — 어느 값에서 리밋 초과하는지 확인")
    s_col1, s_col2, s_col3, s_col4 = st.columns(4)
    scan_axis  = s_col1.selectbox("스캔 축", ["X", "Y", "Z", "Rx", "Ry", "Rz"])
    scan_start = s_col2.number_input("시작값", value=-5.0,  format="%.3f")
    scan_end   = s_col3.number_input("끝값",   value=5.0,   format="%.3f")
    scan_step  = s_col4.number_input("스텝",   value=0.5,   format="%.3f", min_value=0.001)

    st.markdown("고정 값 (스캔하지 않는 축)")
    fc1, fc2, fc3, fc4, fc5, fc6 = st.columns(6)
    fx  = fc1.number_input("Fix X",  value=0.0, format="%.3f", key="fx")
    fy  = fc2.number_input("Fix Y",  value=0.0, format="%.3f", key="fy")
    fz  = fc3.number_input("Fix Z",  value=0.0, format="%.3f", key="fz")
    frx = fc4.number_input("Fix Rx", value=0.0, format="%.3f", key="frx")
    fry = fc5.number_input("Fix Ry", value=0.0, format="%.3f", key="fry")
    frz = fc6.number_input("Fix Rz", value=0.0, format="%.3f", key="frz")

    if st.button("📊 스캔 실행", key="btn_scan", type="primary"):
        values = np.arange(scan_start, scan_end + scan_step * 0.001, scan_step)
        rows = []
        for v in values:
            if   scan_axis == "X":  t, r = [v, fy, fz],  [frx, fry, frz]
            elif scan_axis == "Y":  t, r = [fx, v, fz],  [frx, fry, frz]
            elif scan_axis == "Z":  t, r = [fx, fy, v],  [frx, fry, frz]
            elif scan_axis == "Rx": t, r = [fx, fy, fz], [v,   fry, frz]
            elif scan_axis == "Ry": t, r = [fx, fy, fz], [frx, v,   frz]
            elif scan_axis == "Rz": t, r = [fx, fy, fz], [frx, fry, v  ]

            cal, _ = calculate_position(t, r)
            if cal is None:
                rows.append({scan_axis: v, "Y1": None, "Z1": None, "X1": None,
                              "Z2": None, "Y2": None, "Z3": None, "상태": "ERROR"})
                continue
            ok, _ = check_interlock(cal)
            rows.append({
                scan_axis: round(v, 4),
                "Y1": round(cal[0], 5), "Z1": round(cal[1], 5),
                "X1": round(cal[2], 5), "Z2": round(cal[3], 5),
                "Y2": round(cal[4], 5), "Z3": round(cal[5], 5),
                "상태": "✅ OK" if ok else "❌ LIMIT",
            })

        df = pd.DataFrame(rows)

        def highlight_scan(row):
            color = "background-color: #ffcccc" if "LIMIT" in str(row["상태"]) else ""
            return [color] * len(row)

        st.dataframe(df.style.apply(highlight_scan, axis=1), use_container_width=True)

        st.markdown("##### CalPos 그래프")
        chart_df = df[[scan_axis, "Y1", "Z1", "X1", "Z2", "Y2", "Z3"]].set_index(scan_axis)
        st.line_chart(chart_df, use_container_width=True)

with tab3:
    st.subheader("사전 정의 테스트 케이스")
    PRESET_TESTS = [
        ("원점",       [0.0,  0.0,  0.0], [0.0,  0.0,  0.0]),
        ("X +1mm",     [1.0,  0.0,  0.0], [0.0,  0.0,  0.0]),
        ("X -1mm",    [-1.0,  0.0,  0.0], [0.0,  0.0,  0.0]),
        ("Y +1mm",     [0.0,  1.0,  0.0], [0.0,  0.0,  0.0]),
        ("Y -1mm",     [0.0, -1.0,  0.0], [0.0,  0.0,  0.0]),
        ("Z +1mm",     [0.0,  0.0,  1.0], [0.0,  0.0,  0.0]),
        ("Z -1mm",     [0.0,  0.0, -1.0], [0.0,  0.0,  0.0]),
        ("Rx +1mrad",  [0.0,  0.0,  0.0], [1.0,  0.0,  0.0]),
        ("Ry +1mrad",  [0.0,  0.0,  0.0], [0.0,  1.0,  0.0]),
        ("Rz +1mrad",  [0.0,  0.0,  0.0], [0.0,  0.0,  1.0]),
        ("복합 이동",  [0.5,  0.3, -0.2], [0.5, -0.3,  0.1]),
    ]

    if st.button("🧪 전체 테스트 실행", key="btn_preset", type="primary"):
        rows = []
        for desc, trans, rotate in PRESET_TESTS:
            cal, ball = calculate_position(trans, rotate)
            if cal is None:
                rows.append({"케이스": desc, "Y1": "ERR", "Z1": "ERR",
                              "X1": "ERR", "Z2": "ERR", "Y2": "ERR", "Z3": "ERR", "상태": "ERROR"})
                continue
            ok, violations = check_interlock(cal)
            rows.append({
                "케이스": desc,
                "Y1": round(cal[0], 4), "Z1": round(cal[1], 4),
                "X1": round(cal[2], 4), "Z2": round(cal[3], 4),
                "Y2": round(cal[4], 4), "Z3": round(cal[5], 4),
                "상태": "✅ OK" if ok else "❌ LIMIT | " + " | ".join(violations),
            })

        df = pd.DataFrame(rows)

        def highlight_preset(row):
            color = "background-color: #ffcccc" if "LIMIT" in str(row["상태"]) else ""
            return [color] * len(row)

        st.dataframe(df.style.apply(highlight_preset, axis=1), use_container_width=True)

with tab4:
    st.subheader("🔀 Path Move (Beam Angle 적용)")
    st.caption(
        "Beam Angle(mrad) → pivot Y/Z 보정 후 calPos 계산  "
        "| C# CalculateBeamPathPosition 동일 로직"
    )

    pa_c1, pa_c2, pa_c3 = st.columns(3)
    pa_tx = pa_c1.number_input("Trans X (mm)",     value=0.0, format="%.4f", key="pa_tx")
    pa_ty = pa_c2.number_input("Trans Y (mm)",     value=0.0, format="%.4f", key="pa_ty")
    pa_tz = pa_c3.number_input("Trans Z (mm)",     value=0.0, format="%.4f", key="pa_tz")
    pa_c4, pa_c5, pa_c6 = st.columns(3)
    pa_rx = pa_c4.number_input("Rotate Rx (mrad)", value=0.0, format="%.4f", key="pa_rx")
    pa_ry = pa_c5.number_input("Rotate Ry (mrad)", value=0.0, format="%.4f", key="pa_ry")
    pa_rz = pa_c6.number_input("Rotate Rz (mrad)", value=0.0, format="%.4f", key="pa_rz")

    pa_beam = st.number_input(
        "Beam Angle (mrad)",
        value=0.0, format="%.4f", key="pa_beam_angle",
        help="pivotY += (mrad/1000)×sin(BeamZPathDeg)×−1 / pivotZ += (mrad/1000)×cos(BeamZPathDeg)"
    )

    # 실시간 pivot 미리보기
    _pv = calc_beam_pivot(pa_beam)
    _effective_pivot_y, _y_offset_from_z, _effective_pivot_z = calc_effective_pivot_y(pa_tz, pivot_override=_pv)
    st.info(
        f"적용 Pivot  X: {_pv[0]:.4f}  "
        f"Y: {_pv[1]:.4f} (Δ{_pv[1]-PIVOT[1]:+.4f})  "
        f"Z: {_pv[2]:.4f} (Δ{_pv[2]-PIVOT[2]:+.4f})  "
        f"[BeamZPathDegree = {beam_z_path_degree}°]"
    )
    st.info(
        f"적용 Pivot Z: {_pv[2]:.4f} mm | Trans Z: {pa_tz:+.4f} mm | Pivot Z + Trans Z: {_effective_pivot_z:.4f} mm"
    )
    st.info(
        f"입사각 {beam_z_path_degree:.1f}° 기준 최종 Pivot Y: {_effective_pivot_y:.4f} mm "
        f"(기준 대비 Δ{_effective_pivot_y - PIVOT[1]:+.4f} mm, Z 기준 Y 오프셋 {_y_offset_from_z:+.4f} mm)"
    )

    if st.button("🔍 계산", key="btn_path", type="primary"):
        pivot_beam = calc_beam_pivot(pa_beam)
        cal, ball  = calculate_position(
            [pa_tx, pa_ty, pa_tz],
            [pa_rx, pa_ry, pa_rz],
            pivot_override=pivot_beam
        )

        if cal is None:
            st.error(f"계산 실패: {ball}")
        else:
            ok, violations = check_interlock(cal)

            st.markdown("##### 볼 위치 (3D, mm)")
            ball_df = pd.DataFrame(ball, columns=["X", "Y", "Z"],
                                   index=["Stage1(Y1/Z1)", "Stage2(X1/Z2)", "Stage3(Y2/Z3)"])
            st.dataframe(ball_df.style.format("{:.5f}"), use_container_width=True)

            st.markdown("##### 모터 CalPos (mm)")
            rows = []
            for name, pos, plus, minus in zip(motor_names, cal, plus_limits, minus_limits):
                status = "✅ OK" if minus <= pos <= plus else "❌ LIMIT"
                rows.append({"Motor": name, "CalPos": round(pos, 5),
                             "MinusLimit": minus, "PlusLimit": plus, "상태": status})
            result_df = pd.DataFrame(rows)

            def highlight_limit_path(row):
                color = "background-color: #ffcccc" if "LIMIT" in row["상태"] else ""
                return [color] * len(row)

            st.dataframe(result_df.style.apply(highlight_limit_path, axis=1), use_container_width=True)

            if ok:
                st.success("✅ 인터락 통과 → 이동 가능")
            else:
                st.error("❌ 인터락 위반 → 이동 불가")
                for v in violations:
                    st.warning(v)
