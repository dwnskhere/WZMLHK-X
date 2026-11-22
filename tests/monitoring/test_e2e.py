import asyncio
import json
import os
import socket
import sys
import time
from types import SimpleNamespace as NS

import httpx
import pytest
from fastapi import FastAPI

from bot.monitoring.internal_server import InternalServer
from fakes import *  # noqa
from test_monitoring import build

pytestmark = pytest.mark.asyncio(loop_scope="function")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def web_app(env):
    os.environ.update(env)
    for m in [m for m in sys.modules if m.startswith("web.monitoring")]:
        del sys.modules[m]
    import web.monitoring_routes as r

    r._SETTINGS = None
    r._HITS.clear()
    app = FastAPI()
    app.include_router(r.router)
    return app


@pytest.fixture
def envguard():
    keys = [k for k in os.environ if k.startswith("MONITOR_")]
    saved = {k: os.environ[k] for k in keys}
    yield
    for k in [k for k in os.environ if k.startswith("MONITOR_")]:
        del os.environ[k]
    os.environ.update(saved)


async def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_public_route_end_to_end(envguard):
    port = free_port()
    env = {"MONITOR_API_ENABLED": "true", "MONITOR_API_TOKEN": "s3cret", "MONITOR_INTERNAL_PORT": str(port),
           "MONITOR_RATE_LIMIT_PER_MINUTE": "1000"}
    os.environ.update(env)
    td = {i: sync_wrapper("DirectStatus", listener(i), "Download", NS(processed_bytes=10, speed=5)) for i in range(3)}
    st, rec, store, hist, col, svc = build(FakeState(td), MONITOR_API_TOKEN="s3cret", MONITOR_INTERNAL_PORT=str(port))
    server = InternalServer(st, svc)
    await server.start()
    try:
        app = web_app(env)
        async with await client_for(app) as c:
            # bridge up but no snapshot yet -> retryable 503 passthrough
            r = await c.get("/monitor/api/v1/status", headers={"Authorization": "Bearer s3cret"})
            assert r.status_code == 503 and r.json()["error"]["code"] == "MONITOR_NOT_READY" and r.json()["error"]["retryable"]
            await col.cycle()
            r = await c.get("/monitor/api/v1/status")
            assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
            assert (await c.get("/monitor/api/v1/status", headers={"Authorization": "Bearer nope"})).status_code == 401
            assert (await c.get("/monitor/api/v1/status?token=s3cret")).status_code == 401  # never accepted in URL
            r = await c.get("/monitor/api/v1/status", headers={"Authorization": "Bearer s3cret"})
            assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
            assert "access-control-allow-origin" not in r.headers
            j = r.json()
            assert j["success"] and len(j["data"]["tasks"]) == 3 and j["meta"]["is_stale"] is False
            r = await c.get("/monitor/api/v1/status?sections=summary&tasks_limit=1", headers={"Authorization": "Bearer s3cret"})
            assert list(r.json()["data"]) == ["summary"]
            r = await c.get("/monitor/api/v1/status?sections=bad", headers={"Authorization": "Bearer s3cret"})
            assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_SECTION"
            assert (await c.post("/monitor/api/v1/status", headers={"Authorization": "Bearer s3cret"})).status_code == 405
            assert (await c.delete("/monitor/api/v1/status")).status_code == 405
            assert (await c.get("/monitor/api/v1/other")).status_code == 404
        # private bridge protections
        async with httpx.AsyncClient() as raw:
            url = f"http://127.0.0.1:{port}/internal/v1/snapshot"
            assert (await raw.get(url)).status_code == 401
            assert (await raw.get(url, headers={"X-Monitor-Secret": "s3cret"})).status_code == 401  # public token != secret
            assert (await raw.post(url, headers={"X-Monitor-Secret": st.internal_secret})).status_code == 405
            assert (await raw.get(url, headers={"X-Monitor-Secret": st.internal_secret})).status_code == 200
        # bridge goes away -> 503 with sanitized body, no ports/paths
        await server.stop()
        app = web_app(env)
        async with await client_for(app) as c:
            r = await c.get("/monitor/api/v1/status", headers={"Authorization": "Bearer s3cret"})
            assert r.status_code == 503 and r.json()["error"]["code"] == "MONITOR_BRIDGE_UNAVAILABLE"
            assert str(port) not in r.text
    finally:
        await server.stop()


async def test_disabled_rate_limit_and_no_bot_import(envguard):
    app = web_app({"MONITOR_API_ENABLED": "false"})
    async with await client_for(app) as c:
        r = await c.get("/monitor/api/v1/status", headers={"Authorization": "Bearer x"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "MONITOR_DISABLED"
    app = web_app({"MONITOR_API_ENABLED": "true", "MONITOR_API_TOKEN": "", "MONITOR_RATE_LIMIT_PER_MINUTE": "1"})
    os.environ.pop("MONITOR_API_TOKEN", None)
    async with await client_for(app) as c:
        assert (await c.get("/monitor/api/v1/status")).status_code == 503  # enabled w/o token fails closed
    app = web_app({"MONITOR_API_ENABLED": "true", "MONITOR_API_TOKEN": "t", "MONITOR_RATE_LIMIT_PER_MINUTE": "2",
                   "MONITOR_INTERNAL_PORT": str(free_port())})
    async with await client_for(app) as c:
        codes = [(await c.get("/monitor/api/v1/status", headers={"Authorization": "Bearer t"})).status_code for _ in range(4)]
        assert codes[:2] == [503, 503] and codes[2:] == [429, 429]
    # the web process must never import the bot package (it would start WZML services)
    src = open(os.path.join(os.path.dirname(__file__), "../../web/monitoring_client.py")).read()
    src += open(os.path.join(os.path.dirname(__file__), "../../web/monitoring_routes.py")).read()
    assert "import bot" not in src and "from bot" not in src


async def test_load_500_tasks_concurrent_callers_and_loop_lag(envguard):
    td = {i: sync_wrapper("DirectStatus", listener(i), "Download", NS(processed_bytes=i, speed=3)) for i in range(500)}
    aria = FakeAria2(active=[{"gid": f"g{i}", "status": "active", "totalLength": "100", "completedLength": "5",
                              "downloadSpeed": "1", "uploadSpeed": "0"} for i in range(500)])
    for i in range(500, 700):
        td[i] = Aria2Status(listener(i), f"g{i - 500}")
    st, rec, store, hist, col, svc = build(FakeState(td, aria2=aria), MONITOR_MAX_TASKS_PER_RESPONSE="5000")
    await col.cycle()  # warm-up (imports, first dependency/system probes)
    lags, stop = [], False

    async def lag_probe():
        while not stop:
            t = time.perf_counter()
            await asyncio.sleep(0.005)
            lags.append(time.perf_counter() - t - 0.005)

    probe = asyncio.create_task(lag_probe())
    t0 = time.perf_counter()
    await col.cycle()
    cycle_s = time.perf_counter() - t0

    async def caller():
        await asyncio.sleep(0)  # each real request is its own event-loop callback
        t = time.perf_counter()
        code, body = svc.render({})  # synchronous: the loop-blocking cost per request
        return code, len(body), time.perf_counter() - t

    res = []
    for _ in range(200):  # 200 callers, interleaved with the event loop like the aiohttp server does
        res.append(await caller())
    stop = True
    await probe
    assert all(c == 200 for c, _, _ in res)
    assert len(json.loads(svc.render({})[1])["data"]["tasks"]) == 700
    worst_req, mean_req = max(r[2] for r in res), sum(r[2] for r in res) / len(res)
    print(f"\nLOAD tasks=700 cycle={cycle_s*1000:.0f}ms render_worst={worst_req*1000:.1f}ms "
          f"render_mean={mean_req*1000:.2f}ms max_loop_lag={max(lags)*1000:.1f}ms "
          f"bytes={res[0][1]} aria2_rpc_per_cycle=3 skipped={col.skipped}")
    assert worst_req < 0.1 and max(lags) < 0.1 and col.skipped == 0
