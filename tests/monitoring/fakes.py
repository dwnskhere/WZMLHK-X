"""Fake WZML runtime: wrapper classes carry the real class names the adapters key on."""
import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace as NS


def listener(mid, name="file.mkv", size=1000, mode=("#Aria2", "#GDrive"), **kw):
    msg = NS(id=mid + 1, chat=NS(id=-100123), date=datetime.fromtimestamp(time.time() - 30, tz=timezone.utc),
             from_user=NS(id=999, username="secretuser"), text="https://secret.example/x?token=abc")
    base = dict(mid=mid, name=name, size=size, subname="", mode=mode, message=msg, is_cancelled=False,
                is_torrent=False, download_start_time=time.time() - 20, files_to_proceed=[], proceed_count=0,
                link="https://secret.example/file", dir="/usr/src/app/downloads/1", subsize=size,
                avg_download_speed=10, avg_upload_speed=5, seed=False)
    base.update(kw)
    return NS(**base)


class Aria2Status:
    engine = "Aria2 v1.37.0"
    def __init__(self, l, gid="g1", seeding=False, queued=False):
        self.listener, self._gid, self.seeding, self.queued, self._download, self.start_time = l, gid, seeding, queued, {}, 0
    def gid(self): return self._gid
    async def status(self): raise AssertionError("monitoring must not call async status()")
    async def update(self): raise AssertionError("monitoring must not call update()")


class QbittorrentStatus:
    engine = "qBit v5.0.0"
    def __init__(self, l, seeding=False, queued=False):
        self.listener, self.seeding, self.queued, self._info = l, seeding, queued, None
    def gid(self): return str(self.listener.mid)
    async def status(self): raise AssertionError
    async def update(self): raise AssertionError


class SabnzbdStatus:
    engine = "SABnzbd+ v4.0"
    def __init__(self, l, gid="nzo1", queued=False):
        self.listener, self._gid, self.queued, self._info = l, gid, queued, {}
    def gid(self): return self._gid
    async def status(self): raise AssertionError


class JDownloaderStatus:
    engine = "JDownloader v2"
    def __init__(self, l, info=None):
        self.listener, self._info = l, info or {}
    def gid(self): return "jd1"
    async def status(self): raise AssertionError


class SeedrStatus:
    engine = "Seedr"
    def __init__(self, l, info=None):
        self.listener, self._info = l, info or {}
    def gid(self): return "77"
    async def status(self): raise AssertionError


def sync_wrapper(cls_name, l, status, obj=None, **attrs):
    cls = type(cls_name, (), {"status": lambda self: status, "gid": lambda self: "x1"})
    w = cls()
    w.listener, w._obj, w.engine = l, obj or NS(), "Eng vN/A"
    for k, v in attrs.items():
        setattr(w, k, v)
    return w


class Evt:
    def set(self): pass


class FakeAria2:
    def __init__(self, active=None, waiting=None, fail=False, delay=0):
        self.active, self.waiting, self.fail, self.delay, self.calls = active or [], waiting or [], fail, delay, 0
    async def _go(self, v):
        self.calls += 1
        if self.delay: await asyncio.sleep(self.delay)
        if self.fail: raise ConnectionError("down")
        return v
    async def tellActive(self, *a): return await self._go(self.active)
    async def tellWaiting(self, *a): return await self._go(self.waiting)
    async def getGlobalStat(self): return await self._go({"downloadSpeed": "5000", "uploadSpeed": "100"})
    async def getVersion(self): return await self._go({"version": "1.37"})


class FakeQbTorrents:
    def __init__(self, items): self.items = items
    async def info(self, **k): return self.items


class FakeQb:
    def __init__(self, items):
        self.torrents = FakeQbTorrents(items)
        self.transfer = NS(info=self._tinfo)
    async def _tinfo(self): return NS(dl_info_speed=111, up_info_speed=222)


class FakeState:
    def __init__(self, tasks=None, aria2=None, qb=None, config=None):
        self._td = dict(tasks or {})
        self._aria2, self._qb = aria2, qb
        self._lock, self._qlock = asyncio.Lock(), asyncio.Lock()
        self.qdl, self.qup, self.ndl, self.nup = {}, {}, set(), set()
        self._cfg = config or NS(STATUS_UPDATE_INTERVAL=15, QUEUE_ALL=0, QUEUE_DOWNLOAD=2, QUEUE_UPLOAD=0,
                                 MONTHLY_BANDWIDTH=100, DISABLE_TORRENTS=False, JD_EMAIL="", JD_PASS="",
                                 MEGA_EMAIL="", SEEDR_EMAIL="", DISABLE_SEEDR=True, BOT_TOKEN="SECRET-BOT-TOKEN",
                                 DATABASE_URL="mongodb://user:pw@host", STATUS_LIMIT=10, UPSTREAM_BRANCH="status-dev")
    def task_dict(self): return self._td
    def task_dict_lock(self): return self._lock
    def queued_dl(self): return self.qdl
    def queued_up(self): return self.qup
    def non_queued_dl(self): return self.ndl
    def non_queued_up(self): return self.nup
    def queue_dict_lock(self): return self._qlock
    def config(self): return self._cfg
    def bin_config(self): return NS(ARIA2_NAME="definitely-missing-aria", QBIT_NAME="nope-qb", FFMPEG_NAME="nope-ff",
                                    RCLONE_NAME="nope-rc", SABNZBD_NAME="nope-sab")
    def bot_cache(self): return {"eng_versions": {"aria2": "1.37"}}
    def bot_start_time(self): return time.time() - 100
    def download_dir(self): return "/tmp"
    def bot_id(self): return 1
    def aria2(self): return self._aria2
    def qbittorrent(self): return self._qb
    def sabnzbd(self): return None
    def jdownloader(self): return None
    def telegram(self): return {"bot": object(), "user": None}
    def bandwidth_counters(self): return {"historical": 1024**3, "last_raw": 0, "alerts_sent": []}
    def history_db(self): return None
