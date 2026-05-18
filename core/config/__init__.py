"""SpeAnalyze 설정 단일 출처.

`config/settings.json` 을 메모리로 로드해 dotted-path 로 접근.
첫 부팅 시 QSettings(레지스트리) 에서 자동 마이그레이션.

사용 패턴:
    from core.config import get_config
    cfg = get_config()
    ip = cfg.get("devices.acs.ip", "10.0.0.100")
    cfg.set("devices.acs.ip", "10.0.0.50")
    cfg.save()
"""

from core.config.config_store import ConfigStore, get_config

__all__ = ["ConfigStore", "get_config"]
