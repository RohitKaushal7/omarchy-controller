#!/usr/bin/env bash
# Qt 6 qmllint over the plugin's QML, or over the files named.
# The shell's qs.* modules are found through a temporary qs/ directory of
# symlinks, as Quickshell maps them at run time. Four kinds of warning are
# noise for shell plugins and are left out: unqualified access (delegates read their parents' ids), members of the
# shell's untyped groups (Style.font.body, bar.foreground on a QtObject),
# and Quickshell signals whose parameters are Qt enums with no qmltypes here
# (Process.exited's QProcess::ExitStatus, Socket.error's
# QLocalSocket::LocalSocketError; dev.reuk.beam's working Engine.qml shows
# the same). Quickshell's qmltypes also call PanelWindow uncreatable, which
# the shipped OSD (shell/plugins/osd/Osd.qml) shows is not so. Everything
# else, from a misspelt property to a missing import, fails.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
root=$(cd -- "$here/../.." && pwd -P)
shell=${OMARCHY_PATH:-/usr/share/omarchy}/shell
qmllint=/usr/lib/qt6/bin/qmllint  # /usr/bin/qmllint is Qt 5's
[[ -x $qmllint ]] || { echo "Qt 6 qmllint not found at $qmllint" >&2; exit 1; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/qs"
ln -s "$shell/Commons" "$tmp/qs/Commons"
ln -s "$shell/Ui" "$tmp/qs/Ui"

files=("$@")
if [[ ${#files[@]} -eq 0 ]]; then
  mapfile -d '' files < <(find "$root" -name '*.qml' -not -path '*/.superpowers/*' -not -path '*/.git/*' -print0 | sort -z)
fi
status=0
for file in "${files[@]}"; do
  out=$("$qmllint" -I "$tmp" "$file" 2>&1 || true)
  bad=$(grep -E '^(Warning|Error|Critical)' <<<"$out" \
    | grep -v '\[unqualified\]' \
    | grep -vE 'Member "[A-Za-z0-9_]+" not found on type "(QObject|QtObject)"' \
    | grep -vE 'Type Q[A-Za-z]+::[A-Za-z]+ of parameter .*\[signal-handler-parameters\]' \
    | grep -v 'Type PanelWindow is not creatable' || true)
  if [[ -n $bad ]]; then
    echo "== ${file#"$root"/}"
    echo "$bad"
    status=1
  fi
done
exit $status
