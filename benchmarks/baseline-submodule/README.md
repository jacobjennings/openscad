# OpenSCAD benchmark baselines

This repository stores **reference manifests** for `benchmarks/run_benchmark.py` in the OpenSCAD tree.
Each manifest records SHA-256 digests of binary STL exports and median wall times for a fixed corpus, OpenSCAD build, and export flags.

It is intended to be mounted as a **git submodule** at `benchmarks/baselines/` in your OpenSCAD fork.

## Layout

```text
main.json                 # default profile (required for `compare`)
profiles/                 # optional extra manifests (e.g. CI, other backends)
  linux-x86_64-manifold.json
```

Manifests are JSON produced by:

```bash
# From the parent OpenSCAD repo (submodule checked out at benchmarks/baselines)
../run_benchmark.py baseline --openscad /path/to/openscad --runs 3
```

Commit and push changes here when you intentionally update golden digests or timing references.

## Updating baselines

1. Build the OpenSCAD binary you want to pin.
2. From the parent repo root, with this submodule initialized:
   ```bash
   ./benchmarks/run_benchmark.py baseline --openscad ./build/openscad --runs 3
   ```
3. In this repo:
   ```bash
   git add main.json
   git commit -m "Refresh benchmark baseline for <reason>"
   git push
   ```
4. In the parent OpenSCAD repo, bump the submodule pointer:
   ```bash
   git add benchmarks/baselines
   git commit -m "Bump benchmark baselines submodule"
   ```

## Notes

- Baselines are **build-specific**. Different CGAL/Manifold versions or CMake flags usually change digests.
- Cases marked `"deterministic": false` skip digest comparison (e.g. some Minkowski workloads).
- Do not store `.stl` binaries in this repo; only JSON manifests.
