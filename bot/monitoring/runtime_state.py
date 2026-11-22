"""The only place that touches WZML runtime objects. Read-only, lazy, defensive.

Everything is imported lazily so this package imports cleanly without WZML
(unit tests) and a missing/renamed WZML symbol degrades one section instead of
breaking startup. Tests inject a fake with the same interface.
"""
import time
from importlib import import_module


def _get(module, name, default=None):
    try:
        return getattr(import_module(module), name, default)
    except Exception:
        return default


class RuntimeState:
    # shared task/queue state ------------------------------------------------
    def task_dict(self):
        return _get("bot", "task_dict", {})

    def task_dict_lock(self):
        return _get("bot", "task_dict_lock")

    def queued_dl(self):
        return _get("bot", "queued_dl", {})

    def queued_up(self):
        return _get("bot", "queued_up", {})

    def non_queued_dl(self):
        return _get("bot", "non_queued_dl", set())

    def non_queued_up(self):
        return _get("bot", "non_queued_up", set())

    def queue_dict_lock(self):
        return _get("bot", "queue_dict_lock")

    # configuration / metadata ----------------------------------------------
    def config(self):
        return _get("bot.core.config_manager", "Config")

    def bin_config(self):
        return _get("bot.core.config_manager", "BinConfig")

    def bot_cache(self):
        return _get("bot", "bot_cache", {})

    def bot_start_time(self):
        return _get("bot", "bot_start_time", None)

    def download_dir(self):
        return _get("bot", "DOWNLOAD_DIR", "/")

    def bot_id(self):
        tg = _get("bot.core.tg_client", "TgClient")
        return getattr(tg, "ID", None) if tg else None

    # engines -----------------------------------------------------------------
    def aria2(self):
        tm = _get("bot.core.torrent_manager", "TorrentManager")
        return getattr(tm, "aria2", None) if tm else None

    def qbittorrent(self):
        tm = _get("bot.core.torrent_manager", "TorrentManager")
        return getattr(tm, "qbittorrent", None) if tm else None

    def sabnzbd(self):
        return _get("bot", "sabnzbd_client")

    def jdownloader(self):
        return _get("bot.core.jdownloader_booter", "jdownloader")

    def telegram(self):
        tg = _get("bot.core.tg_client", "TgClient")
        if not tg:
            return {"bot": None, "user": None}
        return {"bot": getattr(tg, "bot", None), "user": getattr(tg, "user", None)}

    # bandwidth accounting (side-effect free) ---------------------------------
    def bandwidth_counters(self):
        """Raw accounting globals already maintained by WZML; nothing is called."""
        mod = None
        try:
            mod = import_module("bot.helper.ext_utils.status_utils")
        except Exception:
            return None
        return {
            "historical": getattr(mod, "_historical_bw", 0),
            "last_raw": getattr(mod, "_last_raw_bw", 0),
            "alerts_sent": sorted(getattr(mod, "bandwidth_alerts_sent", set()) or []),
        }

    # history ------------------------------------------------------------------
    def history_db(self):
        db = _get("bot.helper.ext_utils.db_handler", "database")
        if db is None or getattr(db, "_return", True):
            return None
        return getattr(db, "db", None)

    def now(self):
        return time.time()
