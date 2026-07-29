#!/bin/bash
# Manual trigger for the daily briefing.
#   ./run.sh                 send today's briefing
#   ./run.sh --dry-run       build it, send nothing, record nothing
#   ./run.sh --preview       also write an HTML file you can open in a browser
#   ./run.sh --check-feeds   test all 10 feeds
#   ./run.sh --authorize     one-time Google sign-in
#   ./run.sh --stats         run history and feed health
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PY=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$candidate" ]; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then PY="$(command -v python3 || true)"; fi
if [ -z "$PY" ]; then
  echo "python3 not found. Install the Xcode command line tools:  xcode-select --install" >&2
  exit 1
fi

mkdir -p "$DIR/state/logs"
exec "$PY" -m briefing "$@"
