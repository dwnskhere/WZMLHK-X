"""Monitoring-owned identities: boot id and per-execution task ids."""
import time
import uuid
from collections import OrderedDict

BOOT_ID = uuid.uuid4().hex
STARTED_AT = time.time()


class TaskRegistry:
    """Maps a WZML task key (mid) to a monitoring task_id for this execution.

    Bounded LRU so it can never grow without limit. It only reads the key; it
    never touches WZML task objects.
    """

    def __init__(self, max_entries=5000):
        self._max = max_entries
        self._items = OrderedDict()

    def ensure(self, key):
        key = str(key)
        entry = self._items.get(key)
        if entry is None:
            entry = {"task_id": f"{BOOT_ID[:8]}-{uuid.uuid4().hex[:12]}", "first_seen": time.time()}
            self._items[key] = entry
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        else:
            self._items.move_to_end(key)
        return entry

    def forget_unless(self, active_keys):
        """Drop identities of tasks long gone (kept briefly so late events resolve)."""
        cutoff = time.time() - 600
        active = {str(k) for k in active_keys}
        for key in [k for k, v in self._items.items() if k not in active and v["first_seen"] < cutoff]:
            self._items.pop(key, None)


REGISTRY = TaskRegistry()
