"""Safely copy references to active tasks. No mutation, no I/O under the lock."""
import asyncio


async def copy_under_lock(lock, copy_fn, timeout=0.5):
    """Run ``copy_fn`` (synchronous, no awaits) while briefly holding ``lock``.

    If the lock cannot be taken quickly we fall back to the same synchronous
    copy. Inside one event loop a synchronous copy cannot interleave with other
    coroutines, so the copy is still consistent; we just report it.
    Returns (result, lock_wait_ms, used_lock).
    """
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    acquired = False
    if lock is not None:
        try:
            await asyncio.wait_for(lock.acquire(), timeout)
            acquired = True
        except (asyncio.TimeoutError, Exception):
            acquired = False
    try:
        result = copy_fn()  # synchronous: nothing awaited while the lock is held
    finally:
        if acquired:
            try:
                lock.release()
            except Exception:
                pass
    return result, round((loop.time() - t0) * 1000, 2), acquired


async def collect_task_refs(state, timeout=0.5):
    """Return ([(key, status_wrapper), ...], info) copied from ``task_dict``."""
    td = state.task_dict()
    items, wait_ms, used_lock = await copy_under_lock(
        state.task_dict_lock(), lambda: list(td.items()), timeout
    )
    return items, {"lock_wait_ms": wait_ms, "lock_used": used_lock}
