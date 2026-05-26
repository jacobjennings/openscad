#!/usr/bin/env python3
"""
OpenSCAD compile/render benchmark harness.

Measures end-to-end wall time for batch export and records SHA-256 digests of
binary STL output for bit-identical correctness checks against a baseline.

Subcommands:
  baseline   Write benchmarks/baselines/<name>.json (submodule)
  compare    Run corpus and diff vs baseline (exit 1 on digest/time regression)
  run        Run corpus and write benchmarks/results/runs/<id>/manifest.json
  cycle      run + profiles + agent_report.md (for automated optimize loops)

Environment:
  OPENSCAD_BINARY  Default path to openscad executable (overridden by --openscad).
  OPENSCADPATH     Passed through to child processes (e.g. libraries/MCAD).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from profile_util import (
    find_perf,
    profile_result_to_manifest_entry,
    rel_artifact_path,
    run_profiled,
    _safe_case_dirname,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = REPO_ROOT / "benchmarks" / "corpus-small.lst"
BASELINES_SUBMODULE_DIR = REPO_ROOT / "benchmarks" / "baselines"
RESULTS_RUNS_DIR = REPO_ROOT / "benchmarks" / "results" / "runs"
BASELINES_SETUP_HINT = (
    "Benchmark baselines live in the git submodule at benchmarks/baselines/.\n"
    "  git submodule update --init benchmarks/baselines\n"
    "Or add a new remote:\n"
    "  ./benchmarks/scripts/init_baselines_submodule.sh <git-url>\n"
    "To create a baselines repo from the template:\n"
    "  ./benchmarks/scripts/bootstrap_baselines_repo.sh ~/openscad-benchmark-baselines"
)

DEFAULT_EXPORT_ARGS = [
    "--enable=predictible-output",
    "--render",
    "--export-format",
    "binstl",
    "--quiet",
    "--summary",
    "time",
]


@dataclass
class CaseResult:
    scad: str
    ok: bool
    exit_code: int
    wall_ms: list[float]
    render_ms: int | None
    sha256: str | None
    stl_bytes: int | None
    stderr: str
    deterministic: bool
    profile: list[dict[str, Any]] | None = None


def baselines_checkout_present() -> bool:
    d = BASELINES_SUBMODULE_DIR
    if not d.is_dir():
        return False
    return (d / ".git").exists() or (d / "main.json").is_file()


def require_baselines_checkout() -> Path:
    if baselines_checkout_present():
        return BASELINES_SUBMODULE_DIR
    print(BASELINES_SETUP_HINT, file=sys.stderr)
    print(f"\nMissing directory: {BASELINES_SUBMODULE_DIR}", file=sys.stderr)
    raise SystemExit(1)


def require_baselines_manifest(name: str) -> Path:
    require_baselines_checkout()
    path = BASELINES_SUBMODULE_DIR / f"{name}.json"
    if not path.is_file():
        print(f"Baseline manifest not found: {path}", file=sys.stderr)
        print("Create one with: run_benchmark.py baseline --openscad ...", file=sys.stderr)
        raise SystemExit(1)
    return path


def load_corpus(path: Path) -> list[Path]:
    paths: list[Path] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        p = (REPO_ROOT / line).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Corpus entry not found: {p}")
        paths.append(p)
    return paths


def openscad_version(exe: Path) -> str:
    proc = subprocess.run(
        [str(exe), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    out = (proc.stdout or proc.stderr or "").strip()
    return out.splitlines()[0] if out else "unknown"


def parse_summary_time(stdout: str) -> int | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    time_obj = data.get("time")
    if isinstance(time_obj, dict) and "total" in time_obj:
        return int(time_obj["total"])
    return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _case_profile_dir(run_dir: Path | None, scad_rel: str) -> Path | None:
    if run_dir is None:
        return None
    return run_dir / "profiles" / _safe_case_dirname(scad_rel)


def run_once(
    exe: Path,
    scad: Path,
    export_args: list[str],
    env: dict[str, str],
    *,
    profile_mode: str = "off",
    profile_dir: Path | None = None,
) -> tuple[bool, int, float, str | None, int | None, int | None, str, dict[str, Any] | None]:
    with tempfile.TemporaryDirectory(prefix="openscad-bench-") as tmp:
        stl = Path(tmp) / "out.stl"
        cmd = [str(exe), str(scad), "-o", str(stl), *export_args, "--summary-file", "-"]
        prof_entry: dict[str, Any] | None = None

        if profile_mode != "off" and profile_dir is not None:
            code, profile, stdout, stderr = run_profiled(
                cmd,
                cwd=scad.parent,
                env=env,
                profile_dir=profile_dir,
                mode=profile_mode,
            )
            wall_ms = profile.wall_ms
            prof_entry = profile_result_to_manifest_entry(profile)
            if profile_dir is not None:
                run_root = run_dir_from_profile_dir(profile_dir)
                for key, val in list(prof_entry.get("artifacts", {}).items()):
                    p = Path(val)
                    if p.is_file():
                        prof_entry["artifacts"][key] = rel_artifact_path(p, run_root)
            if code != 0:
                return False, code, wall_ms, None, None, None, stderr[-4000:], prof_entry
            if not stl.is_file() or stl.stat().st_size == 0:
                return (
                    False,
                    code,
                    wall_ms,
                    None,
                    None,
                    None,
                    stderr[-4000:] + "\nMissing or empty STL output",
                    prof_entry,
                )
            digest = sha256_file(stl)
            render_ms = parse_summary_time(stdout)
            return True, 0, wall_ms, digest, stl.stat().st_size, render_ms, stderr[-4000:], prof_entry

        t0 = time.perf_counter()
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            cwd=scad.parent,
        )
        wall_ms = (time.perf_counter() - t0) * 1000.0
        stderr = (proc.stderr or "")[-4000:]
        if proc.returncode != 0:
            return False, proc.returncode, wall_ms, None, None, None, stderr, None
        if not stl.is_file() or stl.stat().st_size == 0:
            return False, proc.returncode, wall_ms, None, None, None, stderr + "\nMissing or empty STL output", None
        digest = sha256_file(stl)
        size = stl.stat().st_size
        render_ms = parse_summary_time(proc.stdout or "")
        return True, 0, wall_ms, digest, size, render_ms, stderr, None


def run_dir_from_profile_dir(profile_dir: Path) -> Path:
    # profiles/<case> -> run root
    return profile_dir.parents[1]


def run_case(
    exe: Path,
    scad: Path,
    export_args: list[str],
    runs: int,
    env: dict[str, str],
    *,
    profile_mode: str = "off",
    run_dir: Path | None = None,
) -> CaseResult:
    rel = scad.relative_to(REPO_ROOT).as_posix()
    wall_samples: list[float] = []
    digests: list[str] = []
    profile_runs: list[dict[str, Any]] = []
    last_digest: str | None = None
    last_size: int | None = None
    last_render_ms: int | None = None
    last_stderr = ""
    exit_code = 0
    ok = True

    case_prof_dir = _case_profile_dir(run_dir, rel)

    for run_idx in range(runs):
        prof_dir = None
        mode = profile_mode
        if case_prof_dir is not None:
            if profile_mode == "record" and run_idx > 0:
                mode = "psutil"
            prof_dir = case_prof_dir / f"run-{run_idx}"
        one_ok, exit_code, wall_ms, digest, size, render_ms, last_stderr, prof = run_once(
            exe,
            scad,
            export_args,
            env,
            profile_mode=mode,
            profile_dir=prof_dir,
        )
        wall_samples.append(wall_ms)
        if prof is not None:
            profile_runs.append(prof)
        if not one_ok:
            ok = False
            break
        assert digest is not None
        digests.append(digest)
        last_digest = digest
        last_size = size
        last_render_ms = render_ms

    deterministic = ok and len(set(digests)) <= 1

    return CaseResult(
        scad=rel,
        ok=ok,
        exit_code=exit_code,
        wall_ms=wall_samples,
        render_ms=last_render_ms,
        sha256=last_digest,
        stl_bytes=last_size,
        stderr=last_stderr,
        deterministic=deterministic,
        profile=profile_runs if profile_runs else None,
    )


def summarize_wall(wall_ms: list[float]) -> dict[str, float]:
    if not wall_ms:
        return {}
    return {
        "min_ms": min(wall_ms),
        "median_ms": statistics.median(wall_ms),
        "max_ms": max(wall_ms),
        "mean_ms": statistics.mean(wall_ms),
    }


def build_manifest(
    exe: Path,
    export_args: list[str],
    corpus: list[Path],
    runs: int,
    env: dict[str, str],
    *,
    profile_mode: str = "off",
    run_dir: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    cases: dict[str, Any] = {}
    for scad in corpus:
        result = run_case(
            exe,
            scad,
            export_args,
            runs,
            env,
            profile_mode=profile_mode,
            run_dir=run_dir,
        )
        entry: dict[str, Any] = {
            "ok": result.ok,
            "exit_code": result.exit_code,
            "wall": summarize_wall(result.wall_ms),
            "runs": len(result.wall_ms),
        }
        if result.render_ms is not None:
            entry["render_ms"] = result.render_ms
        entry["deterministic"] = result.deterministic
        if result.sha256:
            entry["sha256"] = result.sha256
            entry["stl_bytes"] = result.stl_bytes
        if result.profile:
            entry["profile"] = result.profile
        if not result.ok:
            entry["stderr_tail"] = result.stderr
        cases[result.scad] = entry
        status = "ok" if result.ok else "FAIL"
        med = entry.get("wall", {}).get("median_ms", "?")
        digest = (result.sha256 or "")[:12]
        det = "" if result.deterministic else " NONDETERMINISTIC"
        print(f"  [{status}] {result.scad}  median={med}ms  sha256={digest}...{det}", file=sys.stderr)

    manifest: dict[str, Any] = {
        "format_version": 2,
        "platform": platform.platform(),
        "openscad": str(exe.resolve()),
        "openscad_version": openscad_version(exe),
        "export_args": export_args,
        "cases": cases,
    }
    if run_id:
        manifest["run_id"] = run_id
    if run_dir:
        manifest["run_dir"] = str(run_dir.relative_to(REPO_ROOT))
    if profile_mode != "off":
        manifest["profile_mode"] = profile_mode
        manifest["perf_available"] = find_perf() is not None
    return manifest


def compare_manifests(
    baseline: dict[str, Any],
    current: dict[str, Any],
    max_regression_ratio: float,
) -> int:
    errors = 0

    if baseline.get("export_args") != current.get("export_args"):
        print(
            "WARNING: export_args differ between baseline and current run; "
            "digest comparison may not be meaningful.",
            file=sys.stderr,
        )

    base_cases = baseline.get("cases", {})
    cur_cases = current.get("cases", {})

    for name, base in base_cases.items():
        cur = cur_cases.get(name)
        if cur is None:
            print(f"MISSING case in current run: {name}", file=sys.stderr)
            errors += 1
            continue
        if not cur.get("ok"):
            print(f"FAIL export: {name} (exit {cur.get('exit_code')})", file=sys.stderr)
            errors += 1
            continue
        if base.get("deterministic") is False:
            print(f"  {name}: skipping digest check (marked nondeterministic in baseline)", file=sys.stderr)
            continue
        if cur.get("deterministic") is False:
            print(f"WARN: {name} is nondeterministic in current run", file=sys.stderr)
            continue
        b_hash = base.get("sha256")
        c_hash = cur.get("sha256")
        if b_hash and c_hash and b_hash != c_hash:
            print(f"DIGEST MISMATCH: {name}", file=sys.stderr)
            print(f"  baseline: {b_hash}", file=sys.stderr)
            print(f"  current:  {c_hash}", file=sys.stderr)
            errors += 1
        elif b_hash and c_hash and b_hash == c_hash:
            b_med = base.get("wall", {}).get("median_ms")
            c_med = cur.get("wall", {}).get("median_ms")
            if b_med and c_med:
                ratio = c_med / b_med
                pct = (ratio - 1.0) * 100.0
                tag = "faster" if ratio < 1.0 else "slower"
                print(f"  {name}: {c_med:.1f}ms vs {b_med:.1f}ms ({pct:+.1f}% {tag})", file=sys.stderr)
                if ratio > max_regression_ratio:
                    print(
                        f"REGRESSION: {name} exceeded {max_regression_ratio:.2f}x baseline",
                        file=sys.stderr,
                    )
                    errors += 1

    for name in cur_cases:
        if name not in base_cases:
            print(f"NEW case (not in baseline): {name}", file=sys.stderr)

    return 1 if errors else 0


def write_agent_report(
    run_dir: Path,
    baseline: dict[str, Any],
    current: dict[str, Any],
    compare_rc: int,
) -> Path:
    report = run_dir / "agent_report.md"
    lines = [
        "# OpenSCAD benchmark cycle report",
        "",
        f"- **Run directory:** `{run_dir.relative_to(REPO_ROOT)}`",
        f"- **Compare result:** {'PASS' if compare_rc == 0 else 'FAIL'}",
        f"- **OpenSCAD:** {current.get('openscad_version', '?')}",
        f"- **Profile mode:** {current.get('profile_mode', 'off')}",
        f"- **perf(1) available:** {current.get('perf_available', False)}",
        "",
        "## Timing vs baseline",
        "",
        "| Case | Baseline ms | Current ms | Delta | Digest |",
        "|------|-------------|------------|-------|--------|",
    ]

    for name, base in baseline.get("cases", {}).items():
        cur = current.get("cases", {}).get(name, {})
        b_med = base.get("wall", {}).get("median_ms", 0)
        c_med = cur.get("wall", {}).get("median_ms", 0)
        if b_med:
            pct = (c_med / b_med - 1.0) * 100.0
            delta = f"{pct:+.1f}%"
        else:
            delta = "n/a"
        digest_ok = "skip"
        if base.get("deterministic") is not False and cur.get("deterministic") is not False:
            digest_ok = "ok" if base.get("sha256") == cur.get("sha256") else "**MISMATCH**"
        lines.append(f"| `{name}` | {b_med:.1f} | {c_med:.1f} | {delta} | {digest_ok} |")

    lines.extend(["", "## Profile artifacts (per case)", ""])
    for name, cur in current.get("cases", {}).items():
        prof = cur.get("profile")
        if not prof:
            continue
        last = prof[-1]
        lines.append(f"### `{name}`")
        if last.get("max_rss_mb") is not None:
            lines.append(f"- max RSS: {last['max_rss_mb']:.1f} MiB")
        if last.get("cpu_user_ms") is not None:
            lines.append(f"- CPU user: {last['cpu_user_ms']:.0f} ms")
        run_rel = current.get("run_dir", "")
        for k, v in last.get("artifacts", {}).items():
            lines.append(f"- {k}: `{run_rel}/{v}`")
        lines.append("")

    lines.extend(
        [
            "## Agent next steps",
            "",
            "1. Read profile JSON under `profiles/` (psutil.json or perf-stat.json).",
            "2. If `perf.data` exists, run `perf report -i <path>` or open in Hotspot.",
            "3. Apply a focused code change; rebuild OpenSCAD.",
            "4. Re-run: `./benchmarks/run_benchmark.py cycle --openscad ./build/openscad`",
            "",
        ]
    )

    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def new_run_dir() -> tuple[str, Path]:
    run_id = time.strftime("%Y%m%dT%H%M%S")
    run_dir = RESULTS_RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    latest = RESULTS_RUNS_DIR / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink(missing_ok=True)
    latest.symlink_to(run_id, target_is_directory=True)
    return run_id, run_dir


def resolve_openscad(path: str | None) -> Path:
    if path:
        candidate = path
    elif os.environ.get("OPENSCAD_BINARY"):
        candidate = os.environ["OPENSCAD_BINARY"]
    else:
        candidate = "openscad"
    resolved = shutil.which(candidate) if "/" not in candidate and "\\" not in candidate else candidate
    exe = Path(resolved or candidate)
    if not exe.is_file():
        raise FileNotFoundError(f"OpenSCAD binary not found: {candidate}")
    return exe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("run", "baseline", "compare", "cycle"),
        help="run/cycle: timed run; baseline: write submodule manifest; compare/cycle: diff vs baseline",
    )
    parser.add_argument("--openscad", help="Path to openscad executable")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS, help="Corpus list file")
    parser.add_argument("--runs", type=int, default=3, help="Repeats per .scad (median reported)")
    parser.add_argument("--baseline", type=Path, help="Baseline manifest for compare")
    parser.add_argument("--name", default="main", help="Baseline filename stem")
    parser.add_argument("--output", type=Path, help="Results JSON (run only, non-cycle)")
    parser.add_argument("--max-regression", type=float, default=1.10)
    parser.add_argument("--backend", choices=("manifold", "cgal"))
    parser.add_argument(
        "--profile",
        choices=("off", "psutil", "stat", "record", "auto"),
        default="off",
        help="Profile each case (auto: perf stat if available else psutil). cycle enables auto by default.",
    )
    args, extra = parser.parse_known_args()

    if args.command == "baseline":
        require_baselines_checkout()
    elif args.command in ("compare", "cycle"):
        require_baselines_manifest(args.name)

    profile_mode = args.profile
    if args.command == "cycle" and profile_mode == "off":
        profile_mode = "auto"

    export_args = list(DEFAULT_EXPORT_ARGS) + extra
    if args.backend:
        export_args.extend(["--backend", args.backend])

    exe = resolve_openscad(args.openscad)
    corpus = load_corpus(args.corpus)
    env = os.environ.copy()
    libs = REPO_ROOT / "libraries"
    if libs.is_dir():
        env.setdefault("OPENSCADPATH", str(libs))

    run_dir: Path | None = None
    run_id: str | None = None
    if args.command in ("run", "cycle"):
        if args.command == "cycle" or profile_mode != "off":
            run_id, run_dir = new_run_dir()
        elif args.output:
            run_dir = args.output.parent

    print(f"OpenSCAD: {exe}", file=sys.stderr)
    print(f"Corpus: {len(corpus)} files from {args.corpus}", file=sys.stderr)
    print(f"Export args: {' '.join(export_args)}", file=sys.stderr)
    if profile_mode != "off":
        print(f"Profile mode: {profile_mode} (perf={find_perf() is not None})", file=sys.stderr)
    if run_dir:
        print(f"Run directory: {run_dir.relative_to(REPO_ROOT)}", file=sys.stderr)

    manifest = build_manifest(
        exe,
        export_args,
        corpus,
        args.runs,
        env,
        profile_mode=profile_mode,
        run_dir=run_dir,
        run_id=run_id,
    )

    if args.command == "run":
        out = args.output
        if out is None and run_dir:
            out = run_dir / "manifest.json"
        elif out is None:
            out_dir = REPO_ROOT / "benchmarks" / "results"
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"run-{int(time.time())}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out}", file=sys.stderr)
        return 0

    if args.command == "baseline":
        out = BASELINES_SUBMODULE_DIR / f"{args.name}.json"
        out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote baseline {out}", file=sys.stderr)
        print("Commit in the baselines submodule, then bump the submodule pointer in the OpenSCAD repo.", file=sys.stderr)
        return 0

    if args.command in ("compare", "cycle"):
        base_path = args.baseline or require_baselines_manifest(args.name)
        baseline = json.loads(base_path.read_text(encoding="utf-8"))
        print(f"Comparing against {base_path}", file=sys.stderr)
        rc = compare_manifests(baseline, manifest, args.max_regression)

        if args.command == "cycle":
            assert run_dir is not None
            manifest_path = run_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            report = write_agent_report(run_dir, baseline, manifest, rc)
            print(f"Wrote {manifest_path.relative_to(REPO_ROOT)}", file=sys.stderr)
            print(f"Wrote {report.relative_to(REPO_ROOT)}", file=sys.stderr)
            print(f"Latest symlink: benchmarks/results/runs/latest", file=sys.stderr)
        return rc

    return 1


if __name__ == "__main__":
    sys.exit(main())
