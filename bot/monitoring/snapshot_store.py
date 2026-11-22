"""Atomic latest-snapshot holder with pre-serialized JSON."""
import json
import time
import uuid


class SnapshotStore:
    """Holds the latest published snapshot. Replace-only, never mutated."""

    def __init__(self):
        self._snapshot = None  # dict (treated as immutable once published)
        self._json = None  # bytes: pre-serialized full response body for default view
        self._published_mono = None
        self._previous_ids = []
        self.publish_count = 0

    def publish(self, snapshot, body_bytes=None):
        snapshot["snapshot_id"] = snapshot.get("snapshot_id") or uuid.uuid4().hex[:16]
        if body_bytes is None:
            body_bytes = json.dumps(snapshot, separators=(",", ":"), default=str).encode()
        # Single reference assignments: readers see either old or new, never a mix.
        self._previous_ids = (self._previous_ids + [snapshot["snapshot_id"]])[-3:]
        self._snapshot = snapshot
        self._json = body_bytes
        self._published_mono = time.monotonic()
        self.publish_count += 1

    @property
    def snapshot(self):
        return self._snapshot

    @property
    def body(self):
        return self._json

    @property
    def age_seconds(self):
        if self._published_mono is None:
            return None
        return time.monotonic() - self._published_mono
