"""Engine-native raw readers for the 17 WZML status wrappers.

Rules:
  * never call a wrapper's ``update()`` or async ``status()`` (those do one RPC
    per task); engine data comes from ONE aggregate snapshot per engine per cycle
    or from the wrapper's already-cached attributes;
  * never mutate a wrapper or listener;
  * numeric values where the engine supports them, ``None`` otherwise;
  * unknown wrapper classes use a bounded generic serializer.
"""
import asyncio
import time
from inspect import iscoroutinefunction

from .constants import ENGINE_MAP
from .util import num, parse_percent, parse_size, to_int

ARIA2_KEYS = [
    "gid", "status", "totalLength", "completedLength", "downloadSpeed", "uploadSpeed",
    "uploadLength", "numSeeders", "connections", "seeder", "infoHash", "followedBy",
    "bittorrent", "errorCode", "errorMessage",
]
QB_KEYS = [
    "hash", "name", "state", "progress", "size", "downloaded", "uploaded", "dlspeed",
    "upspeed", "eta", "ratio", "num_seeds", "num_leechs", "seeding_time", "tags",
]


class Breaker:
    """Per-engine circuit breaker with exponential backoff."""

    def __init__(self, threshold=3, base=30.0, max_backoff=300.0):
        self.threshold, self.base, self.max_backoff = threshold, base, max_backoff
        self.failures = 0
        self.open_until = 0.0
        self.last_ok = None
        self.last_error = None

    def allow(self):
        return time.monotonic() >= self.open_until

    def success(self):
        self.failures, self.open_until, self.last_ok, self.last_error = 0, 0.0, time.time(), None

    def failure(self, code):
        self.failures += 1
        self.last_error = code
        if self.failures >= self.threshold:
            backoff = min(self.max_backoff, self.base * (2 ** (self.failures - self.threshold)))
            self.open_until = time.monotonic() + backoff

    @property
    def state(self):
        return "open" if not self.allow() else "closed"


class EngineSnapshots:
    def __init__(self):
        self.aria2 = {}
        self.qbit = {}
        self.sabnzbd = {}
        self.global_speed = {}  # engine -> {"download_bps":..,"upload_bps":..}
        self.errors = {}  # engine -> sanitized code
        self.fetched = set()  # engines whose aggregate snapshot succeeded


def _obj_get(obj, name, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _seconds(value):
    if hasattr(value, "total_seconds"):
        try:
            return num(value.total_seconds())
        except Exception:
            return None
    return num(value)


def _qb_to_dict(info):
    out = {k: _obj_get(info, k) for k in QB_KEYS}
    tags = out.get("tags")
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    out["tags"] = list(tags or [])
    return out


async def _bounded(sem, coro, timeout):
    async with sem:
        return await asyncio.wait_for(coro, timeout)


async def fetch_engine_snapshots(state, needed, breakers, sems, timeout):
    """One aggregate read per needed engine. Failures are isolated per engine."""
    snaps = EngineSnapshots()

    async def aria2_job():
        client = state.aria2()
        if client is None:
            raise ConnectionError("not_initialized")
        try:
            act, wait, stat = await _bounded(
                sems["aria2"],
                asyncio.gather(
                    client.tellActive(ARIA2_KEYS),
                    client.tellWaiting(0, 1000, ARIA2_KEYS),
                    client.getGlobalStat(),
                ),
                timeout,
            )
        except TypeError:  # client does not accept key filters
            act, wait, stat = await _bounded(
                sems["aria2"],
                asyncio.gather(client.tellActive(), client.tellWaiting(0, 1000), client.getGlobalStat()),
                timeout,
            )
        snaps.aria2 = {d["gid"]: d for d in [*act, *wait] if isinstance(d, dict) and d.get("gid")}
        snaps.global_speed["aria2"] = {
            "download_bps": to_int(_obj_get(stat, "downloadSpeed"), 0),
            "upload_bps": to_int(_obj_get(stat, "uploadSpeed"), 0),
        }

    async def qbit_job():
        client = state.qbittorrent()
        if client is None:
            raise ConnectionError("not_initialized")
        infos, tinfo = await _bounded(
            sems["qbittorrent"], asyncio.gather(client.torrents.info(), client.transfer.info()), timeout
        )
        for info in infos or []:
            d = _qb_to_dict(info)
            for tag in d["tags"]:
                snaps.qbit[str(tag)] = d
            if d.get("hash"):
                snaps.qbit.setdefault(f"hash:{d['hash']}", d)
        snaps.global_speed["qbittorrent"] = {
            "download_bps": to_int(_obj_get(tinfo, "dl_info_speed"), 0),
            "upload_bps": to_int(_obj_get(tinfo, "up_info_speed"), 0),
        }

    async def sab_job():
        client = state.sabnzbd()
        if client is None:
            raise ConnectionError("not_initialized")
        res = await _bounded(sems["sabnzbd"], client.get_downloads(), timeout)
        queue = (res or {}).get("queue", {})
        snaps.sabnzbd = {s.get("nzo_id"): s for s in queue.get("slots", []) if s.get("nzo_id")}
        kb = num(queue.get("kbpersec"), 0) or 0
        snaps.global_speed["sabnzbd"] = {"download_bps": int(kb * 1024), "upload_bps": 0}

    jobs = {"aria2": aria2_job, "qbittorrent": qbit_job, "sabnzbd": sab_job}
    names, coros = [], []
    for name, job in jobs.items():
        if name in needed and breakers[name].allow():
            names.append(name)
            coros.append(job())
        elif name in needed:
            snaps.errors[name] = "circuit_open"
    for name, res in zip(names, await asyncio.gather(*coros, return_exceptions=True)):
        if isinstance(res, BaseException):
            code = "timeout" if isinstance(res, asyncio.TimeoutError) else (
                "not_initialized" if str(res) == "not_initialized" else "probe_failed")
            snaps.errors[name] = code
            if code != "not_initialized":
                breakers[name].failure(code)
        else:
            breakers[name].success()
            snaps.fetched.add(name)
    return snaps


# ---------------------------------------------------------------------------
# per-wrapper readers -> raw dict
# ---------------------------------------------------------------------------

def _blank():
    return {
        "wzml_status": None, "engine_status": None, "processed": None, "total": None,
        "percent": None, "dl_bps": None, "ul_bps": None, "seed_bps": None, "eta": None,
        "gid": None, "hash": None, "torrent": None, "source": "wrapper_attributes",
        "available": True, "extensions": {},
    }


def _eta_from(total, processed, speed):
    if total is None or processed is None or not speed:
        return None
    left = max(total - processed, 0)
    return int(left / speed)


def _sab_eta(text):
    try:
        parts = [int(float(p)) for p in str(text).split(":")]
    except Exception:
        return None
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return sec


def read_aria2(w, snaps, listener):
    raw = _blank()
    gid = _safe_call(w, "gid")
    raw["gid"] = gid
    info = snaps.aria2.get(gid)
    if info and info.get("followedBy"):
        nxt = snaps.aria2.get(info["followedBy"][0])
        if nxt:
            info = nxt
    raw["source"] = "engine_snapshot" if info else "wrapper_cache"
    info = info or getattr(w, "_download", None) or {}
    if not info:
        raw["available"] = False
    seeding = bool(getattr(w, "seeding", False))
    status = info.get("status")
    raw["engine_status"] = status
    if info:
        if status == "waiting" or getattr(w, "queued", False):
            raw["wzml_status"] = "QueueUp" if seeding else "QueueDl"
        elif status == "paused":
            raw["wzml_status"] = "Pause"
        elif info.get("seeder") == "true" and seeding:
            raw["wzml_status"] = "Seed"
        else:
            raw["wzml_status"] = "Download"
    elif getattr(w, "queued", False):
        raw["wzml_status"] = "QueueUp" if seeding else "QueueDl"
    total, done = to_int(info.get("totalLength")), to_int(info.get("completedLength"))
    dls, uls = to_int(info.get("downloadSpeed")), to_int(info.get("uploadSpeed"))
    raw.update(total=total, processed=done)
    raw["percent"] = round(done / total * 100, 2) if total and done is not None else None
    raw["hash"] = info.get("infoHash") or None
    if seeding and raw["wzml_status"] == "Seed":
        raw["seed_bps"] = uls
    else:
        raw["dl_bps"], raw["ul_bps"] = dls, uls
    raw["eta"] = _eta_from(total, done, dls)
    up_len = to_int(info.get("uploadLength"))
    start = getattr(w, "start_time", 0) or 0
    raw["torrent"] = {
        "is_torrent": bool("bittorrent" in info or seeding or getattr(listener, "is_torrent", False)),
        "hash": raw["hash"],
        "seeders": to_int(info.get("numSeeders")),
        "leechers": to_int(info.get("connections")),
        "uploaded_bytes": up_len,
        "ratio": round(up_len / done, 3) if up_len is not None and done else (0 if done == 0 else None),
        "seeding_seconds": int(time.time() - start) if seeding and start else None,
    }
    return raw


def read_qbit(w, snaps, listener):
    raw = _blank()
    mid = str(getattr(listener, "mid", ""))
    info = snaps.qbit.get(mid)
    raw["source"] = "engine_snapshot" if info else "wrapper_cache"
    if not info and getattr(w, "_info", None) is not None:
        info = _qb_to_dict(w._info)
    if not info:
        raw["available"] = False
        info = {}
    state = info.get("state")
    seeding = bool(getattr(w, "seeding", False))
    queued = bool(getattr(w, "queued", False))
    raw["engine_status"] = str(state) if state is not None else None
    if state is not None or queued:
        s = str(state)
        if s == "queuedDL" or queued:
            raw["wzml_status"] = "QueueDl"
        elif s == "queuedUP":
            raw["wzml_status"] = "QueueUp"
        elif s in ("stoppedDL", "stoppedUP", "pausedDL", "pausedUP"):
            raw["wzml_status"] = "Pause"
        elif s in ("checkingUP", "checkingDL"):
            raw["wzml_status"] = "CheckUp"
        elif s in ("stalledUP", "uploading") and seeding:
            raw["wzml_status"] = "Seed"
        else:
            raw["wzml_status"] = "Download"
    total, done = to_int(info.get("size")), to_int(info.get("downloaded"))
    prog = num(info.get("progress"))
    raw.update(total=total, processed=done)
    raw["percent"] = round(prog * 100, 2) if prog is not None else None
    dls, uls = to_int(info.get("dlspeed")), to_int(info.get("upspeed"))
    if raw["wzml_status"] == "Seed":
        raw["seed_bps"] = uls
    else:
        raw["dl_bps"], raw["ul_bps"] = dls, uls
    raw["eta"] = _seconds(info.get("eta"))
    if raw["eta"] is not None and raw["eta"] >= 8640000:  # qBittorrent "infinity"
        raw["eta"] = None
    raw["hash"] = info.get("hash") or None
    raw["torrent"] = {
        "is_torrent": True,
        "hash": raw["hash"],
        "seeders": to_int(info.get("num_seeds")),
        "leechers": to_int(info.get("num_leechs")),
        "uploaded_bytes": to_int(info.get("uploaded")),
        "ratio": num(info.get("ratio")),
        "seeding_seconds": to_int(_seconds(info.get("seeding_time"))),
    }
    return raw


def read_sabnzbd(w, snaps, listener):
    raw = _blank()
    gid = _safe_call(w, "gid")
    raw["gid"] = gid
    slot = snaps.sabnzbd.get(gid)
    raw["source"] = "engine_snapshot" if slot else "wrapper_cache"
    slot = slot or dict(getattr(w, "_info", None) or {})
    if not slot:
        raw["available"] = False
    mb, left = num(slot.get("mb")), num(slot.get("mbleft"))
    state = slot.get("status")
    raw["engine_status"] = str(state) if state else None
    if slot:
        if slot.get("mb", "0") == slot.get("mbleft", "0"):
            raw["wzml_status"] = "QueueDl"
        elif state == "Paused" and getattr(w, "queued", False):
            raw["wzml_status"] = "QueueDl"
        elif state in ("QuickCheck", "Verifying", "Repairing", "Fetching", "Moving", "Extracting"):
            raw["wzml_status"] = state  # engine-native value, kept exactly
        else:
            raw["wzml_status"] = "Download"
    if mb is not None:
        raw["total"] = int(mb * 1048576)
        if left is not None:
            raw["processed"] = int((mb - left) * 1048576)
    raw["percent"] = parse_percent(slot.get("percentage"))
    eta = _sab_eta(slot.get("timeleft"))
    raw["eta"] = eta
    if left is not None and eta:
        raw["dl_bps"] = int(left * 1048576 / eta)
    return raw


def read_jd(w, snaps, listener):
    raw = _blank()
    info = getattr(w, "_info", None) or {}
    raw["source"] = "wrapper_cache"
    raw["gid"] = _safe_call(w, "gid")
    if not info:
        raw["available"] = False
    state = str(info.get("status", "jdlimit")).capitalize()
    raw["engine_status"] = info.get("status")
    loaded, total = to_int(info.get("bytesLoaded")), to_int(info.get("bytesTotal"))
    if len(state) == 0:
        raw["wzml_status"] = "QueueDl" if not loaded else "Download"
    elif state == "Jdlimit":
        raw["wzml_status"] = "QueueDl"
    else:
        raw["wzml_status"] = state
    raw.update(processed=loaded, total=total, dl_bps=to_int(info.get("speed")), eta=to_int(info.get("eta")))
    raw["percent"] = round(loaded / total * 100, 2) if total and loaded is not None else None
    return raw


def read_seedr(w, snaps, listener):
    raw = _blank()
    info = getattr(w, "_info", None) or {}
    raw["source"] = "wrapper_cache"
    raw["gid"] = _safe_call(w, "gid")
    if not info:
        raw["available"] = False
    raw["wzml_status"] = "QueueDl" if info.get("is_queued") else "Download"
    size = num(info.get("size"))
    prog = num(info.get("progress"), 0)
    raw["total"] = to_int(size)
    raw["percent"] = round(prog, 2) if info else None
    raw["processed"] = int(size * prog / 100) if size and info else None
    raw["dl_bps"] = to_int(info.get("speed"))
    raw["eta"] = to_int(info.get("eta")) or None
    return raw


def _generic_obj(raw, w, listener, proc, speed, total=None, pct=None, eta=None, obj_name="_obj",
                 direction=None):
    obj = getattr(w, obj_name, None) if obj_name else w

    def g(attr):
        return None if attr is None else num(getattr(obj, attr, None))

    raw["processed"] = to_int(g(proc))
    spd = to_int(g(speed))
    raw["total"] = to_int(total if total is not None else getattr(listener, "size", None))
    if pct is not None:
        raw["percent"] = parse_percent(getattr(obj, pct, None))
    elif raw["total"] and raw["processed"] is not None:
        raw["percent"] = round(raw["processed"] / raw["total"] * 100, 2)
    if eta is not None:
        e = getattr(obj, eta, None)
        raw["eta"] = to_int(e) if not isinstance(e, str) or e.replace(".", "", 1).isdigit() else None
    else:
        raw["eta"] = _eta_from(raw["total"], raw["processed"], spd)
    raw["_speed"] = spd


def _apply_direction(raw):
    spd = raw.pop("_speed", None)
    st = raw.get("wzml_status")
    if spd is None:
        return
    if st == "Upload" or st == "Clone":
        raw["ul_bps"] = spd
    elif st == "Download":
        raw["dl_bps"] = spd
    else:  # archive/extract/ffmpeg etc.: processing rate, not network transfer
        raw["extensions"]["processing_bps"] = spd


# class name -> (obj attr, processed attr, speed attr, total expr, pct attr, eta attr)
_SYNC = {
    "DirectStatus": ("_obj", "processed_bytes", "speed", None, None, None),
    "GoogleDriveStatus": ("_obj", "processed_bytes", "speed", "_size", None, None),
    "TelegramStatus": ("_obj", "processed_bytes", "speed", "_size", None, None),
    "UphosterStatus": ("_obj", "processed_bytes", "speed", "_size", None, None),
    "YtStatus": ("_obj", "processed_bytes", "speed", "_size", None, None),
    "YtDlpStatus": ("_obj", "downloaded_bytes", "download_speed", "size_obj", "progress", "eta"),
    "FFmpegStatus": ("_obj", "processed_bytes", "speed_raw", None, "progress_raw", "eta_raw"),
    "SevenZStatus": ("_obj", "processed_bytes", None, None, "progress", None),
    "MetadataStatus": ("_obj", "processed_bytes", "speed_raw", None, "progress_raw", "eta_raw"),
}


def read_sync_wrapper(w, snaps, listener):
    raw = _blank()
    raw["wzml_status"] = _safe_call(w, "status", sync_only=True)
    raw["gid"] = _safe_call(w, "gid")
    name = type(w).__name__
    obj_name, proc, speed, total_src, pct, eta = _SYNC[name]
    obj = getattr(w, obj_name, None)
    total = None
    if total_src == "_size":
        total = getattr(w, "_size", None)
    elif total_src == "size_obj":
        total = getattr(obj, "size", None)
    if name in ("FFmpegStatus", "SevenZStatus"):
        total = getattr(listener, "subsize", None) or getattr(listener, "size", None)
    _generic_obj(raw, w, listener, proc, speed, total=total, pct=pct, eta=eta)
    _apply_direction(raw)
    return raw


def read_mega(w, snaps, listener):
    raw = _blank()
    raw["wzml_status"] = getattr(w, "_status", None) or None
    raw["gid"] = _safe_call(w, "gid")
    done, spd, size = getattr(w, "_downloaded_bytes", None), getattr(w, "_speed", None), getattr(w, "_size", None)
    raw.update(processed=to_int(done), total=to_int(size))
    raw["percent"] = round(done / size * 100, 2) if size and done is not None else None
    raw["eta"] = _eta_from(raw["total"], raw["processed"], num(spd))
    raw["_speed"] = to_int(spd)
    if raw["wzml_status"] is None:
        raw["wzml_status"] = "Download"
    _apply_direction(raw)
    return raw


def read_rclone(w, snaps, listener):
    raw = _blank()
    raw["wzml_status"] = _safe_call(w, "status", sync_only=True)
    raw["gid"] = _safe_call(w, "gid")
    obj = getattr(w, "_obj", None)
    raw["total"] = parse_size(getattr(obj, "size", None))
    raw["processed"] = parse_size(getattr(obj, "transferred_size", None))
    raw["percent"] = parse_percent(getattr(obj, "percentage", None))
    raw["_speed"] = parse_size(getattr(obj, "speed", None))
    raw["eta"] = None
    _apply_direction(raw)
    return raw


def read_queue(w, snaps, listener):
    raw = _blank()
    raw["wzml_status"] = _safe_call(w, "status", sync_only=True)
    raw["gid"] = _safe_call(w, "gid")
    raw["total"] = to_int(getattr(listener, "size", None))
    raw["processed"], raw["percent"] = 0, 0
    return raw


def read_generic(w, snaps, listener):
    """Bounded fallback for wrapper classes this adapter set does not know."""
    raw = _blank()
    raw["source"] = "generic"
    if not iscoroutinefunction(getattr(w, "status", None)):
        raw["wzml_status"] = _safe_call(w, "status", sync_only=True)
    raw["gid"] = _safe_call(w, "gid")
    raw["total"] = parse_size(_safe_call(w, "size", sync_only=True))
    raw["processed"] = parse_size(_safe_call(w, "processed_bytes", sync_only=True))
    raw["percent"] = parse_percent(_safe_call(w, "progress", sync_only=True))
    raw["_speed"] = parse_size(_safe_call(w, "speed", sync_only=True))
    _apply_direction(raw)
    return raw


def _safe_call(obj, name, sync_only=False):
    fn = getattr(obj, name, None)
    if fn is None or not callable(fn) or iscoroutinefunction(fn):
        return None
    try:
        out = fn()
        return None if iscoroutinefunction(out) else out
    except Exception:
        return None


READERS = {
    "Aria2Status": read_aria2,
    "QbittorrentStatus": read_qbit,
    "SabnzbdStatus": read_sabnzbd,
    "JDownloaderStatus": read_jd,
    "SeedrStatus": read_seedr,
    "MegaDownloadStatus": read_mega,
    "RcloneStatus": read_rclone,
    "QueueStatus": read_queue,
    **{name: read_sync_wrapper for name in _SYNC},
}
assert set(ENGINE_MAP) <= set(READERS), set(ENGINE_MAP) - set(READERS)


def read_wrapper(wrapper, snaps, listener):
    reader = READERS.get(type(wrapper).__name__, read_generic)
    return reader(wrapper, snaps, listener)
