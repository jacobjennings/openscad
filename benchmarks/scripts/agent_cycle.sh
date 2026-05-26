#!/usr/bin/env bash
# Agent-oriented benchmark loop: profile + compare + report.
#
# Usage:
#   ./benchmarks/scripts/agent_cycle.sh [--openscad PATH] [extra run_benchmark.py args]
#
# Output: benchmarks/results/runs/<timestamp>/agent_report.md
#         benchmarks/results/runs/latest -> <timestamp>

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

OPENSCAD_ARG=()
if [ "${1:-}" = "--openscad" ]; then
  OPENSCAD_ARG=(--openscad "$2")
  shift 2
fi

exec ./benchmarks/run_benchmark.py cycle "${OPENSCAD_ARG[@]}" --profile auto --runs 2 --max-regression 1.15 "$@"
