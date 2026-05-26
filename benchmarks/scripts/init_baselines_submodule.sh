#!/usr/bin/env bash
# Register and clone the benchmark-baselines git submodule at benchmarks/baselines/.
#
# Usage:
#   ./benchmarks/scripts/init_baselines_submodule.sh <git-url> [branch]
#
# Example:
#   ./benchmarks/scripts/init_baselines_submodule.sh \
#     git@github.com:you/openscad-benchmark-baselines.git

set -euo pipefail

URL="${1:?Usage: $0 <git-url> [branch]}"
BRANCH="${2:-}"

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BASELINES="${REPO_ROOT}/benchmarks/baselines"

cd "$REPO_ROOT"

if [ -f "${BASELINES}/.git" ] || [ -d "${BASELINES}/.git" ]; then
  echo "benchmarks/baselines already looks like a git checkout/submodule."
  git submodule update --init benchmarks/baselines
  exit 0
fi

if [ -e "${BASELINES}" ]; then
  if [ -n "$(ls -A "${BASELINES}" 2>/dev/null)" ]; then
    echo "Refusing to overwrite non-empty ${BASELINES}"
    echo "Move or remove it, then re-run this script."
    exit 1
  fi
  rmdir "${BASELINES}" 2>/dev/null || true
fi

echo "Adding submodule ${URL} -> benchmarks/baselines"
GIT_SUBMODULE_OPTS=()
if [[ "${URL}" != git@* && "${URL}" != https://* && "${URL}" != http://* ]]; then
  # Local path or file:// URL (Git may block file:// unless allowed)
  GIT_SUBMODULE_OPTS=(-c protocol.file.allow=always)
fi
if [ -n "${BRANCH}" ]; then
  git "${GIT_SUBMODULE_OPTS[@]}" submodule add -b "${BRANCH}" "${URL}" benchmarks/baselines
else
  git "${GIT_SUBMODULE_OPTS[@]}" submodule add "${URL}" benchmarks/baselines
fi

git submodule update --init benchmarks/baselines

echo ""
echo "Submodule ready at benchmarks/baselines"
echo "Next steps:"
echo "  1. Seed main.json: ./benchmarks/run_benchmark.py baseline --openscad ./build/openscad"
echo "  2. Commit inside submodule: cd benchmarks/baselines && git add main.json && git commit"
echo "  3. Push baselines repo, then commit submodule pointer in openscad fork"
