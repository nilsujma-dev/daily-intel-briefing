#!/bin/bash
# Scan everything git would actually publish for credential material.
#
#   ./scripts/check-secrets.sh              scan, exit 1 on any finding
#   ./scripts/check-secrets.sh --install    also install it as a pre-commit hook
#
# The point is the file set: inside a repo it inspects tracked plus untracked-but-
# not-ignored files, which is exactly what a commit would capture. Real keys sitting
# in an ignored secrets/ directory are correctly invisible to it.
set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "${1:-}" = "--install" ]; then
  [ -d .git ] || { echo "Not a git repository yet - run git init first." >&2; exit 1; }
  mkdir -p .git/hooks
  cat > .git/hooks/pre-commit <<'HOOK'
#!/bin/bash
exec ./scripts/check-secrets.sh
HOOK
  chmod +x .git/hooks/pre-commit
  echo "Installed .git/hooks/pre-commit - commits are now blocked if a secret appears."
  exit 0
fi

# Built with a read loop rather than mapfile: macOS ships bash 3.2, where
# mapfile does not exist, and this script has to run on the machine doing the push.
FILES=()
collect() { while IFS= read -r line; do [ -n "$line" ] && FILES+=("$line"); done; }

if [ -d .git ] && command -v git >/dev/null; then
  collect < <(git ls-files -c -o --exclude-standard 2>/dev/null)
  SCOPE="tracked + untracked (respecting .gitignore)"
else
  collect < <(find . -type f \
    -not -path './.git/*' -not -path './secrets/*' -not -path './state/*' \
    -not -path './__pycache__/*' -not -name '*.pyc' \
    -not -name 'settings.local.json' -not -name 'deploy.local.conf' | sed 's|^\./||')
  SCOPE="working tree (no git yet; secrets/ and state/ excluded manually)"
fi

if [ ${#FILES[@]} -eq 0 ]; then echo "No files to scan."; exit 0; fi

echo "Scanning ${#FILES[@]} files - $SCOPE"

# label|regex
PATTERNS=(
  "Anthropic API key|sk-ant-[A-Za-z0-9_-]\{20,\}"
  "OpenAI API key|sk-[A-Za-z0-9]\{40,\}"
  "Google OAuth client secret|GOCSPX-[A-Za-z0-9_-]\{10,\}"
  "Google OAuth client id|[0-9]\{8,\}-[a-z0-9]\{20,\}\.apps\.googleusercontent\.com"
  "Private key block|-----BEGIN [A-Z ]*PRIVATE KEY-----"
  "Google refresh token|1//[A-Za-z0-9_-]\{30,\}"
  "AWS access key|AKIA[0-9A-Z]\{16\}"
  "Slack token|xox[baprs]-[A-Za-z0-9-]\{10,\}"
  "Generic bearer secret|[Bb]earer [A-Za-z0-9._-]\{30,\}"
)

FOUND=0
for entry in "${PATTERNS[@]}"; do
  label="${entry%%|*}"; regex="${entry#*|}"
  for f in "${FILES[@]}"; do
    [ -f "$f" ] || continue
    case "$f" in scripts/check-secrets.sh) continue ;; esac   # this file defines the patterns
    if hits=$(grep -nI "$regex" "$f" 2>/dev/null); then
      FOUND=1
      printf '\n\033[31mSECRET\033[0m  %s in %s\n' "$label" "$f"
      printf '%s\n' "$hits" | head -3 | sed 's/^/         /' | cut -c1-110
    fi
  done
done

# The ignore rules themselves are part of the safety property, so verify them.
if [ -d .git ] && command -v git >/dev/null; then
  for must in secrets/client_secret.json secrets/token.json secrets/anthropic_api_key \
              config/settings.local.json deploy.local.conf state/briefing.db; do
    if [ -e "$must" ] && ! git check-ignore -q "$must" 2>/dev/null; then
      FOUND=1
      printf '\n\033[31mNOT IGNORED\033[0m  %s exists but .gitignore does not exclude it\n' "$must"
    fi
  done
  if git ls-files --error-unmatch secrets/anthropic_api_key >/dev/null 2>&1; then
    FOUND=1
    printf '\n\033[31mTRACKED\033[0m  secrets/anthropic_api_key is in the index. Remove it:\n'
    printf '         git rm --cached secrets/anthropic_api_key\n'
  fi
fi

echo
if [ "$FOUND" = "0" ]; then
  printf '\033[32mClean.\033[0m No credential material in anything git would publish.\n'
  exit 0
fi
printf '\033[31mBlocked.\033[0m Remove the material above before committing.\n'
printf 'If it has already been pushed, rotate the credential - deleting the commit is not enough.\n'
exit 1
