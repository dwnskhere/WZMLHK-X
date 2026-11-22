"""Background snapshot collector. Never overlaps, never raises, never blocks WZML."""
import asyncio
import time

from .constants import NATIVE_STATUS_STAGE, WZML_STATUS_STAGE, ENGINE_MAP
from .dependency_collector import probe_dependencies
from .engine_adapters import Breaker, fetch_engine_snapshots
from .engine_health import collect_engine_health
from .identity import BOOT_ID, REGISTRY, STARTED_AT
from .limits_collector import collect_limits
from .queue_collector import build_queues, collect_queue_refs
from .software_collector import collect_software
from .system_collector import SystemSampler
from .task_collector import collect_task_refs
from .task_serializer import minimal_failed_record, serialize_task
from .transfer_collector import build_transfer
from .util import pct, to_int, utc_now_iso

LOG = None


def _log():
    global LOG
    if LOG is None:
        try:
            from bot import LOGGER

            LOG = LOGGER
        except Exception:
            import logging

            LOG = logging.getLogger("monitoring")
    return LOG


class Collector:
    def __init__(self, settings, state, store, recorder, history):
        self.settings, self.state, self.store = settings, state, store
        self.recorder, self.history = recorder, history
        self.breakers = {n: Breaker() for n in ("aria2", "qbittorrent", "sabnzbd")}
        self.sems = {n: asyncio.Semaphore(settings.engine_concurrency) for n in self.breakers}
        self.sampler = SystemSampler(state.download_dir())
        self.seen_statuses = set()
        self.cycle_count = 0
        self.skipped = 0
        self.failed_cycles = 0
        self.last_duration_ms = None
        self.last_error = None
        self.state_name = "starting"
        self._busy = False
        self._task = None
        self._stop = False
        self._cache = {}  # name -> (data, epoch)
        self._at = {}  # cadence timestamps

    # -- lifecycle ------------------------------------------------------------
    def start(self):
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self):
        self._stop = True
        self.state_name = "stopped"
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass

    def interval(self):
        cfg = self.state.config()
        return float(max(1, to_int(getattr(cfg, "STATUS_UPDATE_INTERVAL", 15), 15) or 15))

    async def _loop(self):
        while not self._stop:
            t0 = time.monotonic()
            try:
                await self.cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # last-resort guard: keep the loop alive
                self.failed_cycles += 1
                self.last_error = type(exc).__name__
                self.state_name = "degraded"
                _log().warning(f"Monitoring cycle failed: {type(exc).__name__}")
            await asyncio.sleep(max(0.5, self.interval() - (time.monotonic() - t0)))

    # -- helpers ----------------------------------------------------------------
    def _due(self, name, every):
        now = time.monotonic()
        if now - self._at.get(name, -1e9) >= every:
            self._at[name] = now
            return True
        return False

    async def _section(self, name, factory, sections, warnings):
        """Run a section collector; on failure reuse the last good data as stale."""
        try:
            data = await factory()
            self._cache[name] = (data, time.time())
            sections[name] = "ok"
            return data
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            warnings.append({"code": "SECTION_FAILED", "section": name, "severity": "warning",
                             "message": f"{name} could not be collected ({type(exc).__name__})"})
            cached = self._cache.get(name)
            sections[name] = "stale" if cached else "unavailable"
            return cached[0] if cached else None

    # -- one cycle ----------------------------------------------------------------
    async def cycle(self):
        if self._busy:
            self.skipped += 1
            return
        self._busy = True
        t0 = time.monotonic()
        try:
            snap = await self._build()
            self.store.publish(snap)
            self.cycle_count += 1
            self.state_name = "running" if not snap["meta_base"]["is_partial"] else "degraded"
        finally:
            self._busy = False
            self.last_duration_ms = round((time.monotonic() - t0) * 1000, 1)

    async def _build(self):
        state, cfg, st = self.state, self.state.config(), self.settings
        now = time.time()
        sections, warnings = {}, []

        refs, lockinfo = await collect_task_refs(state)
        qrefs = await collect_queue_refs(state)
        needed = {ENGINE_MAP[type(w).__name__][0] for _, w in refs if type(w).__name__ in ENGINE_MAP}
        needed &= {"aria2", "qbittorrent", "sabnzbd"}
        snaps = await fetch_engine_snapshots(state, needed, self.breakers, self.sems, st.probe_timeout)
        for eng, code in snaps.errors.items():
            if code != "not_initialized":
                warnings.append({"code": "ENGINE_SNAPSHOT_FAILED", "section": "tasks", "severity": "warning",
                                 "message": f"{eng} aggregate read failed ({code}); cached values used"})

        health_map = {e["id"]: e["health"] for e in (self._cache.get("engines", (None,))[0] or [])}
        tasks, failed = [], 0
        for i, (key, wrapper) in enumerate(refs):
            try:
                tasks.append(serialize_task(key, wrapper, snaps, now, st.instance_id,
                                            health=health_map, seen_statuses=self.seen_statuses))
            except Exception:
                failed += 1
                tasks.append(minimal_failed_record(key, st.instance_id, now))
            if i % 50 == 49:
                await asyncio.sleep(0)
        if failed:
            warnings.append({"code": "TASK_SERIALIZE_FAILED", "section": "tasks", "severity": "warning",
                             "message": f"{failed} task(s) could not be fully serialized"})
        sections["tasks"] = "partial" if failed else "ok"
        REGISTRY.forget_unless([k for k, _ in refs])

        by_key = {t["task_key"]: t for t in tasks}
        queues = build_queues(qrefs, cfg, by_key)
        for kind in ("download", "upload"):
            for item in queues[kind]["items"]:
                if item["task_key"] in by_key:
                    by_key[item["task_key"]]["queue_position"] = item["position"]
        sections["queues"] = "ok"

        system = await self._section("system", lambda: asyncio.to_thread(self.sampler.sample), sections, warnings)
        if system is None:
            system = {}
        if self._due("deps", 300) or "dependencies" not in self._cache:
            deps = await self._section(
                "dependencies",
                lambda: asyncio.to_thread(probe_dependencies, state.bin_config(), cfg, st.probe_timeout),
                sections, warnings)
        else:
            deps = self._cache["dependencies"][0]
            sections["dependencies"] = "ok"
        deps = deps or []
        versions = {d["id"]: d["version"] for d in deps if d.get("version")}
        versions.update({"aria2": versions.get("aria2"), "qbittorrent": versions.get("qbittorrent")})
        if self._due("health", 30) or "engines" not in self._cache:
            engines = await self._section(
                "engines",
                lambda: collect_engine_health(state, deps, versions, st.probe_timeout, self.breakers, self.sems),
                sections, warnings)
        else:
            engines = self._cache["engines"][0]
            sections["engines"] = "ok"
        engines = engines or []
        if self._due("software", 600) or "software" not in self._cache:
            software = await self._section(
                "software", lambda: asyncio.to_thread(collect_software, state, deps), sections, warnings)
        else:
            software = self._cache["software"][0]
            sections["software"] = "ok"

        limits = collect_limits(cfg)
        sections["limits"] = "ok"
        transfer = build_transfer(tasks, snaps, system, state, cfg)
        sections["transfer"] = "ok"
        for d in deps:
            if not d["found"] and d["criticality"] in ("critical", "required"):
                warnings.append({"code": "DEPENDENCY_MISSING", "section": "dependencies", "severity": "warning",
                                 "message": f"{d['display_name']} executable '{d['executable']}' was not found on PATH"})
        for e in engines:
            if e["enabled"] and e["configured"] and e["health"] in ("unavailable", "degraded"):
                warnings.append({"code": "ENGINE_UNHEALTHY", "section": "engines", "severity": "warning",
                                 "message": f"{e['display_name']} is {e['health']}"})
        summary = self._summary(tasks, queues, transfer, system, warnings)
        sections["summary"] = "ok"
        is_partial = any(v in ("partial", "stale", "unavailable") for v in sections.values())

        data = {
            "instance": {"instance_id": st.instance_id, "boot_id": BOOT_ID,
                         "wzml_version": (software or {}).get("wzml", {}).get("version"),
                         "wzml_commit": (software or {}).get("wzml", {}).get("commit"),
                         "started_at": utc_now_iso(state.bot_start_time() or STARTED_AT),
                         "uptime_seconds": int(now - (state.bot_start_time() or STARTED_AT))},
            "bot": self._bot(engines),
            "status_catalog": self._catalog(),
            "summary": summary,
            "tasks": tasks,
            "queues": queues,
            "engines": engines,
            "dependencies": deps,
            "system": system,
            "transfer": transfer,
            "limits": limits,
            "software": software or {},
            "warnings": warnings,
        }
        return {
            "generated_epoch": now,
            "generated_at": utc_now_iso(now),
            "data": data,
            "meta_base": {
                "is_partial": is_partial,
                "sections": {**sections, "events": "ok"},
                "collection": {"duration_ms": None, "interval_seconds": self.interval(),
                               "lock_wait_ms": lockinfo["lock_wait_ms"],
                               "lock_used": lockinfo["lock_used"]},
            },
        }

    def _bot(self, engines):
        tg = next((e for e in engines if e["id"] == "telegram"), {})
        return {
            "state": self.state_name if self.state_name in ("starting", "running", "degraded", "stopping", "stopped") else "unknown",
            "telegram_bot": {"configured": tg.get("configured"), "connected": tg.get("connected")},
            "telegram_user": {"configured": self.state.telegram().get("user") is not None,
                              "connected": self.state.telegram().get("user") is not None or None},
            "web_process": "unknown",
            "monitoring": {"collector_state": self.state_name, "cycles": self.cycle_count,
                           "skipped_cycles": self.skipped, "failed_cycles": self.failed_cycles,
                           "last_cycle_ms": self.last_duration_ms, "last_error": self.last_error},
            "private_bridge": "running",
            "collection_interval_seconds": self.interval(),
        }

    def _catalog(self):
        return {
            "known_wzml_statuses": WZML_STATUS_STAGE,
            "known_engine_native_statuses": NATIVE_STATUS_STAGE,
            "observed_statuses": sorted(self.seen_statuses),
            "unknown_state_policy": "Unknown raw values are preserved on the task and normalized to 'other'.",
            "supported_engines": sorted({v[0] for v in ENGINE_MAP.values()}),
            "capabilities": ["read_only", "snapshot_cache", "pagination", "sections_filter", "lifecycle_events"],
        }

    def _summary(self, tasks, queues, transfer, system, warnings):
        def count(fn):
            out = {}
            for t in tasks:
                k = fn(t) or "unknown"
                out[k] = out.get(k, 0) + 1
            return out

        overall = transfer["task_engine"]["overall"]
        cpu, mem = (system or {}).get("cpu") or {}, (system or {}).get("memory") or {}
        disk = ((system or {}).get("filesystem") or {}).get("downloads") or {}
        stages = count(lambda t: t["state"]["stage"])
        return {
            "total_tasks": len(tasks),
            "by_lifecycle": count(lambda t: t["state"]["lifecycle"]),
            "by_stage": stages,
            "by_wzml_status": count(lambda t: t["state"]["wzml_status"]),
            "by_engine": count(lambda t: t["engine"]["id"]),
            "download_queue_length": queues["download"]["length"],
            "upload_queue_length": queues["upload"]["length"],
            "active_downloads": queues["download"]["active"],
            "active_uploads": queues["upload"]["active"],
            "paused": sum(1 for t in tasks if t["state"]["lifecycle"] == "paused"),
            "seeding": stages.get("seeding", 0),
            "rates": {"download_bps": overall["download_bps"], "upload_bps": overall["upload_bps"],
                      "seeding_bps": overall["seeding_bps"]},
            "headline": {"cpu_percent": cpu.get("percent"), "memory_percent": mem.get("percent"),
                         "download_disk_percent": disk.get("percent")},
            "engine_dependency_warning_count": sum(
                1 for w in warnings if w["code"] in ("DEPENDENCY_MISSING", "ENGINE_UNHEALTHY")),
        }
