"""Public-process client for the private monitoring bridge.

This runs in the existing FastAPI (gunicorn) process, which must NOT import the
``bot`` package (that would start WZML services). The shared, stdlib-only
settings module is therefore loaded by file path.
"""
import importlib.util
from pathlib import Path

import httpx

_CFG_PATH = Path(__file__).resolve().parent.parent / "bot" / "monitoring" / "config.py"
_ALLOWED = ("sections", "tasks_limit", "events_limit", "cursor")
_client = None


def load_settings():
    spec = importlib.util.spec_from_file_location("_wzml_monitor_config", _CFG_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load_settings()


def _get_client():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=1.0),
                                    limits=httpx.Limits(max_connections=8, max_keepalive_connections=4))
    return _client


async def fetch_snapshot(settings, query):
    """Return (status_code, body_bytes, error_code_or_None). Never raises."""
    params = {k: v for k, v in query.items() if k in _ALLOWED}
    url = f"http://{settings.internal_host}:{settings.internal_port}/internal/v1/snapshot"
    try:
        resp = await _get_client().get(
            url, params=params,
            headers={"X-Monitor-Secret": settings.internal_secret, "X-Monitor-Via": "public-proxy"})
        return resp.status_code, resp.content, None
    except httpx.TimeoutException:
        return 504, b"", "MONITOR_BRIDGE_TIMEOUT"
    except Exception:
        return 503, b"", "MONITOR_BRIDGE_UNAVAILABLE"


async def close_client():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
