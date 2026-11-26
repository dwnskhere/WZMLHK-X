"""Lifecycle observer hook called from WZML's TaskListener.

Contract (spec 14): synchronous, instant, no I/O, never waits, never raises,
never changes the callback's decision. When monitoring is disabled or failed,
it is a cheap no-op.
"""
import re
import time

from .identity import REGISTRY
from .util import clean_text, num

_RECORDER = None

_URL = re.compile(r"https?://\S+", re.I)
_PATH = re.compile(r"(?<![\w])/(?:[\w.\-+@ ]+/)+[\w.\-+@]*")
_TAG = re.compile(r"<[^>]{1,200}>")


def set_recorder(recorder):
    global _RECORDER
    _RECORDER = recorder


def sanitize_error(error, limit=200):
    text = _TAG.sub("", str(error) if error is not None else "")
    text = _URL.sub("<url>", text)
    text = _PATH.sub("<path>", text)
    return clean_text(text, limit)


def classify_error(listener, error, is_limit=False):
    """Use WZML's own flags as the authority; do not guess from wording."""
    if getattr(listener, "is_cancelled", False):
        return "task.cancelled", "cancelled"
    text = str(error) if error is not None else ""
    if is_limit:
        return "task.rejected", "limit_breached"
    if text.startswith("File/Folder is already available"):
        return "task.rejected", "duplicate"
    return "task.failed", "error"


def record_lifecycle_event(listener, event_type, stage=None, outcome=None,
                           error=None, is_limit=False, **extra):
    recorder = _RECORDER
    if recorder is None:
        return
    try:
        if event_type == "task.error":
            event_type, category = classify_error(listener, error, is_limit)
            outcome = outcome or category
        now = time.time()
        key = getattr(listener, "mid", None)
        entry = REGISTRY.ensure(key)
        message = getattr(listener, "message", None)
        chat = getattr(message, "chat", None)
        started = getattr(listener, "download_start_time", None) or entry["first_seen"]
        event = {
            "task_id": entry["task_id"],
            "task_key": str(key),
            "event_type": event_type,
            "time": None,
            "ts_epoch": now,
            "stage": stage,
            "outcome": outcome,
            "origin": {
                "platform": "telegram",
                "chat_id": getattr(chat, "id", None),
                "message_id": getattr(message, "id", None),
            },
            "name": clean_text(getattr(listener, "name", None)),
            "metrics": {
                "size_bytes": num(getattr(listener, "size", None)),
                "duration_seconds": round(now - started, 2) if started else None,
                "avg_download_bps": num(getattr(listener, "avg_download_speed", None)),
                "avg_upload_bps": num(getattr(listener, "avg_upload_speed", None)),
            },
        }
        from .util import utc_now_iso

        event["time"] = utc_now_iso(now)
        if error is not None:
            event["error"] = {"category": outcome, "code": event_type.split(".")[-1].upper(),
                              "message": sanitize_error(error)}
        event.update({k: v for k, v in extra.items() if isinstance(v, (str, int, float, bool, type(None)))})
        recorder.record(event)
    except Exception:
        return
