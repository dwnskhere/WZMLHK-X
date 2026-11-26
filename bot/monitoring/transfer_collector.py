"""Transfer scopes kept separate; bandwidth accounting is side-effect free."""
from .util import num, pct, utc_now_iso


def build_transfer(tasks, snaps, system, state, config):
    per_engine = {}
    totals = {"download_bps": 0, "upload_bps": 0, "seeding_bps": 0}
    for t in tasks:
        tr, eng = t["transfer"], t["engine"]["id"]
        slot = per_engine.setdefault(eng, {"download_bps": 0, "upload_bps": 0, "seeding_bps": 0, "tasks": 0})
        slot["tasks"] += 1
        for k in ("download_bps", "upload_bps", "seeding_bps"):
            v = tr.get(k)
            if v:
                slot[k] += v
                totals[k] += v
    engine_native = {}
    for eng, sp in snaps.global_speed.items():
        engine_native[eng] = {**sp, "provenance": "engine_global_stat"}
    net = (system or {}).get("network") or {}
    return {
        "task_engine": {
            "per_task": "see tasks[].transfer",
            "per_engine": per_engine,
            "overall": totals,
            "engine_native_aggregate": engine_native,
            "method": "sum of per-task rates from adapters; engine aggregates reported separately "
                      "and are NOT added to avoid double counting",
        },
        "host_network": {
            "scope": "host_or_container",
            "note": "Host/container-wide traffic; not WZML-only.",
            "bytes_sent_total": net.get("bytes_sent_total"), "bytes_recv_total": net.get("bytes_recv_total"),
            "packets_sent_total": net.get("packets_sent_total"), "packets_recv_total": net.get("packets_recv_total"),
            "send_bps": net.get("send_bps"), "recv_bps": net.get("recv_bps"),
        },
        "bandwidth_accounting": bandwidth_accounting(state, config),
    }


def bandwidth_accounting(state, config):
    """Compute from WZML's maintained counters without calling get_bandwidth_string()."""
    limit_gb = num(getattr(config, "MONTHLY_BANDWIDTH", 0), 0) or 0
    limit_bytes = int(limit_gb * 1024**3) if limit_gb else None
    base = {"source": "wzml_accounting_counters_read_only", "period_start": None, "period_end": None,
            "monthly_limit_bytes": limit_bytes}
    counters = state.bandwidth_counters()
    if counters is None:
        return {**base, "used_bytes": None, "percent_used": None, "alert_state": "unknown",
                "available": False}
    try:
        import psutil

        io = psutil.net_io_counters()
        raw = io.bytes_sent + io.bytes_recv
    except Exception:
        return {**base, "used_bytes": None, "percent_used": None, "alert_state": "unknown", "available": False}
    hist, last = num(counters["historical"], 0) or 0, num(counters["last_raw"], 0) or 0
    if raw < last:  # counter reset: mirror WZML's math without mutating its globals
        hist += last
    used = int(hist + raw)
    p = pct(used, limit_bytes) if limit_bytes else None
    alert = "disabled" if not limit_bytes else "exceeded" if p >= 100 else "critical" if p >= 90 else "warning" if p >= 80 else "ok"
    return {**base, "used_bytes": used, "percent_used": p, "alert_state": alert, "available": True,
            "meaning": "host/container network bytes accumulated by WZML's own counter"}
