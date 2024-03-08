# Synapse: Beeper Edition


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
