# OpenSCAD performance benchmarks

> **AI agents:** use the step-by-step improve loop in [`../AGENTS.md`](../AGENTS.md).

Batch timing and **bit-identical** export checks for the compile → geometry → export path used by headless validation (e.g. LoRA training loops that render generated `.scad` to STL).

## Pipeline (what is being measured)

Headless export (`openscad file.scad -o out.stl`) runs roughly:

| Stage | Code | Notes |
|-------|------|--------|
| Parse + AST | `parse()` in `cmdline()` | Lex/yacc + `SourceFile` |
| Instantiate | `root_file->instantiate()` | Builds abstract node tree |
| CSG tree | `Tree(root_node)` | Normalization, modifiers |
| Geometry | `GeometryEvaluator::evaluateGeometry()` | CGAL / Manifold / PolySet |
| Export | `exportFileByName()` → `export_stl` etc. | Tessellation + IO |

`--summary time` reports **geometry evaluation + export** only (from `RenderStatistic` in `do_export()`), not parse/instantiate. The harness records **wall-clock** time for the full subprocess, which matches training-validation latency more closely.

GUI preview (F5) uses OpenCSG and a separate path (`prepare_preview()`); this harness targets full render (`--render`) like production STL export.

## Deterministic outputs (bit-level checks)

Regression tests use:

```text
--enable=predictible-output --render --export-format binstl
```

With `predictible-output`, STL triangles are sorted (`createSortedPolySet` in `export_stl.cc`) so repeated runs on the **same binary** should yield identical files. Compare SHA-256 digests, not floating-point text.

**Caveats:**

- Compare only against a baseline built with the **same** OpenSCAD build, backend (`--backend=manifold` vs `cgal`), and export flags.
- Switching Manifold/CGAL or tessellation settings can change meshes while still being “valid”; treat digest changes as intentional only after review.
- System vs locally-built binaries may differ slightly; always baseline your fork’s `build/openscad`.
- `predictible-output` sorts STL triangles but does **not** guarantee stable meshes for all operations (e.g. Minkowski can vary run-to-run). The harness sets `"deterministic": false` when repeated exports disagree and **skips digest comparison** for those cases. Prefer stable models in the corpus for strict bit checks; use `tests/validatestl.py` or geometry stats when digest checks are not reliable.

## Baselines git submodule

Golden digests and timing references live in a **separate repository**, mounted at `benchmarks/baselines/`. The main OpenSCAD repo only stores the submodule commit pointer (like `libraries/MCAD`).

### One-time setup

1. Create an empty GitHub (or local) repo, e.g. `openscad-benchmark-baselines`.
2. From this fork:

   ```bash
   chmod +x benchmarks/scripts/*.sh benchmarks/run_benchmark.py

   # Option A: bootstrap a local baselines repo from the template, push it, then link
   ./benchmarks/scripts/bootstrap_baselines_repo.sh ~/openscad-benchmark-baselines
   cd ~/openscad-benchmark-baselines
   git remote add origin git@github.com:YOU/openscad-benchmark-baselines.git
   git push -u origin main

   ./benchmarks/scripts/init_baselines_submodule.sh \
     git@github.com:YOU/openscad-benchmark-baselines.git
   ```

   ```fish
   # Same init step (Fish)
   ./benchmarks/scripts/init_baselines_submodule.fish \
     git@github.com:YOU/openscad-benchmark-baselines.git
   ```

3. Clone/update on other machines:

   ```bash
   git submodule update --init benchmarks/baselines
   ```

Template for the baselines-only repo: `benchmarks/baseline-submodule/`.  
Example manifest (not used by `compare`): `benchmarks/example-baseline/main.json`.

### Refreshing baselines

```bash
./benchmarks/run_benchmark.py baseline --openscad ./build/openscad --runs 3
cd benchmarks/baselines && git add main.json && git commit -m "Refresh baseline" && git push
cd ../.. && git add benchmarks/baselines && git commit -m "Bump baselines submodule"
```

## Quick start

```bash
# From repo root; submodule must be initialized (see above).
chmod +x benchmarks/run_benchmark.py benchmarks/scripts/*.sh

# Record baseline digests + timings (writes benchmarks/baselines/main.json)
./benchmarks/run_benchmark.py baseline --openscad ./build/openscad --runs 3

# After performance changes
./benchmarks/run_benchmark.py compare --openscad ./build/openscad --max-regression 1.05
```

## Profiling and agent cycles

For automated optimize loops (agent reads report, edits code, rebuilds, re-runs):

```bash
./benchmarks/scripts/agent_cycle.sh --openscad ./build/openscad
# or
./benchmarks/run_benchmark.py cycle --openscad ./build/openscad --profile auto
```

Each **cycle** writes:

```text
benchmarks/results/runs/<timestamp>/
  manifest.json       # timings, digests, profile summaries
  agent_report.md     # markdown table for agents (PASS/FAIL, deltas, artifact paths)
  profiles/<case>/run-0/psutil.json   # always (via psutil)
```

`benchmarks/results/runs/latest` symlinks to the most recent run.

| `--profile` | Behavior |
|-------------|----------|
| `auto` (cycle default) | `perf stat -j` if `perf` is on PATH, else child CPU/RSS via `getrusage` |
| `psutil` | Same as `auto` fallback: CPU time + peak RSS per case |
| `stat` | Require `perf stat` |
| `record` | `perf record -g` on first repeat only (slow; produces `perf.data`) |

`perf` is expected on this dev machine (`perf stat` used in `auto` mode). Install on Arch if missing: `pkexec pacman -S perf`.

## Quick start (agent)

1. `./benchmarks/scripts/agent_cycle.sh --openscad ./build/openscad`
2. Open `benchmarks/results/runs/latest/agent_report.md`
3. Inspect `benchmarks/results/runs/latest/profiles/`
4. Apply code change, rebuild, repeat

Outputs:

- Baselines (submodule): `benchmarks/baselines/<name>.json`
- Run artifacts (gitignored): `benchmarks/results/runs/<timestamp>/`

### Corpus

- Default: `benchmarks/corpus-small.lst` (fast smoke set).
- Add paths (relative to repo root), one per line. Point at your own `~/spokencad` samples via absolute paths in the list file if needed.

### Environment

- `OPENSCADPATH` — set automatically to `libraries/` when present (MCAD, etc.).
- `OPENSCAD_BINARY` — default executable if `--openscad` is omitted.

## Relation to existing tests

| Mechanism | Purpose |
|-----------|---------|
| `ctest` + `tests/test_cmdline_tool.py` | Correctness regressions (text/PNG/STL golden files) |
| `tests/stlexportsanitytest.py` | STL validity after export |
| `benchmarks/run_benchmark.py` | Timing + digest baselines for performance work |

Optional: wire `benchmarks/run_benchmark.py compare` into CI as a manual/scheduled job once baselines are checked in.

## Suggested optimization workflow

1. **Baseline** on `master` with your release build flags.
2. **Change** one subsystem (cache, evaluator, Manifold op, export).
3. **Compare** — digest must match; median wall time should improve or stay within `--max-regression`.
4. **Extend corpus** with failing/slow models from spokencad before large refactors.
5. Use `perf record` / `hotspot` on a single heavy `.scad` when the harness shows regressions or hotspots.

Build flags that matter for apples-to-apples timing: `ENABLE_MANIFOLD`, `USE_MANIFOLD_TRIANGULATOR`, `USE_MIMALLOC`, `HEADLESS`, and CGAL version (see `openscad --info`).
