#!/usr/bin/env fish
# Agent-oriented benchmark loop: profile + compare + report.

set -l repo_root (cd (dirname (status filename))/../..; and pwd)
cd $repo_root

set -l extra_args
if test (count $argv) -ge 2; and test $argv[1] = --openscad
    set extra_args --openscad $argv[2]
    set argv $argv[3..-1]
end

exec ./benchmarks/run_benchmark.py cycle $extra_args --profile auto --runs 2 --max-regression 1.15 $argv
