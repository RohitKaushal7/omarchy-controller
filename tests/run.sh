#!/usr/bin/env bash
# Every test: the daemon's (Python, including an end-to-end run against a
# synthetic uinput pad), the panel's JavaScript, and qmllint.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$here/py" -t "$here/py"
node --test "$here/js/"
bash "$here/qml/lint.sh"
echo "all tests passed"
