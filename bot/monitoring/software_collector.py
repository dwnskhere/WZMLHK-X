"""Python/package/native versions and WZML repo version. Slow cadence, thread-safe."""
import os
import subprocess
from importlib import metadata

from .constants import PACKAGES


def collect_software(state, dependencies):
    import sys

    pkgs = {}
    for name in PACKAGES:
        try:
            pkgs[name] = metadata.version(name)
        except Exception:
            pkgs[name] = None
    cache = state.bot_cache() or {}
    native = {d["id"]: {"version": d["version"], "found": d["found"]} for d in dependencies}
    commit = None
    try:
        if os.path.isdir(".git"):
            commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                                    timeout=3, stdin=subprocess.DEVNULL).stdout.strip() or None
    except Exception:
        commit = None
    cfg = state.config()
    return {
        "python": sys.version.split()[0],
        "packages": pkgs,
        "native": native,
        "engine_versions_reported_by_wzml": dict(cache.get("eng_versions", {}) or {}),
        "wzml": {
            "version": os.environ.get("WZML_VERSION") or None,
            "commit": commit,
            "upstream_branch": getattr(cfg, "UPSTREAM_BRANCH", None) or None,
        },
        "last_update_at": None,
    }
