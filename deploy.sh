#!/bin/bash
# Deploy the briefing app to a Linux server. Run this FROM YOUR MAC.
#
#   ./deploy.sh --host 10.0.0.5 --user someone
#   ./deploy.sh                                  # uses deploy.local.conf
#   ./deploy.sh --key ~/.ssh/id_rsa_server       # if not your default key
#   ./deploy.sh --no-bootstrap                   # never touch apt; fail if deps are missing
#   ./deploy.sh --keep-mac-schedule              # leave the local 08:00 agent running
#
# Works against a bare Ubuntu install: it probes every prerequisite, installs
# anything missing via apt, transfers the app, credentials and send-history,
# then installs a systemd timer for 08:00 daily.
set -euo pipefail

# Defaults live in deploy.local.conf, which is git-ignored, so no host or
# username is baked into the published script. See deploy.local.conf.example.
HOST=""; USER_NAME=""; REMOTE_DIR="briefing"
SSH_KEY=""; KEEP_MAC_SCHEDULE=0; BOOTSTRAP=1

DIR_EARLY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
[ -f "$DIR_EARLY/deploy.local.conf" ] && . "$DIR_EARLY/deploy.local.conf"

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --user) USER_NAME="$2"; shift 2 ;;
    --key)  SSH_KEY="$2"; shift 2 ;;
    --dir)  REMOTE_DIR="$2"; shift 2 ;;
    --no-bootstrap) BOOTSTRAP=0; shift ;;
    --keep-mac-schedule) KEEP_MAC_SCHEDULE=1; shift ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

DIR="$DIR_EARLY"; cd "$DIR"

if [ -z "$HOST" ] || [ -z "$USER_NAME" ]; then
  cat >&2 <<'EOM'
No target server configured.

  Either pass it explicitly:
      ./deploy.sh --host 192.168.1.50 --user youruser

  Or save your defaults once (this file is git-ignored):
      cp deploy.local.conf.example deploy.local.conf
      $EDITOR deploy.local.conf
EOM
  exit 1
fi
TARGET="$USER_NAME@$HOST"
CTL="/tmp/briefing-deploy-$$.sock"
SSH_OPTS=(-o ControlMaster=auto -o ControlPath="$CTL" -o ControlPersist=180
          -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
[ -n "$SSH_KEY" ] && SSH_OPTS+=(-i "$SSH_KEY")

STAGE=""
cleanup() { [ -n "$STAGE" ] && rm -rf "$STAGE"; ssh -O exit -o ControlPath="$CTL" "$TARGET" 2>/dev/null || true; }
trap cleanup EXIT


# Remove every LaunchAgent that points at this install, whatever it is called.
# Matching on the install path rather than a fixed label means agents left behind
# by earlier versions are cleaned up too, instead of quietly firing forever.
remove_local_agents() {
  local app_dir="$1" removed=0 plist label
  for plist in "$HOME"/Library/LaunchAgents/*.plist; do
    [ -f "$plist" ] || continue
    if grep -q "$app_dir" "$plist" 2>/dev/null; then
      label="$(basename "$plist" .plist)"
      launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
      rm -f "$plist"
      echo "$label"
      removed=$((removed + 1))
    fi
  done
  [ "$removed" -gt 0 ]
}

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
add()  { printf '  \033[33minstall\033[0m %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; }
die()  { printf '\n\033[31mFAILED\033[0m %s\n' "$*" >&2; exit 1; }

rsh()  { ssh "${SSH_OPTS[@]}" "$TARGET" "$@"; }
rsht() { ssh "${SSH_OPTS[@]}" -t "$TARGET" "$@"; }

# ------------------------------------------------------------------ connect
# sudo needs a terminal to read a password into. ssh -t can only allocate one
# if this script itself is attached to a terminal.
if [ ! -t 0 ]; then
  printf '\033[33mwarn\033[0m  stdin is not a terminal - a sudo password prompt could not be answered.\n'
  printf '        Run ./deploy.sh directly from Terminal, not piped or backgrounded.\n'
fi

say "1/7  Connecting to $TARGET"
rsh true 2>/dev/null || die "cannot SSH to $TARGET.
  Test it yourself:  ssh ${SSH_KEY:+-i $SSH_KEY }$TARGET
  If the key is not loaded:  ssh-add ~/.ssh/your_key
  SSH is the one prerequisite that cannot be installed remotely - if the server
  has no sshd, install it from the console:  sudo apt install -y openssh-server"
ok "SSH connection established"

# -------------------------------------------------------------------- probe
# Everything the app needs, tested rather than assumed. On Debian and Ubuntu the
# Python standard library is split across packages: python3-minimal has neither
# sqlite3 nor ssl, and without ca-certificates every HTTPS request fails
# certificate verification even though ssl imports fine.
probe() {
rsh 'bash -s' <<'REMOTE'
p() { printf '%s=%s\n' "$1" "$2"; }
p OS "$( (. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME") || echo unknown)"
p TAR "$(command -v tar >/dev/null && echo ok || echo missing)"
p GZIP "$(command -v gzip >/dev/null && echo ok || echo missing)"
p SYSTEMD "$(command -v systemctl >/dev/null && echo ok || echo missing)"
# Distinguish the three sudo states without triggering a password prompt.
# "sudo -n" never prompts: it succeeds if a cached/NOPASSWD rule applies, and
# otherwise fails with a message that says which situation we are in.
if command -v sudo >/dev/null; then
  if sudo -n true 2>/dev/null; then
    p SUDO nopasswd
  else
    SUDO_ERR="$(sudo -n true 2>&1 || true)"
    case "$SUDO_ERR" in
      *"password is required"*|*"a terminal is required"*) p SUDO password ;;
      *"not allowed"*|*"not in the sudoers"*)              p SUDO denied ;;
      *)                                                    p SUDO password ;;
    esac
  fi
else
  p SUDO missing
fi
p APT "$(command -v apt-get >/dev/null && echo ok || echo missing)"
PY="$(command -v python3 || true)"
p PY "${PY:-missing}"
if [ -n "$PY" ]; then
  p PYVER "$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo unknown)"
  p MOD_SQLITE "$("$PY" -c 'import sqlite3' 2>/dev/null && echo ok || echo missing)"
  p MOD_SSL "$("$PY" -c 'import ssl' 2>/dev/null && echo ok || echo missing)"
  p HTTPS "$("$PY" - <<'PYEOF' 2>/dev/null || echo fail
import ssl, urllib.request, urllib.error
try:
    urllib.request.urlopen("https://oauth2.googleapis.com/", timeout=20,
                           context=ssl.create_default_context())
    print("ok")
except urllib.error.HTTPError:
    print("ok")          # reached the server over verified TLS; status is irrelevant
except Exception as e:
    print("fail:%s" % type(e).__name__)
PYEOF
)"
else
  p PYVER unknown; p MOD_SQLITE missing; p MOD_SSL missing; p HTTPS fail
fi
p TZ "$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo unknown)"
p LINGER "$(loginctl show-user "$USER" --property=Linger --value 2>/dev/null || echo no)"
REMOTE
}

say "2/7  Checking prerequisites"
PROBE="$(probe)"
field() { printf '%s\n' "$PROBE" | grep "^$1=" | head -1 | cut -d= -f2-; }

ok "$(field OS)"
[ "$(field SYSTEMD)" = "ok" ] || die "systemd not found - this script targets a systemd distro."
[ "$(field TAR)" = "ok" ] || die "tar not found on the server (very unusual). Install it: sudo apt install -y tar"

NEED=()
[ "$(field PY)" = "missing" ]         && NEED+=(python3)
[ "$(field MOD_SQLITE)" = "missing" ] && NEED+=(python3)
[ "$(field MOD_SSL)" = "missing" ]    && NEED+=(python3 ca-certificates openssl)
case "$(field HTTPS)" in ok) ;; *) NEED+=(ca-certificates) ;; esac
[ "$(field TZ)" = "unknown" ]         && NEED+=(tzdata)

# de-duplicate
if [ ${#NEED[@]} -gt 0 ]; then
  NEED=($(printf '%s\n' "${NEED[@]}" | sort -u))
fi

if [ ${#NEED[@]} -eq 0 ]; then
  ok "python3 $(field PYVER) with sqlite3 and ssl"
  ok "HTTPS verified against a live endpoint"
  ok "timezone $(field TZ)"
else
  printf '\n  The server is missing:\n'
  [ "$(field PY)" = "missing" ] && add "python3 (not installed)"
  [ "$(field PY)" != "missing" ] && [ "$(field MOD_SQLITE)" = "missing" ] && add "python3 sqlite3 module (python3-minimal only)"
  [ "$(field PY)" != "missing" ] && [ "$(field MOD_SSL)" = "missing" ] && add "python3 ssl module"
  case "$(field HTTPS)" in ok) ;; *) add "ca-certificates (HTTPS check: $(field HTTPS))" ;; esac
  [ "$(field TZ)" = "unknown" ] && add "tzdata (needed for a correct 08:00)"

  [ "$BOOTSTRAP" = "1" ] || die "missing prerequisites and --no-bootstrap was given."
  [ "$(field APT)" = "ok" ] || die "apt-get not available; install manually: ${NEED[*]}"

  case "$(field SUDO)" in
    denied|missing)
      die "$USER_NAME cannot run sudo, so these packages cannot be installed:
      ${NEED[*]}

  Ask someone with root on $HOST to run:
      apt-get install -y --no-install-recommends ${NEED[*]}

  Then run ./deploy.sh again - it will find the prerequisites satisfied and
  will not need sudo for this step." ;;
  esac

  if [ "$(field SUDO)" = "password" ]; then
    say "2b/7  Installing ${NEED[*]}"
    echo "  You will be prompted for $USER_NAME's sudo password on $HOST."
  else
    say "2b/7  Installing ${NEED[*]}"
  fi
  rsht "sudo apt-get update -qq && sudo apt-get install -y --no-install-recommends ${NEED[*]}" \
    || die "apt install failed. Run it manually:
      ssh $TARGET sudo apt-get install -y ${NEED[*]}"

  PROBE="$(probe)"
  [ "$(field PY)" != "missing" ]        || die "python3 still missing after install."
  [ "$(field MOD_SQLITE)" = "ok" ]      || die "python3 sqlite3 still unavailable. Try: sudo apt install -y libsqlite3-0 python3"
  [ "$(field MOD_SSL)" = "ok" ]         || die "python3 ssl still unavailable."
  case "$(field HTTPS)" in
    ok) ;;
    *) die "the server still cannot make a verified HTTPS request ($(field HTTPS)).
      Check DNS and outbound 443 - the app cannot read feeds or send mail without it." ;;
  esac
  ok "python3 $(field PYVER) with sqlite3 and ssl"
  ok "HTTPS verified against a live endpoint"
fi

SUDO_MODE="$(field SUDO)"
case "$SUDO_MODE" in
  nopasswd) ok "$USER_NAME has passwordless sudo - no prompts" ;;
  password) ok "$USER_NAME can sudo (you will be prompted for the password when needed)" ;;
  denied)   warn "$USER_NAME is NOT in the sudoers file - continuing without sudo" ;;
  missing)  warn "sudo is not installed on the server - continuing without it" ;;
esac

LOCAL_TZ="$(readlink /etc/localtime 2>/dev/null | sed 's|.*/zoneinfo/||')"
REMOTE_TZ="$(field TZ)"
if [ -n "$LOCAL_TZ" ] && [ "$REMOTE_TZ" != "$LOCAL_TZ" ]; then
  warn "server timezone is $REMOTE_TZ, your Mac is $LOCAL_TZ - the briefing arrives at 08:00 $REMOTE_TZ."
  warn "To match your Mac:  ssh $TARGET sudo timedatectl set-timezone $LOCAL_TZ"
fi

# ------------------------------------------------------------------ package
say "3/7  Packaging"
for required in secrets/client_secret.json secrets/token.json secrets/anthropic_api_key; do
  [ -f "$required" ] || die "missing $required - the deployment would not be able to send."
done
ok "credentials present (OAuth client, Gmail token, Anthropic key)"

STAGE="$(mktemp -d)"
PAYLOAD=(briefing config tests deploy run.sh README.md secrets)
[ -f state/briefing.db ] && PAYLOAD+=(state/briefing.db)
tar czf "$STAGE/briefing.tgz" \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
  --exclude='state/preview' --exclude='state/logs' \
  --exclude='state/sample-briefing.html' --exclude='state/*.db-journal' \
  --exclude='state/*.db-wal' --exclude='state/*.db-shm' \
  "${PAYLOAD[@]}"
ok "archive built ($(du -h "$STAGE/briefing.tgz" | cut -f1))"

if [ -f state/briefing.db ]; then
  SENT_COUNT="$(python3 -c "
import sqlite3
try: print(sqlite3.connect('state/briefing.db').execute('SELECT COUNT(*) FROM seen').fetchone()[0])
except Exception: print(0)" 2>/dev/null || echo 0)"
  ok "carrying forward $SENT_COUNT already-sent items so the server will not resend them"
fi

# ----------------------------------------------------------------- transfer
say "4/7  Transferring to $TARGET:~/$REMOTE_DIR"
# Piped through ssh rather than scp: OpenSSH 9 implements scp over SFTP, which
# needs sftp-server on the remote. A bare sshd may not have it; a shell and tar
# are always there.
rsh "mkdir -p ~/$REMOTE_DIR ~/.config/systemd/user"
tar_out=$(cat "$STAGE/briefing.tgz" | rsh "tar xzf - -C ~/$REMOTE_DIR" 2>&1) \
  || die "transfer failed: $tar_out"
rsh "set -e
  cd ~/$REMOTE_DIR
  mkdir -p state/logs state/preview
  chmod +x run.sh
  chmod 700 secrets && chmod 600 secrets/* 2>/dev/null || true
  find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true"
ok "extracted, secrets locked to 0600"

rsh "cd ~/$REMOTE_DIR && python3 -c 'import briefing.pipeline, briefing.curate, briefing.gmail'" \
  || die "the application failed to import on the server."
ok "application imports cleanly"

# ------------------------------------------------------------------ systemd
say "5/7  Installing the 08:00 timer"
rsh "install -m 644 ~/$REMOTE_DIR/deploy/briefing.service ~/.config/systemd/user/briefing.service
     install -m 644 ~/$REMOTE_DIR/deploy/briefing.timer   ~/.config/systemd/user/briefing.timer
     systemctl --user daemon-reload
     systemctl --user enable --now briefing.timer >/dev/null 2>&1"
ok "systemd user timer enabled"

if [ "$(field LINGER)" = "yes" ]; then
  ok "linger already enabled - the timer runs without you being logged in"
elif [ "$SUDO_MODE" = "denied" ] || [ "$SUDO_MODE" = "missing" ]; then
  warn "linger could not be enabled because $USER_NAME cannot sudo."
  warn "The timer will only fire while $USER_NAME has an active login session,"
  warn "so after a reboot the 08:00 briefing would be skipped."
  warn "Ask an admin to run this once - it is the only root-level step remaining:"
  warn "    loginctl enable-linger $USER_NAME"
else
  warn "linger is off - user timers only run while $USER_NAME has a session."
  if [ "$SUDO_MODE" = "password" ]; then
    echo  "        Enabling it now - enter $USER_NAME's sudo password when prompted."
  fi
  if rsht "sudo loginctl enable-linger $USER_NAME"; then
    ok "linger enabled - the timer now runs without you being logged in"
  else
    warn "could not enable linger. Ask an admin to run:"
    warn "    loginctl enable-linger $USER_NAME"
    warn "Until then the 08:00 run is skipped unless $USER_NAME is logged in."
  fi
fi

# ------------------------------------------------------------------- verify
say "6/7  Verifying"
rsh "cd ~/$REMOTE_DIR && ./run.sh --check-feeds" | tail -2

# Executing the unit is the only way to prove the systemd sandbox actually lets
# SQLite write to state/. A permissions problem found now beats a silent failure
# at 08:00 tomorrow. With nothing new to report this sends no email but still
# writes a run record - exactly the check required.
echo "  starting briefing.service once…"
rsh "systemctl --user start briefing.service" 2>/dev/null || true
RESULT="$(rsh "systemctl --user show briefing.service -p ExecMainStatus --value" 2>/dev/null || echo '?')"
if [ "$RESULT" = "0" ]; then
  ok "service ran successfully inside the systemd sandbox"
else
  warn "service exited with status $RESULT - inspect with:"
  warn "    ssh $TARGET journalctl --user -u briefing.service -n 40"
  warn "    ssh $TARGET tail -40 ~/$REMOTE_DIR/state/logs/systemd.err.log"
fi
rsh "systemctl --user list-timers briefing.timer --no-pager" | sed -n '1,3p'

# ------------------------------------------------- stop the Mac duplicating
say "7/7  Local schedule"

if [ "$KEEP_MAC_SCHEDULE" = "1" ]; then
  warn "leaving the Mac schedule active - both machines will send, from separate"
  warn "send-histories, so you will receive duplicates."
elif REMOVED="$(remove_local_agents "$DIR")"; then
  ok "local agent removed ($(echo "$REMOVED" | tr '\n' ' ')) - the server is now the only sender"
  ok "your local copy still works for manual runs: ./run.sh --dry-run"
else
  ok "no local schedule installed - nothing to disable"
fi

cat <<EOM

$(printf '\033[1mDeployed.\033[0m') The server sends the briefing at 08:00 $REMOTE_TZ.

  Run it now          ssh $TARGET 'cd ~/$REMOTE_DIR && ./run.sh'
  Preview, no send    ssh $TARGET 'cd ~/$REMOTE_DIR && ./run.sh --dry-run'
  Why was it empty    ssh $TARGET 'cd ~/$REMOTE_DIR && ./run.sh --diagnose'
  History + health    ssh $TARGET 'cd ~/$REMOTE_DIR && ./run.sh --stats'
  Timer status        ssh $TARGET 'systemctl --user list-timers briefing.timer'
  Logs                ssh $TARGET 'journalctl --user -u briefing.service -n 50'

Rerun ./deploy.sh after any change - it is idempotent and preserves the send
history already on the server.
EOM
