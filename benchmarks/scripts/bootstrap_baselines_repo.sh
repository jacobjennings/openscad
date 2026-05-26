#!/usr/bin/env bash
# Create a new local git repo from baseline-submodule/ template (for pushing to GitHub).
#
# Usage:
#   ./benchmarks/scripts/bootstrap_baselines_repo.sh /path/to/new-baselines-repo
#
# Then:
#   cd /path/to/new-baselines-repo && git remote add origin <url> && git push -u origin main
#   cd openscad-fork && ./benchmarks/scripts/init_baselines_submodule.sh <url>

set -euo pipefail

DEST="${1:?Usage: $0 /path/to/new-baselines-repo}"

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TEMPLATE="${REPO_ROOT}/benchmarks/baseline-submodule"

if [ -e "${DEST}" ]; then
  echo "Destination already exists: ${DEST}" >&2
  exit 1
fi

mkdir -p "${DEST}"
cp -a "${TEMPLATE}/." "${DEST}/"
rm -f "${DEST}/.git" 2>/dev/null || true

cd "${DEST}"
git init -b main
git add .
git commit -m "Initial benchmark baselines repository"

echo "Created ${DEST}"
echo "Add remote and push, then run init_baselines_submodule.sh from the OpenSCAD fork."
