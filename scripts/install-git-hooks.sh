#!/bin/sh
# Install the repo's git hooks into .git/hooks (local, per-clone).
# Git hooks are not versioned; this makes them reproducible.
set -eu
cd "$(git rev-parse --show-toplevel)"
mkdir -p .git/hooks
for hook in scripts/git-hooks/*; do
    name=$(basename "$hook")
    cp "$hook" ".git/hooks/$name"
    chmod +x ".git/hooks/$name"
    echo "installed .git/hooks/$name"
done
