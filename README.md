# Synapse: Beeper Edition

This is Beeper's custom version of synapse, we rebase roughly 25 commits on top of each upstream release with a few Beeper specific modifications. We also have an actual Synapse fork here: [**beeper/synapse-fork**](https://github.com/beeper/synapse-fork) which is where we make changes we expect to merge into upstream.

## Branching strategy

We have a bunch of branches. The main development branch for Beeper is
the `beeper` branch. Push commits here and they get built for our
Docker registry. The GitHub Actions logs include the specific image
tag, which is based on the original upstream version as well as the
commit hash.

We also have versioned `beeper-x.y.z` branches. These are used to
archive the history of the `beeper` branch as it is rebased onto newer
upstream Synapse versions. When rebasing, we copy the current `beeper`
branch back onto the old `beeper-x.y.z` branch for historical
reference, then create a new `beeper-x.y.z` branch based on the new
upstream version and rebase the `beeper` branch onto it, then
force-push that resulting commit into the original `beeper` branch,
which will become the new development target.

We also have `upstream-x.y.z` branches that just track the upstream
tags that we use as bases for Beeper changes, see the rebase flow
below.

## CI setup

Note that we have a separate `beeper-ci.yml` GitHub Actions workflow.
It runs exclusively on the `beeper*` branches, in place of the other
CI workflows that upstream uses and that we have not removed from our
fork. Don't get confused between the two.

## Rebase flow

### Create PR

Here we're upgrading to `v1.96.1`:

```
# Make a new branch from the upstream release, we do this so we can create a PR
# of Beeper -> upstream to run tests/confirm we're happy.
git checkout -f v1.96.1
git checkout -b upstream-1.96.1
git push -u beeper upstream-1.96.1

# Check out the base branch, pull any changes
git checkout beeper
git pull

# Now create a new branch to rebase
git checkout -b beeper-1.96.1
# And do the rebase
git rebase v1.96.1
# fix any conflicts...

# Push and make a PR from this branch to the upstream one created above
git push -u beeper beeper-1.96.1
```

### Make release

Once it's ready we just overwrite the `beeper` branch with the new one:

```
git checkout beeper-1.96.1
git push --force beeper beeper
```
