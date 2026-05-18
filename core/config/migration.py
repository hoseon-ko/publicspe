"""QSettings (Windows 레지스트리) → config/settings.json 1회성 마이그레이션.

9개 QSettings 그룹의 키를 docs/CONFIG_SCHEMA.md 매핑표에 따라 새 트리에 채운다.
- QSettings 자체는 손대지 않음 (롤백 가능)
- settings.json 이 이미 존재하면 스킵
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QSettings, QByteArray

from core.config.config_store import ConfigStore


_ORG = "SpeAnalyze"

# 그룹별 키 매핑: [(qsettings_key, json_path, cast_or_None), ...]
_MAINWINDOW_KEYS = [
    ("geometry",             "window.main.geometry",            None),
    ("windowState",          "window.main.windowState",         None),
    ("dockState",            "window.main.dockState",           None),
    ("main_splitter",        "window.main.splitters.main",      None),
    ("side_splitter",        "window.main.splitters.side",      None),
    ("right_splitter_sizes", "window.main.splitters.right",     None),
    ("splitter",             "window.main.splitters.splitter",  None),
    ("active_tab",           "window.main.active_tab",          int),
    ("app/auto_connect",     "window.main.auto_connect",        _to_bool := (lambda v: str(v).lower() in ("1","true","yes","on"))),

    ("acs/ip",        "devices.acs.ip",        str),
    ("acs/port",      "devices.acs.port",      None),
    ("acs/sim",       "devices.acs.sim",       _to_bool),
    ("acs/dry_run",   "devices.acs.dry_run",   _to_bool),
    ("acs/settle",    "devices.acs.settle_ms", None),
    ("acs/settle_ms", "devices.acs.settle_ms", None),

    ("kimm/ip",       "devices.kimm.ip",       str),
    ("kimm/port",     "devices.kimm.port",     None),
    ("kimm/limit",    "devices.kimm.limit_um", float),
    ("kimm/vel",      "devices.kimm.velocity", float),
    ("kimm/dry_run",  "devices.kimm.dry_run",  _to_bool),

    ("sec/camera_collapsed", "ui.sections_collapsed.camera", _to_bool),
    ("sec/motor_collapsed",  "ui.sections_collapsed.motor",  _to_bool),
    ("sec/acs_collapsed",    "ui.sections_collapsed.acs",    _to_bool),
    ("sec/kimm_collapsed",   "ui.sections_collapsed.kimm",   _to_bool),
]

_DEEPALIGN_KEYS = [
    ("camera/vendor", "camera.vendor", str),
]

_LIVETAB_KEYS = [
    ("geometry",  "window.live.geometry",  None),
    ("dockState", "window.live.dockState", None),
]

_CAMERA_PANEL_KEYS = [
    ("camera/exposure_ms", "camera.exposure_ms", float),
    ("camera/fps",         "camera.fps",         float),
    ("camera/fps_lock",    "camera.fps_lock",    _to_bool),
    ("camera/temp",        "camera.temp_c",      float),
    ("camera/type",        "camera.type",        str),
    ("camera/vendor",      "camera.vendor",      str),
    ("camera/adc_bit",     "camera.adc.bit",     None),
    ("camera/adc_gain",    "camera.adc.gain",    str),
    ("camera/adc_quality", "camera.adc.quality", str),
    ("camera/adc_speed",   "camera.adc.speed",   None),
]

_ACQUISITION_KEYS = [
    ("save_dir",  "tabs.acquisition.save_dir",   str),
    ("base_name", "tabs.acquisition.base_name",  str),
    ("inc_date",  "tabs.acquisition.inc_date",   _to_bool),
    ("inc_time",  "tabs.acquisition.inc_time",   _to_bool),
    ("inc_num",   "tabs.acquisition.inc_num",    _to_bool),
    ("exposure",  "tabs.acquisition.exposure",   float),
    ("frames",    "tabs.acquisition.frames",     int),
    ("timeout",   "tabs.acquisition.timeout",    int),
    ("auto_open", "tabs.acquisition.auto_open",  _to_bool),
]

_ANALYSIS_KEYS = [
    ("geometry",    "window.analysis.geometry",    None),
    ("windowState", "window.analysis.windowState", None),
]

_AUTOFOCUS_KEYS = [
    ("combo_metric",    "tabs.autofocus.metric",      str),
    ("spin_center",     "tabs.autofocus.z_center",    float),
    ("spin_range",      "tabs.autofocus.z_range",     float),
    ("spin_step",       "tabs.autofocus.z_step",      float),
    ("spin_avg",        "tabs.autofocus.avg",         int),
    ("spin_settle",     "tabs.autofocus.settle_ms",   int),
    ("chk_goto_best",   "tabs.autofocus.goto_best",   _to_bool),
    ("chk_save_frames", "tabs.autofocus.save_frames", _to_bool),
]

_SCAN_KEYS = [
    ("scan_name",     "tabs.scan.name",          str),
    ("num_steps",     "tabs.scan.num_steps",     int),
    ("steps_move",    "tabs.scan.steps_move",    int),
    ("bin_threshold", "tabs.scan.bin_threshold", int),
    ("motor",         "tabs.scan.motor",         str),
    ("settle_ms",     "tabs.scan.settle_ms",     int),

    ("save/folder",         "tabs.scan.save.folder",         str),
    ("save/file_base",      "tabs.scan.save.file_base",      str),
    ("save/inc_name",       "tabs.scan.save.inc_name",       _to_bool),
    ("save/add_date",       "tabs.scan.save.add_date",       _to_bool),
    ("save/add_time",       "tabs.scan.save.add_time",       _to_bool),
    ("save/date_fmt",       "tabs.scan.save.date_fmt",       str),
    ("save/time_fmt",       "tabs.scan.save.time_fmt",       str),
    ("save/place",          "tabs.scan.save.place",          str),
    ("save/frame_to_save",  "tabs.scan.save.frame_to_save",  str),
]


def _copy_group(qs: QSettings, mapping: list, store: ConfigStore) -> int:
    """그룹의 키들을 복사. 복사된 개수 반환."""
    count = 0
    for qkey, jpath, cast in mapping:
        if not qs.contains(qkey):
            continue
        raw = qs.value(qkey)
        if raw is None:
            continue
        if cast is not None:
            try:
                raw = cast(raw)
            except (TypeError, ValueError):
                continue
        store.set(jpath, raw)
        count += 1
    return count


def _copy_motor_panel(store: ConfigStore) -> int:
    """MotorPanel 의 dynamic key (step_m1..4, weight_fwd_m1..4, weight_bwd_m1..4)."""
    qs = QSettings(_ORG, "MotorPanel")
    count = 0
    for i in range(1, 5):
        for src_pat, dst_attr, cast in [
            (f"step_m{i}",       "step",       int),
            (f"weight_fwd_m{i}", "weight_fwd", float),
            (f"weight_bwd_m{i}", "weight_bwd", float),
        ]:
            if qs.contains(src_pat):
                try:
                    store.set(f"devices.picomotor.motors.m{i}.{dst_attr}",
                              cast(qs.value(src_pat)))
                    count += 1
                except (TypeError, ValueError):
                    pass
    return count


def _copy_acs_kin_steps(store: ConfigStore) -> int:
    """MainWindow 의 acs/kin_step_0..5 → devices.acs.kin_steps[]."""
    qs = QSettings(_ORG, "MainWindow")
    steps = []
    for i in range(6):
        key = f"acs/kin_step_{i}"
        if qs.contains(key):
            try:
                steps.append(float(qs.value(key)))
            except (TypeError, ValueError):
                steps.append(0.1)
        else:
            steps.append(0.1)
    if any(qs.contains(f"acs/kin_step_{i}") for i in range(6)):
        store.set("devices.acs.kin_steps", steps)
        return 6
    return 0


def _copy_acs_kinematic_settings(store: ConfigStore) -> int:
    """acs_settings_panel.py 의 list 타입 키들 (prefix/stage_setup 등)."""
    qs = QSettings(_ORG, "MainWindow")
    count = 0
    for key, jpath in [
        ("stage_setup",  "devices.acs.stage_setup"),
        ("encoder_pos",  "devices.acs.encoder_pos"),
        ("plus_limits",  "devices.acs.plus_limits"),
        ("minus_limits", "devices.acs.minus_limits"),
        ("direction",    "devices.acs.direction"),
        ("mapping",      "devices.acs.mapping"),
        ("pivot",        "devices.acs.pivot"),
        ("beam_z",       "devices.acs.beam_z"),
    ]:
        # acs_settings_panel.py 는 `<prefix>/<key>` 로 저장. prefix 는 호출자가 결정.
        # 일반적인 후보 두 가지를 검사.
        for prefix in ("acs", "kinematic"):
            full = f"{prefix}/{key}"
            if qs.contains(full):
                val = qs.value(full)
                if val is None:
                    continue
                if key == "beam_z":
                    try:
                        store.set(jpath, float(val))
                        count += 1
                    except (TypeError, ValueError):
                        pass
                else:
                    if isinstance(val, list):
                        store.set(jpath, val)
                        count += 1
                break
    return count


def _copy_sections_collapsed_dynamic(store: ConfigStore) -> int:
    """sec/<name>_collapsed 동적 키 — MainWindow + CameraPanel + MotorPanel + LiveTab."""
    count = 0
    for group in ("MainWindow", "CameraPanel", "MotorPanel", "LiveTab"):
        qs = QSettings(_ORG, group)
        for key in qs.allKeys():
            if key.startswith("sec/") and key.endswith("_collapsed"):
                name = key[len("sec/"):-len("_collapsed")]
                try:
                    val = qs.value(key)
                    if isinstance(val, str):
                        val = val.lower() in ("1", "true", "yes", "on")
                    store.set(f"ui.sections_collapsed.{name}", bool(val))
                    count += 1
                except Exception:
                    pass
    return count


def migrate_from_qsettings_if_needed(store: ConfigStore) -> int:
    """settings.json 이 비어있고 QSettings 에 데이터가 있을 때만 1회성 마이그레이션.

    Returns: 복사된 키 개수 (0 이면 마이그레이션 안 함).
    """
    # 이미 설정 파일에 의미있는 내용이 있으면 스킵
    if store.has("window") or store.has("devices"):
        return 0

    # QSettings 에서 한 키도 없으면 스킵 (첫 실행)
    has_any = False
    for group in ("MainWindow", "DeepAlignTab", "LiveTab", "CameraPanel",
                  "MotorPanel", "AcquisitionTab", "AnalysisTab",
                  "AutoFocusTab", "ScanTab"):
        if QSettings(_ORG, group).allKeys():
            has_any = True
            break
    if not has_any:
        return 0

    total = 0
    total += _copy_group(QSettings(_ORG, "MainWindow"),    _MAINWINDOW_KEYS,   store)
    total += _copy_group(QSettings(_ORG, "DeepAlignTab"),  _DEEPALIGN_KEYS,    store)
    total += _copy_group(QSettings(_ORG, "LiveTab"),       _LIVETAB_KEYS,      store)
    total += _copy_group(QSettings(_ORG, "CameraPanel"),   _CAMERA_PANEL_KEYS, store)
    total += _copy_group(QSettings(_ORG, "AcquisitionTab"),_ACQUISITION_KEYS,  store)
    total += _copy_group(QSettings(_ORG, "AnalysisTab"),   _ANALYSIS_KEYS,     store)
    total += _copy_group(QSettings(_ORG, "AutoFocusTab"),  _AUTOFOCUS_KEYS,    store)
    total += _copy_group(QSettings(_ORG, "ScanTab"),       _SCAN_KEYS,         store)

    total += _copy_motor_panel(store)
    total += _copy_acs_kin_steps(store)
    total += _copy_acs_kinematic_settings(store)
    total += _copy_sections_collapsed_dynamic(store)

    if total > 0:
        store.save()
        try:
            from core.logger import dev_logger
            dev_logger.info(f"[config] migrated {total} keys from QSettings → {store.path}")
        except Exception:
            pass

    return total
