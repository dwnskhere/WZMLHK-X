"""Assemble one status response from the cached snapshot. No engine/DB/shell work."""
import base64
import time
import uuid

from .models import dumps, error_body, success_envelope
from .util import utc_now_iso

SECTIONS = ("instance", "bot", "status_catalog", "summary", "tasks", "queues", "engines",
            "dependencies", "system", "transfer", "limits", "software", "events", "warnings")


class QueryError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def parse_query(params, settings):
    raw = params.get("sections", "all") or "all"
    if raw.strip().lower() == "all":
        sections = list(SECTIONS)
    else:
        sections = [s.strip() for s in raw.split(",") if s.strip()]
        bad = [s for s in sections if s not in SECTIONS]
        if bad or not sections:
            raise QueryError("INVALID_SECTION", "Unknown section(s): " + ", ".join(bad[:5] or ["(empty)"]))
    def _int(name, lo, hi, default):
        v = params.get(name)
        if v in (None, ""):
            return default
        try:
            n = int(v)
        except ValueError:
            raise QueryError("INVALID_QUERY", f"{name} must be an integer")
        if n < lo or n > hi:
            raise QueryError("INVALID_QUERY", f"{name} must be between {lo} and {hi}")
        return n
    tasks_limit = _int("tasks_limit", 1, settings.max_tasks_per_response, settings.max_tasks_per_response)
    events_limit = _int("events_limit", 0, 500, settings.events_response_limit)
    offset = 0
    cur = params.get("cursor")
    if cur:
        try:
            kind, _, val = base64.urlsafe_b64decode(cur.encode()).decode().partition(":")
            if kind != "t" or int(val) < 0:
                raise ValueError
            offset = int(val)
        except Exception:
            raise QueryError("INVALID_CURSOR", "cursor is invalid")
    return sections, tasks_limit, events_limit, offset


def _cursor(offset):
    return base64.urlsafe_b64encode(f"t:{offset}".encode()).decode()


class StatusService:
    def __init__(self, settings, store, recorder, history, collector):
        self.settings, self.store, self.recorder = settings, store, recorder
        self.history, self.collector = history, collector
        self._cache_key = None
        self._cache_data = None

    def render(self, params, via_proxy=False):
        """Return (http_status, body_bytes). Never raises."""
        rid = uuid.uuid4().hex
        try:
            try:
                sections, tasks_limit, events_limit, offset = parse_query(params, self.settings)
            except QueryError as exc:
                return 400, error_body(exc.code, exc.message, rid)
            snap = self.store.snapshot
            if snap is None:
                return 503, error_body("MONITOR_NOT_READY", "The first monitoring snapshot is not ready yet.", rid)
            data = snap["data"]
            tasks_all = data["tasks"]
            page = tasks_all[offset: offset + tasks_limit]
            nxt = offset + len(page)
            truncated = nxt < len(tasks_all)
            events = self._events(events_limit)
            key = (snap["snapshot_id"], tuple(sections), tasks_limit, offset, events_limit,
                   self.recorder.seq, via_proxy)
            if key != self._cache_key:
                out = {}
                for name in sections:
                    if name == "tasks":
                        out[name] = page
                    elif name == "events":
                        out[name] = events
                    elif name == "bot" and via_proxy:
                        out[name] = {**data["bot"], "web_process": "available"}
                    else:
                        out[name] = data[name]
                self._cache_data, self._cache_key = dumps(out), key
            age = self.store.age_seconds or 0.0
            stale = age > self.settings.snapshot_max_age
            base = snap["meta_base"]
            warnings = list(data["warnings"])
            if stale:
                warnings = warnings + [{"code": "SNAPSHOT_STALE", "section": None, "severity": "warning",
                                        "message": "The latest snapshot is older than the freshness threshold."}]
            meta = {
                "request_id": rid, "snapshot_id": snap["snapshot_id"],
                "generated_at": snap["generated_at"], "served_at": utc_now_iso(time.time()),
                "age_ms": int(age * 1000), "is_stale": stale,
                "is_partial": bool(base["is_partial"] or stale),
                "sections": base["sections"],
                "collection": {**base["collection"], "duration_ms": self.collector.last_duration_ms,
                               "cycles": self.collector.cycle_count},
                "warnings": warnings,
                "pagination": {
                    "tasks": {"total": len(tasks_all), "returned": len(page) if "tasks" in sections else 0,
                              "offset": offset, "truncated": truncated and "tasks" in sections,
                              "next_cursor": _cursor(nxt) if truncated and "tasks" in sections else None},
                    "events": {"returned": len(events["recent"]) if "events" in sections else 0,
                               "limit": events_limit},
                },
            }
            return 200, success_envelope(self._cache_data, meta)
        except Exception:
            return 500, error_body("MONITOR_INTERNAL_ERROR", "The monitoring service failed to build a response.", rid)

    def _events(self, limit):
        rec = self.recorder
        return {
            "recent": rec.recent(limit) if limit else [],
            "history": self.history.describe(),
            "buffer": rec.stats(),
            "event_types_note": "task.stopped is reserved; WZML flags are used to classify cancel vs failure.",
        }
