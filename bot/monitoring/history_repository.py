"""Optional asynchronous MongoDB history writer. MongoDB is never required."""
import asyncio
import time
from datetime import datetime, timezone

from .util import utc_now_iso


class HistoryRepository:
    """Persists drained events using WZML's existing database handle (if any).

    Modes reported to the API: ``disabled`` | ``memory_only`` | ``durable`` |
    ``unavailable``. ``durable`` is only claimed after a successful write.
    """

    def __init__(self, settings, recorder, state):
        self.settings = settings
        self.recorder = recorder
        self.state = state
        self.mode = "disabled" if not settings.history_enabled else "memory_only"
        self.last_error = None
        self.last_write_at = None
        self.written_total = 0
        self.failed_batches = 0
        self._backoff_until = 0.0
        self._index_ready = False
        self._wake = None
        self._task = None
        self._stop = False

    async def start(self):
        if not self.settings.history_enabled:
            return
        self._wake = asyncio.Event()
        self.recorder.attach_wake(self._wake)
        self.recorder.persist = True
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self):
        self._stop = True
        if self._wake:
            self._wake.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass
        self.recorder.persist = False

    def _collection(self):
        db = self.state.history_db()
        if db is None:
            return None
        return db.monitor_events[str(self.state.bot_id() or "default")]

    async def _ensure_index(self, coll):
        if self._index_ready:
            return
        try:
            await asyncio.wait_for(
                coll.create_index("ts", expireAfterSeconds=self.settings.history_retention_days * 86400),
                self.settings.probe_timeout,
            )
        except Exception as exc:  # index options may conflict; history still works
            self.last_error = f"index: {type(exc).__name__}"
        self._index_ready = True

    async def _run(self):
        while not self._stop:
            try:
                try:
                    await asyncio.wait_for(self._wake.wait(), 15)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                if time.monotonic() < self._backoff_until:
                    continue
                coll = self._collection()
                if coll is None:
                    self.mode = "memory_only"
                    # Keep the in-memory ring only; do not hoard pending items.
                    self.recorder.drain(self.recorder.queue_size)
                    continue
                while True:
                    batch = self.recorder.drain(100)
                    if not batch:
                        break
                    docs = []
                    for ev in batch:
                        doc = dict(ev)
                        doc["ts"] = datetime.fromtimestamp(ev.get("ts_epoch", time.time()), tz=timezone.utc)
                        docs.append(doc)
                    try:
                        await self._ensure_index(coll)
                        await asyncio.wait_for(coll.insert_many(docs, ordered=False), self.settings.probe_timeout * 2)
                        self.mode = "durable"
                        self.last_write_at = utc_now_iso()
                        self.written_total += len(docs)
                    except Exception as exc:
                        self.failed_batches += 1
                        self.mode = "unavailable"
                        self.last_error = type(exc).__name__
                        self.recorder.requeue_front(batch)
                        self._backoff_until = time.monotonic() + min(300, 15 * self.failed_batches)
                        break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let the writer die
                self.last_error = type(exc).__name__
                await asyncio.sleep(5)

    def describe(self):
        return {
            "mode": self.mode,
            "durable": self.mode == "durable",
            "retention_days": self.settings.history_retention_days,
            "last_write_at": self.last_write_at,
            "written_total": self.written_total,
            "failed_batches": self.failed_batches,
            "last_error": self.last_error,
        }
