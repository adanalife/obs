#!/usr/bin/env bash
# Self-check for number-fragment.sh: the three names it has to get right.
# Run it directly — it works in a throwaway git repo and touches nothing else.
set -euo pipefail

script="$(cd "$(dirname "$0")" && pwd)/number-fragment.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
git init -q .
mkdir changelog.d

check() {
  [ "$1" = "$2" ] || { echo "want $2, got $1"; exit 1; }
}

add() { touch "$1"; git add "$1"; }

add changelog.d/+audio.fix.md
check "$("$script" 186 changelog.d/+audio.fix.md)" changelog.d/186.fix.md

# A second fragment of the same type takes towncrier's counter rather than
# clobbering the first.
add changelog.d/+refresh.fix.md
check "$("$script" 186 changelog.d/+refresh.fix.md)" changelog.d/186.fix.1.md

# towncrier's own de-duplicated name: the last segment is a counter, so the
# type is the one before it.
add changelog.d/+gate.ci.1.md
check "$("$script" 187 changelog.d/+gate.ci.1.md)" changelog.d/187.ci.md

# number-fragments.sh reads each placeholder's PR off the squash commit that
# added it, and leaves one with no number alone.
all="$(dirname "$script")/number-fragments.sh"
git -c user.name=t -c user.email=t@t commit -qm "seed"
add changelog.d/+bed-3f9a.added.md
git -c user.name=t -c user.email=t@t commit -qm "feat(audio): seed the bed (#275)"
add changelog.d/+direct-01ab.fix.md
git -c user.name=t -c user.email=t@t commit -qm "fix: pushed with no PR"
"$all" > /dev/null
[ -e changelog.d/275.added.md ] || { echo "want changelog.d/275.added.md"; exit 1; }
[ -e changelog.d/+direct-01ab.fix.md ] || { echo "a fragment with no PR number should stay put"; exit 1; }

echo "ok"
