"""Host/container metrics via psutil. Sampled in a worker thread, never on the loop."""
import os
import platform
import sys
import time

from .identity import STARTED_AT


class SystemSampler:
    def __init__(self, download_dir="/"):
        self.download_dir = download_dir
        self._prev_io = None
        self._prev_net = None
        self._last_procs = []
        self._last_procs_at = 0.0
        self._primed = False

    def sample(self, with_processes=False):
        import psutil

        now = time.monotonic()
        out = {}
        if not self._primed:
            psutil.cpu_percent(interval=None)
            self._primed = True
        cpu = {"percent": psutil.cpu_percent(interval=None),
               "logical_cores": psutil.cpu_count(logical=True),
               "physical_cores": psutil.cpu_count(logical=False)}
        try:
            cpu["usable_cores"] = len(os.sched_getaffinity(0))
        except Exception:
            cpu["usable_cores"] = None
        try:
            f = psutil.cpu_freq()
            cpu["frequency_mhz"] = round(f.current, 1) if f else None
        except Exception:
            cpu["frequency_mhz"] = None
        try:
            cpu["load_average"] = dict(zip(("1m", "5m", "15m"), (round(x, 2) for x in os.getloadavg())))
        except Exception:
            cpu["load_average"] = None
        out["cpu"] = cpu
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        out["memory"] = {"total_bytes": vm.total, "used_bytes": vm.used, "available_bytes": vm.available,
                         "percent": vm.percent}
        out["swap"] = {"total_bytes": sw.total, "used_bytes": sw.used, "free_bytes": sw.free, "percent": sw.percent}

        def du(path):
            try:
                u = psutil.disk_usage(path)
                return {"total_bytes": u.total, "used_bytes": u.used, "free_bytes": u.free, "percent": u.percent}
            except Exception:
                return None

        try:
            same = os.stat("/").st_dev == os.stat(self.download_dir).st_dev
        except Exception:
            same = None
        out["filesystem"] = {"root": du("/"), "downloads": du(self.download_dir), "same_device": same}

        try:
            io = psutil.disk_io_counters()
        except Exception:
            io = None
        if io:
            rate_r = rate_w = None
            if self._prev_io and now > self._prev_io[0]:
                dt = now - self._prev_io[0]
                rate_r = max(0, int((io.read_bytes - self._prev_io[1]) / dt))
                rate_w = max(0, int((io.write_bytes - self._prev_io[2]) / dt))
            self._prev_io = (now, io.read_bytes, io.write_bytes)
            out["disk_io"] = {"read_bytes_total": io.read_bytes, "write_bytes_total": io.write_bytes,
                              "read_bps": rate_r, "write_bps": rate_w,
                              "read_time_ms": getattr(io, "read_time", None),
                              "write_time_ms": getattr(io, "write_time", None)}
        else:
            out["disk_io"] = None

        net = psutil.net_io_counters()
        rate_s = rate_r = None
        if self._prev_net and now > self._prev_net[0]:
            dt = now - self._prev_net[0]
            rate_s = max(0, int((net.bytes_sent - self._prev_net[1]) / dt))
            rate_r = max(0, int((net.bytes_recv - self._prev_net[2]) / dt))
        self._prev_net = (now, net.bytes_sent, net.bytes_recv)
        out["network"] = {"scope": "host_or_container", "bytes_sent_total": net.bytes_sent,
                          "bytes_recv_total": net.bytes_recv, "packets_sent_total": net.packets_sent,
                          "packets_recv_total": net.packets_recv, "send_bps": rate_s, "recv_bps": rate_r}

        proc = psutil.Process()
        with proc.oneshot():
            runtime = {"pid": proc.pid, "uptime_seconds": int(time.time() - proc.create_time()),
                       "rss_bytes": proc.memory_info().rss, "cpu_percent": proc.cpu_percent(interval=None),
                       "threads": proc.num_threads()}
        runtime["host_uptime_seconds"] = int(time.time() - psutil.boot_time())
        out["runtime"] = runtime
        out["os"] = {"system": platform.system(), "release": platform.release(),
                     "version": platform.version()[:120], "architecture": platform.machine(),
                     "python": sys.version.split()[0]}
        if with_processes or now - self._last_procs_at > 60:
            procs = []
            for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
                i = p.info
                procs.append({"name": (i.get("name") or "")[:60], "pid": i.get("pid"),
                              "cpu_percent": round(i.get("cpu_percent") or 0, 1),
                              "memory_percent": round(i.get("memory_percent") or 0, 1)})
            procs.sort(key=lambda x: (x["cpu_percent"], x["memory_percent"]), reverse=True)
            self._last_procs, self._last_procs_at = procs[:10], now
        out["processes"] = list(self._last_procs)
        return out
