#!/bin/bash
# Helper script to configure local development environment
# This tells git to ignore local changes to the PR review cache file

CACHE_FILE="scripts/pr_reviews_cache.json"

echo "Configuring local development environment..."
echo ""

# Set skip-worktree on cache file
if git ls-files "$CACHE_FILE" 2>/dev/null | grep -q "$CACHE_FILE"; then
    git update-index --skip-worktree "$CACHE_FILE"
    echo "✓ Cache file changes will be ignored locally"
    echo "  File: $CACHE_FILE"
else
    echo "ℹ Cache file not yet in repository"
    echo "  Will be configured once the file exists"
fi

echo ""
echo "You can now run update_stats.py without worrying about committing cache changes."
echo ""
echo "To check status: git ls-files -v | grep '$CACHE_FILE'"
echo "  'S' = skip-worktree enabled"
echo "  'H' = normal tracking"
