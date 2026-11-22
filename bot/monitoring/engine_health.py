"""Report configured/enabled/initialized/connected per engine from existing managers.

Probes are read-only and bounded; they never launch or restart an engine. Binary
availability (dependency section) is intentionally separate from connectivity.
"""
import asyncio

from .util import utc_now_iso

ENGINES = (
    ("aria2", "Aria2"), ("qbittorrent", "qBittorrent"), ("sabnzbd", "SABnzbd"),
    ("jdownloader", "JDownloader"), ("mega", "MEGA"), ("seedr", "Seedr"),
    ("rclone", "Rclone"), ("ffmpeg", "FFmpeg"), ("sevenz", "7-Zip"), ("yt_dlp", "yt-dlp"),
    ("gdrive_api", "Google Drive API"), ("telegram", "Telegram"), ("uphoster", "Uphoster"),
    ("direct", "Direct link"), ("youtube_api", "YouTube API"), ("metadata", "Metadata"),
    ("queue_system", "Queue system"),
)
_DEP_FOR = {"rclone": "rclone", "ffmpeg": "ffmpeg", "sevenz": "sevenz", "mega": "megacmd"}
_REQUIRED_BY = {
    "aria2": ["direct links", "torrent metadata"], "qbittorrent": ["torrents"],
    "sabnzbd": ["usenet"], "jdownloader": ["jdownloader"], "mega": ["mega links"],
    "seedr": ["seedr"], "rclone": ["rclone transfers"], "ffmpeg": ["media processing"],
    "sevenz": ["archive/extract"], "telegram": ["all tasks"],
}


async def _probe(coro, timeout):
    try:
        await asyncio.wait_for(coro, timeout)
        return True, None
    except asyncio.TimeoutError:
        return False, "TIMEOUT"
    except Exception as exc:
        return False, type(exc).__name__.upper()[:40]


async def collect_engine_health(state, deps, versions, timeout, breakers, sems, needed_enabled=None):
    cfg = state.config()
    now = utc_now_iso()
    dep_by = {d["id"]: d for d in deps}
    out = []

    async def aria2():
        c = state.aria2()
        if c is None:
            return dict(initialized=False, connected=False, err=("NOT_INITIALIZED", "Aria2 client is not initialized"))
        if not breakers["aria2"].allow():
            return dict(initialized=True, connected=False, err=("CIRCUIT_OPEN", "Aria2 probes are backing off"))
        ok, code = await _probe(c.getVersion(), timeout)
        (breakers["aria2"].success if ok else (lambda: breakers["aria2"].failure(code)))()
        return dict(initialized=True, connected=ok, err=None if ok else (code, "Aria2 RPC probe failed"))

    async def qbit():
        c = state.qbittorrent()
        if getattr(cfg, "DISABLE_TORRENTS", False):
            return dict(enabled=False, initialized=False, connected=False)
        if c is None:
            return dict(initialized=False, connected=False, err=("NOT_INITIALIZED", "qBittorrent client is not initialized"))
        if not breakers["qbittorrent"].allow():
            return dict(initialized=True, connected=False, err=("CIRCUIT_OPEN", "qBittorrent probes are backing off"))
        ok, code = await _probe(c.transfer.info(), timeout)
        (breakers["qbittorrent"].success if ok else (lambda: breakers["qbittorrent"].failure(code)))()
        return dict(initialized=True, connected=ok, err=None if ok else (code, "qBittorrent API probe failed"))

    async def sab():
        c = state.sabnzbd()
        if c is None:
            return dict(initialized=False, connected=False, err=("NOT_INITIALIZED", "SABnzbd client is not initialized"))
        if not breakers["sabnzbd"].allow():
            return dict(initialized=True, connected=False, err=("CIRCUIT_OPEN", "SABnzbd probes are backing off"))
        ok, code = await _probe(c.check_login(), timeout)
        (breakers["sabnzbd"].success if ok else (lambda: breakers["sabnzbd"].failure(code)))()
        return dict(initialized=True, connected=ok, err=None if ok else (code, "SABnzbd probe failed"))

    results = await asyncio.gather(aria2(), qbit(), sab())
    live = dict(zip(("aria2", "qbittorrent", "sabnzbd"), results))

    for eid, label in ENGINES:
        info = {"enabled": True, "initialized": None, "connected": None, "err": None}
        configured = True
        if eid in live:
            info.update(live[eid])
        elif eid == "jdownloader":
            jd = state.jdownloader()
            configured = bool(getattr(cfg, "JD_EMAIL", "") and getattr(cfg, "JD_PASS", ""))
            info.update(initialized=jd is not None and configured,
                        connected=bool(getattr(jd, "is_connected", False)) if configured else False)
        elif eid == "mega":
            configured = bool(getattr(cfg, "MEGA_EMAIL", ""))
        elif eid == "seedr":
            configured = bool(getattr(cfg, "SEEDR_EMAIL", "")) and not getattr(cfg, "DISABLE_SEEDR", True)
            info["enabled"] = not getattr(cfg, "DISABLE_SEEDR", True)
        elif eid == "telegram":
            tg = state.telegram()
            bot = tg.get("bot")
            info.update(initialized=bot is not None, connected=bool(getattr(bot, "is_connected", bot is not None)))
        dep = dep_by.get(_DEP_FOR.get(eid, ""))
        if info["enabled"] is False:
            health = "disabled"
        elif not configured:
            health = "disabled" if eid in ("jdownloader", "mega", "seedr") else "unknown"
        elif info["connected"] is True:
            health = "healthy"
        elif info["connected"] is False and info["initialized"] is False:
            health = "unavailable"
        elif info["connected"] is False:
            health = "degraded"
        elif dep is not None:
            health = "healthy" if dep["found"] else "unavailable"
        else:
            health = "unknown"
        err = info.get("err")
        out.append({
            "id": eid, "display_name": label, "configured": configured, "enabled": bool(info["enabled"]),
            "initialized": info["initialized"], "connected": info["connected"], "health": health,
            "version": versions.get(eid), "capabilities": [], "last_checked_at": now,
            "last_error": {"code": err[0], "message": err[1]} if err else None,
            "required_by_enabled_features": _REQUIRED_BY.get(eid, []) if info["enabled"] and configured else [],
        })
    return out
