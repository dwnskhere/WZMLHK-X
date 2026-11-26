"""Normalize one WZML task into the API task record. Pure, per-task isolated."""
import re

from .constants import (
    ENGINE_MAP, INPUT_MODE, NATIVE_STATUS_STAGE, OUTPUT_MODE, WZML_STATUS_STAGE,
)
from .engine_adapters import read_wrapper
from .identity import BOOT_ID, REGISTRY
from .util import camel_to_snake, clean_text, num, to_int, utc_now_iso

_VER = re.compile(r"\sv([\w.+\-]+)\s*(?:\(.*\))?$")
_PAUSED_NATIVE = {"paused", "stoppeddl", "stoppedup", "pauseddl", "pausedup"}


def engine_identity(wrapper):
    cls = type(wrapper).__name__
    if cls in ENGINE_MAP:
        eid, label, caps = ENGINE_MAP[cls]
    else:
        eid, label, caps = camel_to_snake(cls.replace("Status", "")) or "unknown", cls, ()
    version = None
    label_str = getattr(wrapper, "engine", None)
    if isinstance(label_str, str):
        m = _VER.search(label_str)
        if m and m.group(1).upper() != "N/A":
            version = m.group(1)
    return eid, label, version, caps


def normalize_stage(wzml_status, engine_status=None):
    """Return (stage, is_known). Unknown raw values map to ``other``."""
    if wzml_status in WZML_STATUS_STAGE:
        return WZML_STATUS_STAGE[wzml_status], True
    for candidate in (wzml_status, engine_status):
        if candidate in NATIVE_STATUS_STAGE:
            return NATIVE_STATUS_STAGE[candidate], True
    if wzml_status in (None, ""):
        return "unknown", False
    return "other", False


def derive_lifecycle(wzml_status, engine_status):
    if wzml_status in (None, ""):
        return "unknown"
    if wzml_status in ("QueueDl", "QueueUp"):
        return "queued"
    if wzml_status == "Pause" or str(engine_status or "").lower() in _PAUSED_NATIVE:
        return "paused"
    return "running"


def normalize_mode(raw, table):
    if not raw:
        return None
    key = str(raw).lstrip("#").lower()
    return table.get(key, key)


def _epoch(value):
    try:
        return float(value.timestamp())
    except Exception:
        return num(value)


def _processing(listener, stage):
    files = getattr(listener, "files_to_proceed", None)
    count = len(files) if isinstance(files, (list, tuple, set, dict)) else None
    sub = clean_text(getattr(listener, "subname", None)) or None
    return {
        "operation": stage if stage in (
            "archiving", "extracting", "splitting", "sample_video", "converting",
            "ffmpeg_processing", "youtube_processing", "metadata_processing", "checking",
        ) else None,
        "current_item": sub,
        "file_count": count,
        "completed_files": to_int(getattr(listener, "proceed_count", None)) if count else None,
        "current_sub_name": sub,
    }


def serialize_task(key, wrapper, snaps, now, instance_id, queue_pos=None, health=None, seen_statuses=None):
    listener = getattr(wrapper, "listener", None)
    entry = REGISTRY.ensure(key)
    eid, label, version, caps = engine_identity(wrapper)
    raw = read_wrapper(wrapper, snaps, listener)
    stage, known = normalize_stage(raw["wzml_status"], raw["engine_status"])
    lifecycle = derive_lifecycle(raw["wzml_status"], raw["engine_status"])
    if seen_statuses is not None:
        for v in (raw["wzml_status"], raw["engine_status"]):
            if v and len(seen_statuses) < 200:
                seen_statuses.add(str(v))

    message = getattr(listener, "message", None)
    chat = getattr(message, "chat", None)
    mode = getattr(listener, "mode", None) or ()
    in_raw = mode[0] if len(mode) > 0 else None
    out_raw = mode[1] if len(mode) > 1 else None

    started = num(getattr(listener, "download_start_time", None)) or entry["first_seen"]
    msg_ts = _epoch(getattr(message, "date", None))
    elapsed_base = msg_ts or started
    total, processed = raw["total"], raw["processed"]
    remaining = max(total - processed, 0) if total is not None and processed is not None else None

    torrent = raw["torrent"] or {
        "is_torrent": bool(getattr(listener, "is_torrent", False)),
        "hash": None, "seeders": None, "leechers": None, "uploaded_bytes": None,
        "ratio": None, "seeding_seconds": None,
    }
    available = []
    for name, val in (("progress", raw["percent"]), ("bytes", processed), ("speed", raw["dl_bps"] if raw["dl_bps"] is not None else raw["ul_bps"] if raw["ul_bps"] is not None else raw["seed_bps"]),
                      ("eta", raw["eta"])):
        if val is not None:
            available.append(name)

    quality = ("engine_snapshot" if raw["source"] == "engine_snapshot" else
               "cached" if raw["source"] == "wrapper_cache" and raw["available"] else
               "unavailable" if not raw["available"] else "wrapper_attributes")
    ext = dict(raw["extensions"])
    ext_out = {eid: ext} if ext else {}

    return {
        "task_id": entry["task_id"],
        "task_key": str(key),
        "instance_id": instance_id,
        "boot_id": BOOT_ID,
        "origin": {
            "platform": "telegram",
            "chat_id": getattr(chat, "id", None),
            "message_id": getattr(message, "id", None),
        },
        "name": clean_text(getattr(listener, "name", None)) or None,
        "sub_name": clean_text(getattr(listener, "subname", None)) or None,
        "input_mode": {"raw": in_raw, "normalized": normalize_mode(in_raw, INPUT_MODE)},
        "output_mode": {"raw": out_raw, "normalized": normalize_mode(out_raw, OUTPUT_MODE)},
        "engine": {
            "id": eid,
            "label": label,
            "version": version,
            "task_id": clean_text(raw["gid"], 100) if raw["gid"] else None,
            "hash": raw["hash"],
            "health": (health or {}).get(eid, "unknown"),
        },
        "state": {
            "lifecycle": lifecycle,
            "stage": stage,
            "wzml_status": raw["wzml_status"],
            "engine_status": raw["engine_status"],
            "normalized_status": stage,
            "cancel_requested": bool(getattr(listener, "is_cancelled", False)),
            "known_status": known,
        },
        "progress": {
            "percent": raw["percent"],
            "processed_bytes": processed,
            "total_bytes": total,
            "remaining_bytes": remaining,
            "files": {
                "processed": to_int(getattr(listener, "proceed_count", None)) if getattr(listener, "files_to_proceed", None) else None,
                "total": len(listener.files_to_proceed) if isinstance(getattr(listener, "files_to_proceed", None), (list, tuple, set)) and listener.files_to_proceed else None,
            },
        },
        "transfer": {
            "download_bps": raw["dl_bps"],
            "upload_bps": raw["ul_bps"],
            "seeding_bps": raw["seed_bps"],
            "source": raw["source"],
            "quality": quality,
        },
        "timing": {
            "started_at": utc_now_iso(started) if started else None,
            "updated_at": utc_now_iso(now),
            "elapsed_seconds": round(now - elapsed_base, 1) if elapsed_base else None,
            "eta_seconds": raw["eta"],
            "queue_wait_seconds": None,
        },
        "torrent": torrent,
        "processing": _processing(listener, stage),
        "queue_position": queue_pos,
        "capabilities": sorted(set(caps) & ({"engine_gid", "engine_hash", "torrent", "seeding"} | set(available)) | set(available)),
        "extensions": ext_out,
    }


def minimal_failed_record(key, instance_id, now):
    entry = REGISTRY.ensure(key)
    return {
        "task_id": entry["task_id"], "task_key": str(key), "instance_id": instance_id,
        "boot_id": BOOT_ID, "origin": {"platform": "telegram", "chat_id": None, "message_id": None},
        "name": None, "sub_name": None,
        "input_mode": {"raw": None, "normalized": None},
        "output_mode": {"raw": None, "normalized": None},
        "engine": {"id": "unknown", "label": None, "version": None, "task_id": None, "hash": None, "health": "unknown"},
        "state": {"lifecycle": "unknown", "stage": "unknown", "wzml_status": None,
                  "engine_status": None, "normalized_status": "unknown",
                  "cancel_requested": False, "known_status": False},
        "progress": {"percent": None, "processed_bytes": None, "total_bytes": None,
                     "remaining_bytes": None, "files": {"processed": None, "total": None}},
        "transfer": {"download_bps": None, "upload_bps": None, "seeding_bps": None,
                     "source": "unavailable", "quality": "unavailable"},
        "timing": {"started_at": utc_now_iso(entry["first_seen"]), "updated_at": utc_now_iso(now),
                   "elapsed_seconds": None, "eta_seconds": None, "queue_wait_seconds": None},
        "torrent": {"is_torrent": False, "hash": None, "seeders": None, "leechers": None,
                    "uploaded_bytes": None, "ratio": None, "seeding_seconds": None},
        "processing": {"operation": None, "current_item": None, "file_count": None,
                       "completed_files": None, "current_sub_name": None},
        "queue_position": None, "capabilities": [], "extensions": {}, "serialize_error": True,
    }
