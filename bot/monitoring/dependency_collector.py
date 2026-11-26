"""Read-only binary presence and bounded version probes (run off the event loop)."""
import os
import shutil
import subprocess
import time

from .constants import DEPENDENCIES
from .util import clean_text, utc_now_iso


def _first_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return clean_text(line, 120)
    return None


def probe_dependencies(bin_config, config, timeout=5.0, with_versions=True):
    items = []
    torrents_off = bool(getattr(config, "DISABLE_TORRENTS", False)) if config else False
    for dep_id, attr, fallback, display, ver_args, crit, features in DEPENDENCIES:
        name = (getattr(bin_config, attr, None) if attr and bin_config else None) or fallback
        path = shutil.which(name)
        found = path is not None
        executable = bool(path and os.access(path, os.X_OK))
        version, result = None, "not_found"
        if found and with_versions:
            try:
                args = [path, *[a for a in ver_args if a]]
                proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                      stdin=subprocess.DEVNULL)
                version = _first_line(proc.stdout) or _first_line(proc.stderr)
                result = "ok" if version else "no_output"
            except subprocess.TimeoutExpired:
                result = "timeout"
            except Exception as exc:
                result = f"error:{type(exc).__name__}"
        elif found:
            result = "not_probed"
        criticality = crit
        if crit == "required_unless_torrents_disabled":
            criticality = "optional" if torrents_off else "required"
        items.append({
            "id": dep_id, "display_name": display, "executable": name, "found": found,
            "executable_permission": executable, "version": version, "probe_result": result,
            "criticality": criticality, "features": list(features),
            "last_checked_at": utc_now_iso(time.time()),
        })
    return items
