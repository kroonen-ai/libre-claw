#!/usr/bin/env sh
# Copyright 2026 Kroonen AI Inc. (https://kroonen.ai)
# SPDX-License-Identifier: Apache-2.0

set -eu

REPO_URL="${LIBRE_CLAW_REPO_URL:-https://github.com/kroonen-ai/libre-claw.git}"
INSTALL_DIR="${LIBRE_CLAW_INSTALL_DIR:-$HOME/.libre-claw/app}"
BIN_DIR="${LIBRE_CLAW_BIN_DIR:-$HOME/.local/bin}"
EXTRAS="${LIBRE_CLAW_EXTRAS:-browser}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

info() {
  printf '%s\n' "$*"
}

if ! command -v git >/dev/null 2>&1; then
  info "git is required but was not found on PATH."
  exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  info "$PYTHON_BIN is required but was not found on PATH."
  exit 1
fi

mkdir -p "$INSTALL_DIR" "$BIN_DIR"

case "$INSTALL_DIR" in
  ""|"/"|"$HOME")
    info "Refusing unsafe LIBRE_CLAW_INSTALL_DIR: $INSTALL_DIR"
    exit 1
    ;;
esac

if [ -d "$INSTALL_DIR/.git" ]; then
  info "Updating Libre Claw in $INSTALL_DIR"
  git -C "$INSTALL_DIR" pull --ff-only
else
  info "Installing Libre Claw into $INSTALL_DIR"
  rm -rf "$INSTALL_DIR"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

"$PYTHON_BIN" -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip

if [ -n "$EXTRAS" ]; then
  "$INSTALL_DIR/.venv/bin/python" -m pip install -e "$INSTALL_DIR[$EXTRAS]"
else
  "$INSTALL_DIR/.venv/bin/python" -m pip install -e "$INSTALL_DIR"
fi

cat > "$BIN_DIR/libre-claw" <<EOF
#!/usr/bin/env sh
exec "$INSTALL_DIR/.venv/bin/libre-claw" "\$@"
EOF
chmod +x "$BIN_DIR/libre-claw"

cat > "$BIN_DIR/lc" <<EOF
#!/usr/bin/env sh
exec "$INSTALL_DIR/.venv/bin/lc" "\$@"
EOF
chmod +x "$BIN_DIR/lc"

info "Libre Claw installed."
info "Run: $BIN_DIR/libre-claw"
info "If needed, add this to PATH: export PATH=\"$BIN_DIR:\$PATH\""
