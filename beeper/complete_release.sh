#!/usr/bin/env bash

set -euo pipefail
source $(realpath $(dirname $0))/utils.sh

BEEPER_REMOTE=$(get_beeper_remote)

VERSION=${1:-}

if [ -z "$VERSION" ]; then
    echo >&2 "Must specify version!"
    exit 1
fi

echo "Completing Synapse: Beeper Edition version $VERSION"
echo "WARNING: this script will DELETE the branch called: beeper"
read -p "Press enter to continue"

UPSTREAM_BRANCH=upstream-$VERSION
BEEPER_BRANCH=beeper-$VERSION

git checkout $BEEPER_BRANCH
git branch -D beeper
git checkout -b beeper
git push --force $BEEPER_REMOTE beeper

# Cleanup
git branch -D $BEEPER_BRANCH
git push $BEEPER_REMOTE --delete $BEEPER_BRANCH
git branch -D $UPSTREAM_BRANCH
git push $BEEPER_REMOTE --delete $UPSTREAM_BRANCH
