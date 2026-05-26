# Agent instructions — OpenSCAD performance work

This document is for **AI agents** optimizing compile/render performance in this fork. Human-oriented detail lives in `benchmarks/README.md`.

## Goal

Improve headless export performance (`openscad model.scad -o out.stl`) while keeping **bit-identical** binary STL output vs a pinned baseline, unless the user explicitly approves intentional mesh changes.

Typical consumer: batch validation in ML pipelines (e.g. spokencad LoRA training).

---

## Prerequisites (check before looping)

Run from the **repository root** (`/home/jakej/openscad` or your clone).

| Requirement | How to verify |
|-------------|----------------|
| Baselines submodule | `test -f benchmarks/baselines/main.json` |
| Built OpenSCAD binary | `test -x ./build/openscad` (prefer fork build over system AppImage) |
| Corpus list | `benchmarks/corpus-small.lst` (extend as needed) |
| `perf` (recommended) | `perf --version` — cycle uses `perf stat` when available |
| Libraries on `OPENSCADPATH` | `libraries/` present → harness sets `OPENSCADPATH` automatically |

Initialize baselines submodule if missing:

```bash
git submodule update --init benchmarks/baselines
```

If the submodule was never added, see `benchmarks/README.md` (bootstrap + `init_baselines_submodule.sh`).

Build OpenSCAD (Release, for meaningful timings):

```bash
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
```

Use **`./build/openscad`** for all benchmark commands unless the user specifies another path.

---

## The improve loop (primary workflow)

Repeat until compare passes and timings improve (or the user stops you).

### Step 1 — Run one agent cycle

```bash
./benchmarks/scripts/agent_cycle.sh --openscad ./build/openscad
```

Equivalent:

```bash
./benchmarks/run_benchmark.py cycle --openscad ./build/openscad --profile auto --runs 2 --max-regression 1.15
```

- **Exit code 0** — digest checks passed and no case exceeded 1.15× baseline median wall time.
- **Exit code 1** — digest mismatch, export failure, or timing regression.

### Step 2 — Read the report (required)

Open **`benchmarks/results/runs/latest/agent_report.md`**.

Use it for:

- Overall **PASS / FAIL**
- Per-case **timing delta** vs baseline
- **Digest** column (`ok` vs `**MISMATCH**`)
- Paths to profile artifacts under `profiles/`

Also read **`benchmarks/results/runs/latest/manifest.json`** if you need raw numbers or nested `profile` / `perf_stat` data.

### Step 3 — Inspect profiling data

Under **`benchmarks/results/runs/latest/profiles/`**, each corpus case has `run-0/`, `run-1/`, …

| File | Meaning |
|------|---------|
| `perf-stat.json` | Hardware counters (`perf stat -j`) when `perf` is installed |
| `psutil.json` | Child CPU time + peak RSS (fallback or supplement) |
| `perf.data` | Only if cycle was run with `--profile record` (heavy) |

Deeper analysis (optional, human or agent with shell):

```bash
RUN=benchmarks/results/runs/latest
perf report -i "$RUN/profiles/<case_dir>/run-0/perf.data"   # after --profile record
```

Use profiling to pick **one** subsystem to change (evaluator, cache, Manifold op, export tessellation, etc.). Avoid drive-by refactors.

### Step 4 — Implement a focused change

- Match existing code style; minimal diff.
- Do not change export flags or baseline corpus unless intentional.
- Rebuild after C/C++ changes: `cmake --build build -j`

### Step 5 — Go to Step 1

---

## Success criteria (do not skip)

A change is **acceptable** only when:

1. **`agent_cycle.sh` exits 0** against the **current** `benchmarks/baselines/main.json`.
2. **Deterministic cases**: `sha256` in manifest matches baseline (report shows digest `ok`).
3. **Timing**: no case above `--max-regression` (default **1.15** in `agent_cycle.sh`; use **1.05** for stricter checks).
4. Cases with **`"deterministic": false`** in baseline: digest check is skipped — do not treat as free pass for other cases.

A change is **not** acceptable when:

- **DIGEST MISMATCH** on any deterministic case (unless user asked to change output).
- Export **FAIL** or missing STL for any corpus file.
- You compared against the wrong binary (system vs `./build/openscad`).

---

## When to refresh the baseline (rare)

Only after **intentional** output or toolchain change (backend switch, tessellation change, new `predictible-output` behavior):

```bash
./benchmarks/run_benchmark.py baseline --openscad ./build/openscad --runs 3
cd benchmarks/baselines
git add main.json
git commit -m "Refresh baseline: <reason>"
git push
cd ../..
git add benchmarks/baselines
git commit -m "Bump baselines submodule"
```

Never refresh baseline just to make a failed digest check pass.

---

## Command reference

| Task | Command |
|------|---------|
| Full agent cycle | `./benchmarks/scripts/agent_cycle.sh --openscad ./build/openscad` |
| Compare only (no new run dir) | `./benchmarks/run_benchmark.py compare --openscad ./build/openscad` |
| Stricter timing gate | add `--max-regression 1.05` |
| Record flamegraph data | `./benchmarks/run_benchmark.py cycle --openscad ./build/openscad --profile record --runs 1` |
| Custom corpus | `--corpus path/to/list.lst` (one `.scad` path per line, relative to repo root or absolute) |
| Manifold vs CGAL | add `--backend manifold` or `--backend cgal` to **both** baseline and compare runs |

Fish shell: use `benchmarks/scripts/agent_cycle.fish` instead of `.sh`.

---

## What is being measured

Headless pipeline (see `src/openscad.cc` `do_export()`):

1. Parse + instantiate → `Tree`
2. **`GeometryEvaluator::evaluateGeometry()`** — usually the hotspot
3. Export binary STL (`export_stl.cc` with `--enable=predictible-output`)

Wall time = full subprocess. `--summary time` in OpenSCAD JSON is geometry+export only (secondary metric).

---

## Pitfalls for agents

- **Use the fork binary** (`./build/openscad`), not `/usr/bin/openscad` AppImage, when optimizing this tree — baselines are pinned to a specific build.
- **Minkowski** (and some other ops) may be **nondeterministic** even with `predictible-output`; do not add them to the strict corpus without checking stability.
- **Do not** commit `benchmarks/results/` — gitignored run artifacts.
- **Do not** commit secrets or machine-specific paths into `benchmarks/baselines/main.json` without user review.
- Timing noise: use `--runs 3` for baseline; agent cycle uses 2 runs for speed. If borderline regressions flap, re-run or increase runs.
- Installing system packages: on this user's machine use **`pkexec pacman -S <pkg>`**, not `sudo` (see `~/AGENTS.md`).

---

## Extending the corpus

Edit or add a list file (e.g. copy `benchmarks/corpus-small.lst`):

```text
tests/data/scad/3D/features/union-tests.scad
/path/to/spokencad/samples/slow_case.scad
```

Then:

```bash
./benchmarks/scripts/agent_cycle.sh --openscad ./build/openscad --corpus benchmarks/my-corpus.lst
```

Add stable cases before optimizing; add failing/slow cases from production when narrowing regressions.

---

## Related documentation

| Path | Contents |
|------|----------|
| `benchmarks/README.md` | Pipeline detail, submodule setup, `perf` modes |
| `benchmarks/run_benchmark.py` | Harness implementation |
| `doc/testing.md` | Regression tests (`ctest`) — correctness, not perf |
| `README.md` | Building dependencies and platform specifics |

---

## Quick checklist (copy per iteration)

```
[ ] ./build/openscad exists and is up to date
[ ] benchmarks/baselines/main.json present (submodule init)
[ ] ./benchmarks/scripts/agent_cycle.sh --openscad ./build/openscad
[ ] Read benchmarks/results/runs/latest/agent_report.md
[ ] If FAIL: fix digest first, then timing; inspect profiles/
[ ] If PASS: summarize wall-time deltas for the user
```
