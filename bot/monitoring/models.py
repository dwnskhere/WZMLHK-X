"""Common response envelope and error shapes (spec 11)."""
import json
import uuid

from .constants import API_VERSION, SCHEMA_VERSION
from .util import utc_now_iso

ERROR_STATUS = {
    "INVALID_QUERY": 400, "INVALID_SECTION": 400, "INVALID_CURSOR": 400,
    "UNAUTHORIZED": 401, "RATE_LIMITED": 429,
    "MONITOR_NOT_READY": 503, "MONITOR_DISABLED": 503, "MONITOR_BRIDGE_UNAVAILABLE": 503,
    "MONITOR_BRIDGE_TIMEOUT": 504, "MONITOR_INTERNAL_ERROR": 500,
}
RETRYABLE = {"RATE_LIMITED", "MONITOR_NOT_READY", "MONITOR_BRIDGE_UNAVAILABLE", "MONITOR_BRIDGE_TIMEOUT"}


def dumps(obj):
    return json.dumps(obj, separators=(",", ":"), default=str, allow_nan=False).encode()


def error_body(code, message, request_id=None, details=None):
    return dumps({
        "success": False,
        "api_version": API_VERSION,
        "error": {"code": code, "message": message, "retryable": code in RETRYABLE, "details": details},
        "meta": {"request_id": request_id or uuid.uuid4().hex, "generated_at": utc_now_iso()},
    })


def success_envelope(data_bytes, meta):
    head = ('{"success":true,"api_version":"%s","schema_version":"%s","data":' % (API_VERSION, SCHEMA_VERSION)).encode()
    return head + data_bytes + b',"meta":' + dumps(meta) + b"}"
