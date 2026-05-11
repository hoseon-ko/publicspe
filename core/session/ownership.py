"""Session ownership constants and helpers."""

from __future__ import annotations

from typing import Final

OWNER_NONE: Final[str] = "none"
OWNER_LIVE: Final[str] = "Live"
OWNER_ACQUISITION: Final[str] = "Acquisition"
OWNER_SCAN: Final[str] = "Scan"
OWNER_AUTOFOCUS: Final[str] = "AutoFocus"
OWNER_KINEMATIC: Final[str] = "Kinematic"
OWNER_DEEPALIGN: Final[str] = "DeepAlign"
OWNER_ANALYSIS: Final[str] = "Analysis"

VALID_OWNERS: Final[set[str]] = {
    OWNER_NONE,
    OWNER_LIVE,
    OWNER_ACQUISITION,
    OWNER_SCAN,
    OWNER_AUTOFOCUS,
    OWNER_KINEMATIC,
    OWNER_DEEPALIGN,
    OWNER_ANALYSIS,
}


def normalize_owner(owner: str | None) -> str:
    if owner is None:
        return OWNER_NONE
    value = owner.strip()
    return value if value else OWNER_NONE


def validate_owner(owner: str | None) -> str:
    value = normalize_owner(owner)
    if value not in VALID_OWNERS:
        raise ValueError(f"Unsupported owner: {owner!r}")
    return value
