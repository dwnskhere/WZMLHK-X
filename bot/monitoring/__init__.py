"""Optional, read-only WZML monitoring subsystem.

Importing this package is cheap and side-effect free. ``start_monitoring`` is the
single entry point called by the bot after WZML is initialized; it never raises.
"""
import asyncio

_runtime = {"started": False, "parts": None}


def _log():
    try:
        from bot import LOGGER

        return LOGGER
    except Exception:
        import logging

        return logging.getLogger("monitoring")


async def start_monitoring():
    """Start collector + private bridge if enabled and configured. Never raises."""
    if _runtime["started"]:
        return False
    try:
        from .config import load_settings

        settings = load_settings()
        if not settings.enabled:
            return False
        if not settings.api_token:
            _log().warning("Monitoring API enabled but MONITOR_API_TOKEN is not set; monitoring stays off.")
            return False
        from .collector import Collector
        from .event_recorder import EventRecorder
        from .history_repository import HistoryRepository
        from .hooks import set_recorder
        from .internal_server import InternalServer
        from .runtime_state import RuntimeState
        from .service import StatusService
        from .snapshot_store import SnapshotStore

        state = RuntimeState()
        recorder = EventRecorder(settings.event_queue_size)
        store = SnapshotStore()
        history = HistoryRepository(settings, recorder, state)
        collector = Collector(settings, state, store, recorder, history)
        service = StatusService(settings, store, recorder, history, collector)
        server = InternalServer(settings, service)
        await server.start()
        await history.start()
        set_recorder(recorder)
        collector.start()
        _runtime.update(started=True, parts=(collector, history, server))
        _log().info(f"Monitoring API bridge listening on {settings.internal_host}:{settings.internal_port}")
        return True
    except Exception as exc:
        try:
            from .hooks import set_recorder

            set_recorder(None)
        except Exception:
            pass
        _log().warning(f"Monitoring disabled for this process: {type(exc).__name__}: {exc}")
        return False


async def stop_monitoring(timeout=5.0):
    """Stop monitoring resources. Never raises, bounded by ``timeout``."""
    parts = _runtime.get("parts")
    if not parts:
        return
    _runtime.update(started=False, parts=None)
    try:
        from .hooks import set_recorder

        set_recorder(None)
        for part in parts:
            try:
                await asyncio.wait_for(part.stop(), timeout)
            except Exception:
                pass
    except Exception:
        pass
