"""Bounded, non-blocking lifecycle event buffer.

``record`` is synchronous, O(1), never awaits, never raises and never waits for
capacity. Overflow drops the oldest non-terminal event first and is counted.
"""
from collections import deque

from .constants import TERMINAL_EVENTS
from .identity import BOOT_ID


class EventRecorder:
    def __init__(self, queue_size=1000, recent_size=500):
        self.queue_size = max(10, int(queue_size))
        self._recent = deque(maxlen=max(10, int(recent_size)))
        self._pending = deque()
        self._last_stage = {}
        self.seq = 0
        self.dropped_non_terminal = 0
        self.dropped_terminal = 0
        self.coalesced = 0
        self.persist = False  # set by the service when a history writer exists
        self._wake = None

    def attach_wake(self, event):
        self._wake = event

    # -- producer side (called from WZML callbacks via hooks.py) -------------
    def record(self, event):
        try:
            etype = event.get("event_type")
            key = event.get("task_key")
            if etype == "task.stage_changed":
                if self._last_stage.get(key) == event.get("stage"):
                    self.coalesced += 1
                    return None
            if event.get("stage"):
                self._last_stage[key] = event.get("stage")
                if len(self._last_stage) > 5000:
                    self._last_stage.pop(next(iter(self._last_stage)), None)
            if etype in TERMINAL_EVENTS:
                self._last_stage.pop(key, None)
            self.seq += 1
            event["sequence"] = self.seq
            event["event_id"] = f"{BOOT_ID[:8]}-{self.seq}"
            self._recent.append(event)
            if self.persist:
                self._enqueue(event, etype in TERMINAL_EVENTS)
            return event
        except Exception:
            return None

    def _enqueue(self, event, terminal):
        if len(self._pending) >= self.queue_size:
            for i, old in enumerate(self._pending):
                if old.get("event_type") not in TERMINAL_EVENTS:
                    del self._pending[i]
                    self.dropped_non_terminal += 1
                    break
            else:
                if terminal:
                    self._pending.popleft()
                    self.dropped_terminal += 1
                else:
                    self.dropped_non_terminal += 1
                    return
        self._pending.append(event)
        if self._wake is not None:
            self._wake.set()

    # -- consumer side --------------------------------------------------------
    def drain(self, limit=100):
        out = []
        while self._pending and len(out) < limit:
            out.append(self._pending.popleft())
        return out

    def requeue_front(self, events):
        for ev in reversed(events):
            if len(self._pending) < self.queue_size:
                self._pending.appendleft(ev)

    @property
    def backlog(self):
        return len(self._pending)

    def recent(self, limit=50, after_sequence=0):
        if limit <= 0:
            return []
        items = [e for e in self._recent if e.get("sequence", 0) > after_sequence]
        return items[-limit:]

    def stats(self):
        return {
            "recorded_total": self.seq,
            "buffered_recent": len(self._recent),
            "write_backlog": self.backlog,
            "queue_capacity": self.queue_size,
            "dropped_non_terminal": self.dropped_non_terminal,
            "dropped_terminal": self.dropped_terminal,
            "coalesced": self.coalesced,
        }
