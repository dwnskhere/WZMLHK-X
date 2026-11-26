"""Private loopback bridge inside the bot process (read-only, GET only)."""
import hmac
import ipaddress

from aiohttp import web

from .models import error_body

MAX_QS = 2048


def _is_loopback(addr):
    try:
        return ipaddress.ip_address(addr).is_loopback
    except Exception:
        return False


def build_app(settings, service):
    @web.middleware
    async def guard(request, handler):
        if not _is_loopback(request.remote or ""):
            return web.Response(status=403, body=error_body("UNAUTHORIZED", "Forbidden."),
                                content_type="application/json")
        given = request.headers.get("X-Monitor-Secret", "")
        if not hmac.compare_digest(given.encode(), settings.internal_secret.encode()):
            return web.Response(status=401, body=error_body("UNAUTHORIZED", "Unauthorized."),
                                content_type="application/json")
        return await handler(request)

    async def snapshot(request):
        if len(request.query_string) > MAX_QS:
            return web.Response(status=400, body=error_body("INVALID_QUERY", "Query too long."),
                                content_type="application/json")
        status, body = service.render(dict(request.query), via_proxy=request.headers.get("X-Monitor-Via") == "public-proxy")
        return web.Response(status=status, body=body, content_type="application/json",
                            headers={"Cache-Control": "no-store"})

    async def health(request):
        return web.json_response({"ok": True})

    app = web.Application(middlewares=[guard], client_max_size=1024)
    app.router.add_get("/internal/v1/snapshot", snapshot)
    app.router.add_get("/internal/v1/ping", health)
    return app


class InternalServer:
    def __init__(self, settings, service):
        self.settings, self.service = settings, service
        self.runner = None
        self.state = "stopped"

    async def start(self):
        self.runner = web.AppRunner(build_app(self.settings, self.service), access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.settings.internal_host, self.settings.internal_port)
        await site.start()
        self.state = "running"

    async def stop(self):
        self.state = "stopped"
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
