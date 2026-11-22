"""Read queue membership/slot information. Reads keys only; never mutates."""
from .task_collector import copy_under_lock
from .util import to_int


async def collect_queue_refs(state, timeout=0.5):
    def _copy():
        return {
            "queued_dl": list(state.queued_dl().keys()),
            "queued_up": list(state.queued_up().keys()),
            "active_dl": set(state.non_queued_dl()),
            "active_up": set(state.non_queued_up()),
        }

    refs, wait_ms, used_lock = await copy_under_lock(state.queue_dict_lock(), _copy, timeout)
    refs["lock_wait_ms"] = wait_ms
    refs["lock_used"] = used_lock
    return refs


def _cap(config, name):
    value = to_int(getattr(config, name, 0) if config else 0, 0) or 0
    return value if value > 0 else None


def build_queues(refs, config, tasks_by_key):
    """Assemble the ``queues`` section. Queue items enrich task records only."""
    all_cap = _cap(config, "QUEUE_ALL")
    caps = {"download": _cap(config, "QUEUE_DOWNLOAD"), "upload": _cap(config, "QUEUE_UPLOAD")}
    active = {"download": len(refs["active_dl"]), "upload": len(refs["active_up"])}
    queued = {"download": refs["queued_dl"], "upload": refs["queued_up"]}
    stage = {"download": "download_queue", "upload": "upload_queue"}
    raw = {"download": "QueueDl", "upload": "QueueUp"}
    out = {}
    for kind in ("download", "upload"):
        slots = None
        limits = []
        if caps[kind] is not None:
            limits.append(caps[kind] - active[kind])
        if all_cap is not None:
            limits.append(all_cap - active["download"] - active["upload"])
        if limits:
            slots = max(0, min(limits))
        items = []
        for pos, key in enumerate(queued[kind], start=1):
            task = tasks_by_key.get(str(key))
            items.append({
                "task_id": task["task_id"] if task else None,
                "task_key": str(key),
                "position": pos,
                "name": task["name"] if task else None,
                "wzml_status": task["state"]["wzml_status"] if task else raw[kind],
                "intended_stage": stage[kind],
                "enqueued_at": None,
                "queue_age_seconds": None,
            })
        out[kind] = {
            "length": len(items),
            "active": active[kind],
            "capacity": caps[kind],
            "unlimited": caps[kind] is None and all_cap is None,
            "available_slots": slots,
            "items": items,
        }
    out["overall_capacity"] = all_cap
    return out
