"""Profiling helpers for the OpenSCAD benchmark harness."""

from __future__ import annotations

import json
import re
import resource
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore


@dataclass
class ProfileResult:
    wall_ms: float
    cpu_user_ms: float | None = None
    cpu_system_ms: float | None = None
    max_rss_mb: float | None = None
    perf_stat: dict[str, Any] | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def find_perf() -> Path | None:
    p = shutil.which("perf")
    return Path(p) if p else None


def _safe_case_dirname(scad_rel: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", scad_rel).strip("_")


def _child_resource_usage() -> tuple[float | None, float | None, float | None]:
    """Linux/macOS: aggregate resource use of waited-for children."""
    try:
        ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    except (OSError, ValueError):
        return None, None, None
    cpu_user = ru.ru_utime * 1000.0
    cpu_system = ru.ru_stime * 1000.0
    # Linux: ru_maxrss in KiB; macOS: bytes
    if sys.platform == "darwin":
        max_rss_mb = ru.ru_maxrss / (1024.0 * 1024.0)
    else:
        max_rss_mb = ru.ru_maxrss / 1024.0
    return cpu_user, cpu_system, max_rss_mb


def _parse_perf_stat_json(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def run_profiled(
    cmd: list[str],
    *,
    cwd: Path | None,
    env: dict[str, str],
    profile_dir: Path,
    mode: str,
) -> tuple[int, ProfileResult, str, str]:
    """
    Run cmd with profiling. Returns exit_code, profile, stdout, stderr.
    mode: off | psutil | stat | record | auto (stat if perf exists else psutil)
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    perf = find_perf()
    if mode == "auto":
        mode = "stat" if perf else "psutil"
    if mode == "off":
        mode = "psutil"

    stdout = ""
    stderr = ""
    t0 = time.perf_counter()

    if mode == "stat" and perf:
        stat_path = profile_dir / "perf-stat.json"
        perf_cmd = [
            str(perf),
            "stat",
            "-j",
            "-e",
            "cycles,instructions,cache-references,cache-misses,branches,branch-misses",
            "-o",
            str(stat_path),
            "--",
            *cmd,
        ]
        proc = subprocess.run(
            perf_cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env,
        )
        wall_ms = (time.perf_counter() - t0) * 1000.0
        stdout, stderr = proc.stdout or "", proc.stderr or ""
        perf_stat = None
        if stat_path.is_file():
            perf_stat = _parse_perf_stat_json(stat_path.read_text(encoding="utf-8"))
        artifacts = {"perf_stat_json": str(stat_path)}
        return (
            proc.returncode,
            ProfileResult(
                wall_ms=wall_ms,
                perf_stat=perf_stat,
                artifacts=artifacts,
            ),
            stdout,
            stderr,
        )

    if mode == "record" and perf:
        data_path = profile_dir / "perf.data"
        script_path = profile_dir / "perf.script.txt"
        perf_cmd = [str(perf), "record", "-g", "-o", str(data_path), "--"] + cmd
        proc = subprocess.run(
            perf_cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env,
        )
        wall_ms = (time.perf_counter() - t0) * 1000.0
        stdout, stderr = proc.stdout or "", proc.stderr or ""
        artifacts: dict[str, str] = {}
        if data_path.is_file():
            artifacts["perf_data"] = str(data_path)
            try:
                sp = subprocess.run(
                    [str(perf), "script", "-i", str(data_path)],
                    check=False,
                    capture_output=True,
                    text=True,
                    cwd=cwd,
                    env=env,
                )
                if sp.stdout:
                    script_path.write_text(sp.stdout, encoding="utf-8")
                    artifacts["perf_script"] = str(script_path)
            except OSError:
                pass
        return (
            proc.returncode,
            ProfileResult(wall_ms=wall_ms, artifacts=artifacts),
            stdout,
            stderr,
        )

    # psutil mode: wall clock + child rusage (no perf required)
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )
    wall_ms = (time.perf_counter() - t0) * 1000.0
    out, err = proc.stdout or "", proc.stderr or ""
    cpu_user, cpu_system, max_rss = _child_resource_usage()
    metrics_path = profile_dir / "psutil.json"
    metrics = {
        "cpu_user_ms": cpu_user,
        "cpu_system_ms": cpu_system,
        "max_rss_mb": max_rss,
    }
    metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    return (
        proc.returncode,
        ProfileResult(
            wall_ms=wall_ms,
            cpu_user_ms=cpu_user,
            cpu_system_ms=cpu_system,
            max_rss_mb=max_rss,
            artifacts={"psutil_json": str(metrics_path)},
        ),
        out,
        err,
    )


def rel_artifact_path(path: Path, run_dir: Path) -> str:
    try:
        return str(path.relative_to(run_dir))
    except ValueError:
        return str(path)


def profile_result_to_manifest_entry(profile: ProfileResult) -> dict[str, Any]:
    entry: dict[str, Any] = {"wall_ms": profile.wall_ms}
    if profile.cpu_user_ms is not None:
        entry["cpu_user_ms"] = profile.cpu_user_ms
    if profile.cpu_system_ms is not None:
        entry["cpu_system_ms"] = profile.cpu_system_ms
    if profile.max_rss_mb is not None:
        entry["max_rss_mb"] = profile.max_rss_mb
    if profile.perf_stat is not None:
        entry["perf_stat"] = profile.perf_stat
    if profile.artifacts:
        entry["artifacts"] = profile.artifacts
    if profile.error:
        entry["error"] = profile.error
    return entry
