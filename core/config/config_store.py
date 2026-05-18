"""파일 기반 설정 스토어 — QSettings 대체.

- 단일 JSON (`config/settings.json`) 을 메모리 dict 로 보관
- dotted-path 로 get/set (예: "devices.acs.ip")
- atomic write (`.tmp` → `os.replace`)
- 동일 프로세스 내 RLock 으로 직렬화
- QByteArray 는 `"b64:<base64>"` 접두어로 직렬화
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QByteArray


# ── 파일 위치 ────────────────────────────────────────────────────────────────
# 프로젝트 루트의 config/settings.json. 패키지 위치 기준 두 단계 상위.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_DIR   = _PROJECT_ROOT / "config"
_CONFIG_FILE  = _CONFIG_DIR / "settings.json"

_SCHEMA_VERSION = 1


# ── 직렬화 헬퍼 ──────────────────────────────────────────────────────────────
_B64_PREFIX = "b64:"

def _serialize(value: Any) -> Any:
    """QByteArray → base64 문자열, 그 외는 그대로."""
    if isinstance(value, QByteArray):
        return _B64_PREFIX + base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, bytes):
        return _B64_PREFIX + base64.b64encode(value).decode("ascii")
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return value


def _deserialize(value: Any) -> Any:
    """`b64:` 접두어 → QByteArray, 그 외는 그대로."""
    if isinstance(value, str) and value.startswith(_B64_PREFIX):
        raw = base64.b64decode(value[len(_B64_PREFIX):])
        return QByteArray(raw)
    return value


# ── 스토어 ───────────────────────────────────────────────────────────────────
class ConfigStore:
    """단일 JSON 설정 스토어."""

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path else _CONFIG_FILE
        self._lock = threading.RLock()
        self._data: dict = {"version": _SCHEMA_VERSION}
        self._load()

    # ── 파일 IO ───────────────────────────────────────────
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as f:
                self._data = json.load(f) or {"version": _SCHEMA_VERSION}
        except (json.JSONDecodeError, OSError) as e:
            # 손상 시 백업 + 기본값
            backup = self._path.with_suffix(f".corrupt-{int(time.time())}")
            try:
                shutil.copy2(self._path, backup)
            except Exception:
                pass
            try:
                from core.logger import dev_logger
                dev_logger.error(f"[config] load failed: {e} — backup={backup}")
            except Exception:
                pass
            self._data = {"version": _SCHEMA_VERSION}

    def save(self) -> None:
        """atomic write."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False, sort_keys=False)
            os.replace(tmp, self._path)

    # ── dotted-path 접근 ──────────────────────────────────
    def get(self, path: str, default: Any = None) -> Any:
        """`a.b.c` 경로로 값을 읽는다. 없으면 default."""
        with self._lock:
            node: Any = self._data
            for part in path.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    return default
            return _deserialize(node)

    def set(self, path: str, value: Any) -> None:
        """`a.b.c` 경로에 값을 쓴다. 중간 dict 자동 생성."""
        with self._lock:
            parts = path.split(".")
            node = self._data
            for part in parts[:-1]:
                nxt = node.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    node[part] = nxt
                node = nxt
            node[parts[-1]] = _serialize(value)

    def get_typed(self, path: str, default: Any, cast) -> Any:
        """get + 명시적 캐스팅. QSettings 의 `value(key, default, type=X)` 흉내."""
        val = self.get(path, default)
        if val is None:
            return default
        try:
            return cast(val)
        except (TypeError, ValueError):
            return default

    # ── 카메라 vendor-aware 헬퍼 ──────────────────────────
    def get_camera_setting(self, name: str, default: Any = None,
                           vendor: str | None = None) -> Any:
        """현재 또는 지정된 vendor 의 카메라 설정을 반환.

        vendor 미지정 시 `camera.last_used.vendor` 사용.
        """
        if vendor is None:
            vendor = str(self.get("camera.last_used.vendor", "Simulation") or "Simulation")
        return self.get(f"camera.vendors.{vendor}.{name}", default)

    def set_camera_setting(self, name: str, value: Any,
                           vendor: str | None = None) -> None:
        """현재 또는 지정된 vendor 의 카메라 설정을 저장."""
        if vendor is None:
            vendor = str(self.get("camera.last_used.vendor", "Simulation") or "Simulation")
        self.set(f"camera.vendors.{vendor}.{name}", value)

    def set_last_camera(self, vendor: str, device_id: str = "") -> None:
        """마지막 사용 카메라 기록 — 다음 부팅 자동 재연결용."""
        self.set("camera.last_used.vendor", str(vendor))
        self.set("camera.last_used.device_id", str(device_id))

    def has(self, path: str) -> bool:
        with self._lock:
            node: Any = self._data
            for part in path.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    return False
            return True

    def remove(self, path: str) -> None:
        with self._lock:
            parts = path.split(".")
            node = self._data
            for part in parts[:-1]:
                nxt = node.get(part) if isinstance(node, dict) else None
                if not isinstance(nxt, dict):
                    return
                node = nxt
            if isinstance(node, dict):
                node.pop(parts[-1], None)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def data(self) -> dict:
        """디버그/검사용 raw dict 참조."""
        return self._data


# ── 싱글톤 ───────────────────────────────────────────────────────────────────
_instance: ConfigStore | None = None
_singleton_lock = threading.Lock()


_DEFAULT_SKELETON = {
    "version": _SCHEMA_VERSION,
    "window": {},
    "devices": {},
    "camera": {"last_used": {"vendor": "Simulation", "device_id": ""}, "vendors": {}},
    "ui": {},
    "tabs": {},
}


def _upgrade_camera_schema(store: ConfigStore) -> bool:
    """옛 평면 카메라 스키마 → vendor-별 분리 스키마로 1회성 업그레이드.

    옛: camera.{vendor, exposure_ms, fps, ...}
    새: camera.{last_used.{vendor,device_id}, vendors.<vendor>.{exposure_ms, ...}}

    Returns: 업그레이드 발생했으면 True
    """
    cam = store.data.get("camera", {})
    if not isinstance(cam, dict) or not cam:
        return False
    # 이미 새 구조면 스킵
    if "last_used" in cam or "vendors" in cam:
        return False
    # 평면 vendor 값 추출
    vendor = str(cam.get("vendor", "Simulation") or "Simulation")
    flat = {k: v for k, v in cam.items() if k != "vendor"}
    store.data["camera"] = {
        "last_used": {"vendor": vendor, "device_id": ""},
        "vendors":   {vendor: flat},
    }
    store.save()
    try:
        from core.logger import dev_logger
        dev_logger.info(f"[config] upgraded camera schema → vendors.{vendor} ({len(flat)} keys)")
    except Exception:
        pass
    return True


def get_config() -> ConfigStore:
    """프로세스 전역 ConfigStore 싱글톤.

    첫 호출 시 파일 로드 + (필요시) QSettings 마이그레이션 + 골격 보장.
    파일이 없으면 빈 골격으로 즉시 생성한다 (config/ 폴더도 자동 mkdir).
    """
    global _instance
    if _instance is None:
        with _singleton_lock:
            if _instance is None:
                store = ConfigStore()
                fresh = not store.path.exists()
                # 마이그레이션: 파일이 비어있고 QSettings 에 데이터가 있을 때만
                if fresh or not store.data.get("window"):
                    from core.config.migration import migrate_from_qsettings_if_needed
                    migrate_from_qsettings_if_needed(store)
                # 첫 부팅 보장: 마이그레이션이 0건이어도 골격 + 파일 생성
                if not store.path.exists():
                    for k, v in _DEFAULT_SKELETON.items():
                        if k not in store.data:
                            store.data[k] = v if not isinstance(v, dict) else dict(v)
                    store.save()
                # 옛 평면 카메라 스키마 → vendor-별 자동 업그레이드
                _upgrade_camera_schema(store)
                _instance = store
    return _instance
