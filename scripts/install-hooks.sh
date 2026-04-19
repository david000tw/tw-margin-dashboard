#!/usr/bin/env bash
# Install git pre-commit hook that runs `python pipeline.py check` before every commit.
# Usage (from repo root):  bash scripts/install-hooks.sh

set -e
cd "$(dirname "$0")/.."

HOOK="$(git rev-parse --git-dir)/hooks/pre-commit"

cat > "$HOOK" <<'EOF'
#!/usr/bin/env bash
# Pre-commit: validate data integrity before commit.
set -e
cd "$(git rev-parse --show-toplevel)"

echo "[pre-commit] python pipeline.py check"
python pipeline.py check
EOF

chmod +x "$HOOK"
echo "Installed: $HOOK"
echo "Test:  touch /tmp/x && git add /tmp/x && git commit -m test  (should run check)"
echo "Skip once:  git commit --no-verify -m '...'"
