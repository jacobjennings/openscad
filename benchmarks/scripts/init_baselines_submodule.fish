#!/usr/bin/env fish
# Register and clone the benchmark-baselines git submodule at benchmarks/baselines/.
#
# Usage:
#   ./benchmarks/scripts/init_baselines_submodule.fish <git-url> [branch]

if test (count $argv) -lt 1
    echo "Usage: $argv[0] <git-url> [branch]" >&2
    exit 1
end

set -l url $argv[1]
set -l branch ""
if test (count $argv) -ge 2
    set branch $argv[2]
end

set -l repo_root (cd (dirname (status filename))/../..; and pwd)
set -l baselines $repo_root/benchmarks/baselines

cd $repo_root

if test -f $baselines/.git -o -d $baselines/.git
    echo "benchmarks/baselines already looks like a git checkout/submodule."
    git submodule update --init benchmarks/baselines
    exit 0
end

if test -e $baselines
    set -l contents (ls -A $baselines 2>/dev/null)
    if test -n "$contents"
        echo "Refusing to overwrite non-empty $baselines" >&2
        exit 1
    end
    rmdir $baselines 2>/dev/null
end

echo "Adding submodule $url -> benchmarks/baselines"
if test -n "$branch"
    git submodule add -b $branch $url benchmarks/baselines
else
    git submodule add $url benchmarks/baselines
end

git submodule update --init benchmarks/baselines

echo ""
echo "Submodule ready at benchmarks/baselines"
echo "Next steps:"
echo "  1. Seed main.json: ./benchmarks/run_benchmark.py baseline --openscad ./build/openscad"
echo "  2. Commit inside submodule: cd benchmarks/baselines && git add main.json && git commit"
echo "  3. Push baselines repo, then commit submodule pointer in openscad fork"
