"""Monitoring-only settings.

Read from environment variables first, then from the deployment's ``config.py``
(same lookup order WZML uses). Nothing here changes any existing WZML setting.
Used by BOTH processes (bot and web), so it must not import ``bot``.
"""
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from importlib import import_module

_TRUE = {"1", "true", "yes", "on", "y"}


def _raw(name, default=None):
    value = os.environ.get(name)
    if value not in (None, ""):
        return value
    try:
        mod = import_module("config")
        value = getattr(mod, name, None)
        if value not in (None, ""):
            return value
    except Exception:
        pass
    return default


def _bool(name, default=False):
    value = _raw(name)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE


def _int(name, default, lo=None, hi=None):
    try:
        value = int(float(_raw(name, default)))
    except (TypeError, ValueError):
        value = default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def _float(name, default, lo=None, hi=None):
    try:
        value = float(_raw(name, default))
    except (TypeError, ValueError):
        value = default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


@dataclass(frozen=True)
class MonitorSettings:
    enabled: bool
    api_token: str
    internal_host: str
    internal_port: int
    internal_secret: str
    snapshot_max_age: float
    max_tasks_per_response: int
    events_response_limit: int
    history_enabled: bool
    history_retention_days: int
    event_queue_size: int
    engine_concurrency: int
    probe_timeout: float
    rate_limit_per_minute: int
    instance_id: str

    @property
    def usable(self):
        """Fail closed: enabled but without public auth means not usable."""
        return bool(self.enabled and self.api_token)


def derive_internal_secret(api_token):
    """Deterministic secret, distinct from the public token, for when none is set.

    Both processes derive the same value from the public token, so no shared
    file or generated state is needed. The public token cannot be recovered
    from it.
    """
    return hmac.new(
        api_token.encode(), b"wzml-monitor-bridge-v1", hashlib.sha256
    ).hexdigest()


def load_settings():
    token = str(_raw("MONITOR_API_TOKEN", "") or "").strip()
    supplied = str(_raw("MONITOR_INTERNAL_SECRET", "") or "").strip()
    if supplied:
        secret = supplied
    elif token:
        secret = derive_internal_secret(token)
    else:
        secret = secrets.token_hex(16)  # unusable anyway (no public token)
    host = str(_raw("MONITOR_INTERNAL_HOST", "127.0.0.1") or "127.0.0.1").strip()
    if host not in ("127.0.0.1", "localhost", "::1"):
        host = "127.0.0.1"  # the private bridge is loopback-only by contract
    return MonitorSettings(
        enabled=_bool("MONITOR_API_ENABLED", False),
        api_token=token,
        internal_host="127.0.0.1" if host == "localhost" else host,
        internal_port=_int("MONITOR_INTERNAL_PORT", 8765, 1024, 65535),
        internal_secret=secret,
        snapshot_max_age=_float("MONITOR_SNAPSHOT_MAX_AGE", 30.0, 5.0, 3600.0),
        max_tasks_per_response=_int("MONITOR_MAX_TASKS_PER_RESPONSE", 500, 1, 5000),
        events_response_limit=_int("MONITOR_EVENTS_RESPONSE_LIMIT", 50, 0, 500),
        history_enabled=_bool("MONITOR_HISTORY_ENABLED", True),
        history_retention_days=_int("MONITOR_HISTORY_RETENTION_DAYS", 30, 1, 3650),
        event_queue_size=_int("MONITOR_EVENT_QUEUE_SIZE", 1000, 10, 100000),
        engine_concurrency=_int("MONITOR_ENGINE_CONCURRENCY", 2, 1, 16),
        probe_timeout=_float("MONITOR_PROBE_TIMEOUT", 5.0, 0.5, 60.0),
        rate_limit_per_minute=_int("MONITOR_RATE_LIMIT_PER_MINUTE", 120, 1, 100000),
        instance_id=str(_raw("MONITOR_INSTANCE_ID", "") or "default").strip()[:64],
    )
