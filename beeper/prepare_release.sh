#!/usr/bin/env bash

set -euo pipefail
source $(realpath $(dirname $0))/utils.sh

BEEPER_REMOTE=$(get_beeper_remote)

VERSION=${1:-}

if [ -z "$VERSION" ]; then
    echo >&2 "Must specify version!"
    exit 1
fi

STARTING_BRANCH=$(git branch --show-current)

echo "Preparing Synapse: Beeper Edition version $VERSION"
echo "WARNING: this script will rebase on top of the CURRENT BRANCH: $STARTING_BRANCH"
read -p "Press enter to continue"

TAG=v$VERSION
UPSTREAM_BRANCH=upstream-$VERSION
BEEPER_BRANCH=beeper-$VERSION

# Checkout the tag, create upstream branch, push it
echo "Setup branch $UPSTREAM_BRANCH"
git checkout -f $TAG
git checkout -b $UPSTREAM_BRANCH
git push -u $BEEPER_REMOTE $UPSTREAM_BRANCH

# Switch back to our starting branch, create new version branch from it
echo "Setup branch $BEEPER_BRANCH"
git checkout $STARTING_BRANCH
git checkout -b $BEEPER_BRANCH

# And rebase against upstream, applying only our Beeper commits
echo "Initiate rebase..."
git rebase $UPSTREAM_BRANCH || read -p "Rebase was a mess, press enter once you fix it"

git push -u $BEEPER_REMOTE $BEEPER_BRANCH

echo "OK we done!"
echo "Go HERE and make the PR: https://github.com/beeper/synapse/compare/upstream-$VERSION...beeper-$VERSION?expand=1"
