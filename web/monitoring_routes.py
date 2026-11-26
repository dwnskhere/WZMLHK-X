"""Authenticated public route: GET /monitor/api/v1/status (read-only)."""
import hmac
import json
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import Response

from web.monitoring_client import fetch_snapshot, load_settings

router = APIRouter()
_SETTINGS = None
_HITS = {}
_MESSAGES = {
    "MONITOR_DISABLED": "The monitoring API is not enabled.",
    "UNAUTHORIZED": "Missing or invalid bearer token.",
    "RATE_LIMITED": "Too many requests.",
    "MONITOR_BRIDGE_UNAVAILABLE": "The monitoring service is currently unavailable.",
    "MONITOR_BRIDGE_TIMEOUT": "The monitoring service did not respond in time.",
}
_RETRYABLE = {"RATE_LIMITED", "MONITOR_BRIDGE_UNAVAILABLE", "MONITOR_BRIDGE_TIMEOUT"}
_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _settings():
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = load_settings()
    return _SETTINGS


def _error(status, code, rid, extra=None):
    body = {
        "success": False, "api_version": "1",
        "error": {"code": code, "message": _MESSAGES.get(code, "Request failed."),
                  "retryable": code in _RETRYABLE, "details": None},
        "meta": {"request_id": rid, "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"},
    }
    return Response(json.dumps(body, separators=(",", ":")), status_code=status,
                    media_type="application/json", headers={**_HEADERS, **(extra or {}), "X-Request-Id": rid})


def _rate_limited(key, limit):
    now = time.monotonic()
    hits = _HITS.get(key)
    if hits is None:
        if len(_HITS) > 1000:
            for k in [k for k, v in _HITS.items() if not v or now - v[-1] > 60][:500]:
                _HITS.pop(k, None)
        hits = _HITS[key] = deque()
    while hits and now - hits[0] > 60:
        hits.popleft()
    if len(hits) >= limit:
        return True
    hits.append(now)
    return False


@router.get("/monitor/api/v1/status")
async def monitor_status(request: Request):
    rid = uuid.uuid4().hex
    try:
        s = _settings()
        if not s.usable:  # disabled, or enabled without a token: fail closed
            return _error(503, "MONITOR_DISABLED", rid)
        client = request.client.host if request.client else "unknown"
        if _rate_limited(client, s.rate_limit_per_minute):
            return _error(429, "RATE_LIMITED", rid, {"Retry-After": "10"})
        auth = request.headers.get("authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip().encode(), s.api_token.encode()):
            return _error(401, "UNAUTHORIZED", rid, {"WWW-Authenticate": "Bearer"})
        status, body, err = await fetch_snapshot(s, dict(request.query_params))
        if err:
            return _error(status, err, rid, {"Retry-After": "5"})
        return Response(body, status_code=status, media_type="application/json",
                        headers={**_HEADERS, "X-Request-Id": rid})
    except Exception:
        return _error(503, "MONITOR_BRIDGE_UNAVAILABLE", rid)
