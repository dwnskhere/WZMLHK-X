"""Allowlisted effective limits. The whole Config object is never serialized."""
from .constants import LIMIT_KEYS
from .util import num


def collect_limits(config):
    out = {}
    for key, (unit, scope, note) in LIMIT_KEYS.items():
        value = num(getattr(config, key, None)) if config is not None else None
        out[key] = {"value": value, "unit": unit, "scope": scope, "source": "config",
                    "unlimited_or_disabled": (value == 0) if value is not None else None, "note": note}
    return out
