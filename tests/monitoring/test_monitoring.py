import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from bot.monitoring.collector import Collector
from bot.monitoring.config import derive_internal_secret, load_settings
from bot.monitoring.constants import ENGINE_MAP, WZML_STATUS_STAGE
from bot.monitoring.engine_adapters import READERS, EngineSnapshots
from bot.monitoring.event_recorder import EventRecorder
from bot.monitoring.history_repository import HistoryRepository
from bot.monitoring.hooks import record_lifecycle_event, sanitize_error, set_recorder
from bot.monitoring.service import StatusService
from bot.monitoring.snapshot_store import SnapshotStore
from bot.monitoring.task_serializer import derive_lifecycle, normalize_stage, serialize_task

from fakes import *  # noqa

pytestmark = pytest.mark.asyncio(loop_scope="function")


def settings(**env):
    import os
    old = {k: os.environ.get(k) for k in env}
    os.environ.update({k: str(v) for k, v in env.items()})
    try:
        return load_settings()
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def build(state, **env):
    env = {"MONITOR_API_ENABLED": "true", "MONITOR_API_TOKEN": "tok", **env}
    st = settings(**env)
    rec = EventRecorder(st.event_queue_size)
    store = SnapshotStore()
    hist = HistoryRepository(st, rec, state)
    col = Collector(st, state, store, rec, hist)
    return st, rec, store, hist, col, StatusService(st, store, rec, hist, col)


def all_wrappers():
    out = {}
    snap = EngineSnapshots()
    out[1] = Aria2Status(listener(1, size=1000), "g1")
    out[2] = QbittorrentStatus(listener(2, mode=("#qBit", "#Leech")))
    out[3] = SabnzbdStatus(listener(3))
    out[4] = JDownloaderStatus(listener(4), {"bytesLoaded": 50, "bytesTotal": 100, "speed": 10, "status": "Running"})
    out[5] = SeedrStatus(listener(5), {"progress": 50, "size": 200, "speed": 5, "eta": 3})
    out[6] = sync_wrapper("DirectStatus", listener(6), "Download", NS(processed_bytes=100, speed=10))
    out[7] = sync_wrapper("GoogleDriveStatus", listener(7), "Upload", NS(processed_bytes=300, speed=30), _size=1000)
    out[8] = sync_wrapper("TelegramStatus", listener(8), "Upload", NS(processed_bytes=300, speed=30), _size=1000)
    out[9] = sync_wrapper("UphosterStatus", listener(9), "Upload", NS(processed_bytes=1, speed=1), _size=10)
    out[10] = sync_wrapper("YtStatus", listener(10), "Download", NS(processed_bytes=1, speed=1), _size=10)
    out[11] = sync_wrapper("YtDlpStatus", listener(11), "Download", NS(downloaded_bytes=50, download_speed=5, size=100, progress=50, eta=10))
    out[12] = sync_wrapper("FFmpegStatus", listener(12), "FFmpeg", NS(processed_bytes=10, speed_raw=2, progress_raw=10.0, eta_raw=5))
    out[13] = sync_wrapper("SevenZStatus", listener(13), "Archive", NS(processed_bytes=10, progress="10%"))
    out[14] = sync_wrapper("MetadataStatus", listener(14), "Metadata", NS(processed_bytes=1, speed_raw=1, progress_raw=1.0, eta_raw=1))
    out[15] = sync_wrapper("MegaDownloadStatus", listener(15), "Download", _status="Download", _downloaded_bytes=10, _speed=2, _size=100)
    out[16] = sync_wrapper("RcloneStatus", listener(16), "Upload", NS(size="1.00GB", transferred_size="512.00MB", percentage="50%", speed="1.5MiB/s", eta="5s"))
    out[17] = sync_wrapper("QueueStatus", listener(17), "QueueDl")
    return out


async def test_all_17_wrappers_serialize_and_are_covered():
    assert set(ENGINE_MAP) == set(READERS)
    assert len(ENGINE_MAP) == 17
    snaps = EngineSnapshots()
    seen = set()
    for k, w in all_wrappers().items():
        t = serialize_task(k, w, snaps, 0.0 + __import__("time").time(), "i")
        assert t["task_key"] == str(k) and t["engine"]["id"]
        seen.add(t["engine"]["id"])
        json.dumps(t)
    assert len(seen) == 17


async def test_16_known_statuses_and_unknown_preserved():
    assert len(WZML_STATUS_STAGE) == 16
    for raw, stage in WZML_STATUS_STAGE.items():
        assert normalize_stage(raw)[0] == stage
    assert normalize_stage("Verifying")[0] == "checking"
    assert normalize_stage("BrandNewThing") == ("other", False)
    assert normalize_stage(None)[0] == "unknown"
    assert derive_lifecycle("QueueDl", None) == "queued"
    assert derive_lifecycle("Pause", None) == "paused"
    assert derive_lifecycle("Seed", None) == "running"


async def test_aria2_qbit_use_aggregate_snapshot_no_per_task_rpc():
    aria = FakeAria2(active=[{"gid": "g1", "status": "active", "totalLength": "1000", "completedLength": "250",
                              "downloadSpeed": "50", "uploadSpeed": "5", "numSeeders": "3", "connections": "4",
                              "infoHash": "abc"}])
    qb = FakeQb([NS(hash="h", name="n", state="stalledUP", progress=1.0, size=500, downloaded=500, uploaded=900,
                    dlspeed=0, upspeed=77, eta=NS(total_seconds=lambda: 8640000), ratio=1.8, num_seeds=2,
                    num_leechs=1, seeding_time=NS(total_seconds=lambda: 60), tags=["2"])])
    w2 = QbittorrentStatus(listener(2), seeding=True)
    st = FakeState({1: Aria2Status(listener(1), "g1"), 2: w2}, aria2=aria, qb=qb)
    _, rec, store, hist, col, svc = build(st)
    await col.cycle()
    first = aria.calls  # includes one health probe (getVersion) on the first cycle
    await col.cycle()
    assert aria.calls - first == 3  # tellActive + tellWaiting + getGlobalStat, regardless of task count
    tasks = {t["task_key"]: t for t in store.snapshot["data"]["tasks"]}
    a, q = tasks["1"], tasks["2"]
    assert a["progress"]["percent"] == 25 and a["transfer"]["download_bps"] == 50 and a["engine"]["hash"] == "abc"
    assert a["timing"]["eta_seconds"] == 15 and a["state"]["wzml_status"] == "Download"
    assert q["state"]["stage"] == "seeding" and q["transfer"]["seeding_bps"] == 77
    assert q["torrent"]["ratio"] == 1.8 and q["timing"]["eta_seconds"] is None
    assert a["engine"]["version"] == "1.37.0"


async def test_engine_failure_isolated_and_circuit_breaker():
    st = FakeState({1: Aria2Status(listener(1), "g1"), 2: QbittorrentStatus(listener(2))},
                   aria2=FakeAria2(fail=True), qb=FakeQb([]))
    _, rec, store, hist, col, svc = build(st)
    for _ in range(4):
        await col.cycle()
    assert col.breakers["aria2"].state == "open"
    assert col.breakers["qbittorrent"].state == "closed"
    snap = store.snapshot
    assert snap["data"]["tasks"][0]["transfer"]["quality"] == "unavailable"
    assert any(w["code"] == "ENGINE_SNAPSHOT_FAILED" for w in snap["data"]["warnings"])
    assert store.publish_count == 4  # failure never produced an empty/fake snapshot


async def test_slow_engine_times_out_without_blocking_cycle():
    st = FakeState({1: Aria2Status(listener(1), "g1")}, aria2=FakeAria2(delay=5))
    _, rec, store, hist, col, svc = build(st, MONITOR_PROBE_TIMEOUT="0.5")
    import time
    t0 = time.monotonic()
    await col.cycle()
    assert time.monotonic() - t0 < 4
    assert store.snapshot is not None


async def test_no_overlap_and_failed_collection_keeps_good_snapshot():
    st = FakeState({1: sync_wrapper("DirectStatus", listener(1), "Download", NS(processed_bytes=1, speed=1))})
    _, rec, store, hist, col, svc = build(st)
    await col.cycle()
    good = store.snapshot["snapshot_id"]
    col._busy = True
    await col.cycle()
    assert col.skipped == 1 and store.snapshot["snapshot_id"] == good
    col._busy = False
    st.task_dict = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await col.cycle()
    assert store.snapshot["snapshot_id"] == good


async def test_locks_not_held_during_await_and_no_mutation():
    td = {i: sync_wrapper("DirectStatus", listener(i), "Download", NS(processed_bytes=1, speed=1)) for i in range(120)}
    st = FakeState(td)
    before = dict(td)
    _, rec, store, hist, col, svc = build(st)
    await col.cycle()
    assert not st._lock.locked() and not st._qlock.locked()
    assert st._td == before and len(store.snapshot["data"]["tasks"]) == 120


async def test_queue_dedup_positions_and_capacity():
    q1 = sync_wrapper("QueueStatus", listener(10, name="q1"), "QueueDl")
    q2 = sync_wrapper("QueueStatus", listener(11, name="q2"), "QueueDl")
    run = sync_wrapper("DirectStatus", listener(12), "Download", NS(processed_bytes=1, speed=1))
    st = FakeState({10: q1, 11: q2, 12: run})
    st.qdl = {10: Evt(), 11: Evt(), 99: Evt()}
    st.ndl = {12}
    _, rec, store, hist, col, svc = build(st)
    await col.cycle()
    d = store.snapshot["data"]
    assert d["summary"]["total_tasks"] == 3  # QueueStatus not double counted
    qd = d["queues"]["download"]
    assert qd["length"] == 3 and qd["active"] == 1 and qd["capacity"] == 2 and qd["available_slots"] == 1
    assert [i["position"] for i in qd["items"]] == [1, 2, 3]
    assert qd["items"][2]["task_id"] is None  # queued key not (yet) in task_dict
    tk = {t["task_key"]: t for t in d["tasks"]}
    assert tk["10"]["queue_position"] == 1 and tk["10"]["state"]["lifecycle"] == "queued"


async def test_service_envelope_sections_pagination_errors_and_privacy():
    td = {i: sync_wrapper("DirectStatus", listener(i), "Download", NS(processed_bytes=1, speed=1)) for i in range(7)}
    st = FakeState(td)
    _, rec, store, hist, col, svc = build(st, MONITOR_MAX_TASKS_PER_RESPONSE="5")
    code, body = svc.render({})
    assert code == 503 and json.loads(body)["error"]["code"] == "MONITOR_NOT_READY"
    await col.cycle()
    code, body = svc.render({})
    j = json.loads(body)
    assert code == 200 and j["success"] and j["api_version"] == "1"
    assert set(j["data"]) == {"instance", "bot", "status_catalog", "summary", "tasks", "queues", "engines",
                              "dependencies", "system", "transfer", "limits", "software", "events", "warnings"}
    p = j["meta"]["pagination"]["tasks"]
    assert p["total"] == 7 and p["returned"] == 5 and p["truncated"] and p["next_cursor"]
    code, body2 = svc.render({"cursor": p["next_cursor"]})
    j2 = json.loads(body2)
    assert len(j2["data"]["tasks"]) == 2 and not j2["meta"]["pagination"]["tasks"]["truncated"]
    code, body3 = svc.render({"sections": "summary,queues"})
    assert set(json.loads(body3)["data"]) == {"summary", "queues"}
    assert svc.render({"sections": "nope"})[0] == 400
    assert svc.render({"tasks_limit": "abc"})[0] == 400
    assert svc.render({"tasks_limit": "9999"})[0] == 400
    assert svc.render({"cursor": "garbage"})[0] == 400
    text = body.decode() + body2.decode()
    for secret in ("secret.example", "secretuser", "SECRET-BOT-TOKEN", "mongodb://", "/usr/src/app", "token=abc", "pw@host"):
        assert secret not in text, secret
    assert j["meta"]["is_stale"] is False and j["meta"]["sections"]["tasks"] == "ok"
    store._published_mono -= 100
    assert json.loads(svc.render({})[1])["meta"]["is_stale"] is True


async def test_missing_binaries_are_warnings_not_failures():
    st = FakeState({})
    _, rec, store, hist, col, svc = build(st)
    await col.cycle()
    d = store.snapshot["data"]
    deps = {x["id"]: x for x in d["dependencies"]}
    assert deps["aria2"]["found"] is False and deps["aria2"]["executable"] == "definitely-missing-aria"
    assert any(w["code"] == "DEPENDENCY_MISSING" for w in d["warnings"])
    eng = {e["id"]: e for e in d["engines"]}
    assert eng["aria2"]["initialized"] is False and eng["aria2"]["health"] == "unavailable"
    assert store.snapshot["meta_base"]["collection"]["interval_seconds"] == 15


async def test_bandwidth_is_side_effect_free_and_system_section():
    st = FakeState({})
    _, rec, store, hist, col, svc = build(st)
    await col.cycle()
    bw = store.snapshot["data"]["transfer"]["bandwidth_accounting"]
    assert bw["available"] and bw["monthly_limit_bytes"] == 100 * 1024**3 and bw["used_bytes"] >= 1024**3
    s = store.snapshot["data"]["system"]
    assert s["network"]["scope"] == "host_or_container" and s["memory"]["total_bytes"] > 0
    assert "limits" and "BOT_TOKEN" not in store.snapshot["data"]["limits"]


async def test_events_hook_nonblocking_bounded_and_classified():
    rec = EventRecorder(queue_size=10)
    rec.persist = True
    set_recorder(rec)
    try:
        l = listener(5)
        record_lifecycle_event(l, "task.started", stage="downloading")
        record_lifecycle_event(l, "task.stage_completed", stage="downloading")
        record_lifecycle_event(l, "task.error", stage="downloading", error="No space http://x.y/z at /usr/src/app/f")
        l.is_cancelled = True
        record_lifecycle_event(l, "task.error", error="Stopped by user!")
        record_lifecycle_event(l, "task.error", error="x", is_limit=True)
        types = [e["event_type"] for e in rec.recent(50)]
        assert types == ["task.started", "task.stage_completed", "task.failed", "task.cancelled", "task.cancelled"]
        failed = rec.recent(50)[2]
        assert "http" not in failed["error"]["message"] and "/usr" not in failed["error"]["message"]
        assert failed["origin"]["chat_id"] == -100123 and "user" not in json.dumps(failed).lower().replace("user_id", "")
        for i in range(100):  # overflow: never blocks, never raises, terminal prioritized
            record_lifecycle_event(listener(i), "task.stage_changed", stage="uploading")
        record_lifecycle_event(listener(500), "task.succeeded", stage="uploading")
        assert rec.backlog <= 10 and rec.dropped_non_terminal > 0
        assert any(e["event_type"] == "task.succeeded" for e in rec._pending)
        record_lifecycle_event(object(), "task.error", error=None)  # garbage input must not raise
    finally:
        set_recorder(None)
    record_lifecycle_event(listener(1), "task.started")  # disabled: silent no-op


async def test_history_mode_is_honest_without_mongo():
    st = FakeState({})
    _, rec, store, hist, col, svc = build(st)
    await hist.start()
    record_lifecycle_event_obj = rec.record({"event_type": "task.succeeded", "task_key": "1"})
    await asyncio.sleep(0.05)
    assert hist.describe()["mode"] == "memory_only" and hist.describe()["durable"] is False
    await hist.stop()


async def test_config_fail_closed_and_secret_derivation():
    s = settings(MONITOR_API_ENABLED="true")
    assert s.enabled and not s.usable
    d = settings()
    assert not d.enabled and not d.usable
    s2 = settings(MONITOR_API_ENABLED="true", MONITOR_API_TOKEN="abc")
    assert s2.usable and s2.internal_secret == derive_internal_secret("abc") and s2.internal_secret != "abc"
    assert settings(MONITOR_INTERNAL_HOST="0.0.0.0").internal_host == "127.0.0.1"
    assert settings(MONITOR_INTERNAL_PORT="notanumber").internal_port == 8765
