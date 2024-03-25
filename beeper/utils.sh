function get_upstream_remote() {
    for remote in $(git remote); do
        url=$(git remote get-url $remote)
        if [ "$url" = "git@github.com:element-hq/synapse.git" ]; then
            echo $remote
            return 0
        fi
    done
    echo >&2 "No upstream remote found (looking for URL: git@github.com:element-hq/synapse.git)"
    return 1
}

function get_beeper_remote() {
    for remote in $(git remote); do
        url=$(git remote get-url $remote)
        if [ "$url" = "git@github.com:beeper/synapse.git" ]; then
            echo $remote
            return 0
        fi
    done
    echo >&2 "No upstream remote found (looking for URL: git@github.com:beeper/synapse.git)"
    return 1
}
