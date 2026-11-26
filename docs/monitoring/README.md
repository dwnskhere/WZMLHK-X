# WZML-HK Monitoring API v1

Read-only, optional status API. **Disabled by default.** It observes WZML and never controls it.

`GET /monitor/api/v1/status` with `Authorization: Bearer <MONITOR_API_TOKEN>`

## Enable (environment variables or config.py)

| Variable | Default | Notes |
|---|---|---|
| `MONITOR_API_ENABLED` | `false` | Set `true` to turn on. |
| `MONITOR_API_TOKEN` | none | **Required.** Enabled without a token = feature stays off (fails closed). |
| `MONITOR_INTERNAL_PORT` | `8765` | Private loopback bridge in the bot process. Not exposed publicly. |
| `MONITOR_INTERNAL_SECRET` | derived from token | Optional; distinct from the public token. |
| `MONITOR_INSTANCE_ID` | `default` | Your unique installation id. |
| `MONITOR_SNAPSHOT_MAX_AGE` | `30` | Seconds before `meta.is_stale` is true. |
| `MONITOR_MAX_TASKS_PER_RESPONSE` | `500` | Page cap; truncation is explicit (`meta.pagination`). |
| `MONITOR_EVENTS_RESPONSE_LIMIT` | `50` | Recent events returned. |
| `MONITOR_HISTORY_ENABLED` / `_RETENTION_DAYS` | `true` / `30` | Uses existing MongoDB only if connected. |
| `MONITOR_EVENT_QUEUE_SIZE` | `1000` | Bounded event buffer. |
| `MONITOR_ENGINE_CONCURRENCY` | `2` | Per-engine probe limit. |
| `MONITOR_PROBE_TIMEOUT` | `5` | Seconds for engine/dependency probes. |
| `MONITOR_RATE_LIMIT_PER_MINUTE` | `120` | Per client IP, public route. |

Use **environment variables** (Heroku Config Vars). Values saved only in MongoDB deploy-config are not visible to the web process.
Collection cadence follows WZML `STATUS_UPDATE_INTERVAL` (15 s default).

```bash
curl -H "Authorization: Bearer $TOKEN" "https://<app>.herokuapp.com/monitor/api/v1/status"
curl -H "Authorization: Bearer $TOKEN" "https://<app>/monitor/api/v1/status?sections=summary,tasks,queues&tasks_limit=100"
```

Query: `sections` (default `all`), `tasks_limit`, `events_limit`, `cursor` (from `meta.pagination.tasks.next_cursor`).
Errors: 400 invalid query, 401 bad/missing token, 429 rate limit, 503 not ready/disabled/bridge down, 504 bridge timeout.
Schema: `openapi.yaml`. Full example: `example_response.json`.

## Architecture

```
caller -> existing FastAPI (gunicorn) [auth, rate limit] -> 127.0.0.1:8765 bridge (bot process, secret header)
                                                              -> immutable cached snapshot <- background collector
```
The web process never imports `bot`. API requests never poll engines, run shell commands or query MongoDB.

## Behaviour guarantees (verified by tests/monitoring)

* Collector: no overlapping cycles; WZML locks held only for a synchronous dict copy; one aggregate read per engine per cycle (aria2: 3 RPCs, qBittorrent: 2, SABnzbd: 1) regardless of task count; per-engine timeout + circuit breaker; a failed cycle never replaces a good snapshot.
* Missing binaries (`blitzfetcher`, `stormtorrent`, ...) appear as `DEPENDENCY_MISSING` warnings and engine `unavailable`; nothing is started, renamed or "fixed".
* No raw URLs, local paths, tokens, Telegram user ids/usernames/text, process usernames or Config dump are serialized.
* Events: `task.started`, `stage_completed`, `seeding_started`, `succeeded`, `failed`, `cancelled`, `rejected`. Cancel vs failure uses WZML's own `is_cancelled` flag, not wording. History is reported `durable` only after a successful Mongo write, else `memory_only`/`unavailable`.

## Known limitations

1. This feature does **not** fix the deployment crash (`FileNotFoundError: stormtorrent`). That happens in `bot/__init__.py` before monitoring starts and must be solved separately (a base image that ships the renamed binaries). Monitoring only starts once WZML itself starts.
2. JDownloader and Seedr have no aggregate API; their numbers come from the wrapper's already-cached data (`transfer.quality: "cached"`), which refreshes only when WZML's own status loop runs. SABnzbd verifying/repairing phases read the queue only.
3. Task end after seeding (ratio reached) is not hooked, so a seeding task has `task.seeding_started` but no later terminal event. `task.stopped` is reserved and unused.
4. Dependency versions are probed at startup and every 5 minutes in a worker thread.
5. Validated offline with fakes of all 17 status wrappers and a real loopback bridge + FastAPI route. Not yet run against a live WZML instance with real engines.
