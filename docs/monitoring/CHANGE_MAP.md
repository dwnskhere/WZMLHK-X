# Source change map and non-regression report

## Existing files touched (4) — purely additive, 0 lines removed (`integration_hooks.diff`)

| File | Why | Exact minimal change | Risk | Regression check |
|---|---|---|---|---|
| `bot/__main__.py` | Start optional monitoring after WZML is initialized | `try: from .monitoring import start_monitoring; bot_loop.create_task(start_monitoring()) except Exception: log` after `restart_notification` task | Import/start errors caught; function itself never raises and is a no-op unless `MONITOR_API_ENABLED=true` | start_monitoring disabled/failure tests |
| `web/wserver.py` | Register public proxy router | `try: app.include_router(router) except Exception: log` right after `app = FastAPI(...)` | Only adds `/monitor/api/v1/status`; existing routes unchanged | route test: only GET, others 405 |
| `bot/modules/restart.py` | Release bridge port/tasks before restart | `try: await stop_monitoring() except: pass` before `mega_cleanup()` | Bounded by 5 s timeout, exceptions swallowed | stop is idempotent |
| `bot/helper/listeners/task_listener.py` | Lifecycle events | 1 guarded import (no-op fallback) + 7 single-line `record_lifecycle_event(...)` calls at the top of `on_download_start`, `on_download_complete` (after the cancel check), `on_upload_complete`, `on_download_error`, `on_upload_error` | Synchronous, O(1), no I/O, never raises, never alters success/error decisions; no-op when disabled | hook tests: overflow, garbage input, disabled |

## Verified byte-identical to the supplied archive (sha256)

`Dockerfile`, `start.sh`, `setpkgs.sh`, `requirements.txt` (no new dependency), `update.py`, `bot/__init__.py`,
`bot/core/config_manager.py` (BinConfig untouched), `bot/core/torrent_manager.py`, `bot/core/startup.py`,
`bot/helper/ext_utils/task_manager.py`.

## New files

`bot/monitoring/*` (package), `web/monitoring_client.py`, `web/monitoring_routes.py`, `tests/monitoring/*`, `docs/monitoring/*`.

## Not done on purpose (spec section 3.2)

No aria2/qBittorrent/SABnzbd/FFmpeg binary, startup, Dockerfile, `setpkgs.sh` or `TorrentManager` change.

## Test results

`cd tests/monitoring && python -m pytest -q` -> 17 passed (see `test_results.txt`).
