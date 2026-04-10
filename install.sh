#!/usr/bin/env bash
# One-shot install: venv, editable pip install, user systemd units, shell alias.
# Run from the repo root: ./install.sh
# Or one line after clone: bash ~/kernelIQ/install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

USER_SYSTEMD="${HOME}/.config/systemd/user"
SERVICE_DEST="${USER_SYSTEMD}/kerneliq-daemon.service"
RETENTION_SERVICE_DEST="${USER_SYSTEMD}/kerneliq-retention.service"
RETENTION_TIMER_SRC="${SCRIPT_DIR}/kerneliq-retention.timer"
RETENTION_TIMER_DEST="${USER_SYSTEMD}/kerneliq-retention.timer"
VENV_PY="${SCRIPT_DIR}/.venv/bin/python"
VENV_PIP="${SCRIPT_DIR}/.venv/bin/pip"
VENV_KERNELIQ="${SCRIPT_DIR}/.venv/bin/kerneliq"

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.11+ first."
  exit 1
fi

if ! python3 -c 'import sys; exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "ERROR: Python 3.11+ required (found: $(python3 -V 2>&1))."
  exit 1
fi

# Capture python version for use in apt package name (e.g. python3.12-venv)
PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"

# ---------------------------------------------------------------------------
# Virtual environment
# ---------------------------------------------------------------------------

_create_venv() {
  # Temporarily disable exit-on-error so we can catch venv failure
  # and attempt the apt fix before bailing out.
  # python3 -m venv prints its own error and exits non-zero on Ubuntu
  # when python3.X-venv is not installed — set -e would kill the script
  # before we could recover.
  set +e
  python3 -m venv "${SCRIPT_DIR}/.venv" 2>/dev/null
  local venv_exit=$?
  set -e

  if [[ ${venv_exit} -eq 0 ]]; then
    return 0
  fi

  # On Ubuntu/Debian, venv fails if python3.X-venv is not installed.
  # Try to install it automatically.
  if command -v apt-get >/dev/null 2>&1; then
    VENV_PKG="python${PY_VERSION}-venv"
    echo "venv creation failed — installing ${VENV_PKG} ..."

    # When piped through bash (curl | bash) there may be no TTY for
    # sudo password prompts. Try without sudo first (works if running
    # as root), then fall back to sudo.
    if apt-get install -y "${VENV_PKG}" >/dev/null 2>&1 || \
       sudo apt-get install -y "${VENV_PKG}" 2>&1; then
      echo "${VENV_PKG} installed."
      rm -rf "${SCRIPT_DIR}/.venv"
      python3 -m venv "${SCRIPT_DIR}/.venv"
      return 0
    else
      echo ""
      echo "ERROR: Could not install ${VENV_PKG} automatically."
      echo "Run this manually then re-run the installer:"
      echo "  sudo apt install ${VENV_PKG}"
      exit 1
    fi
  fi

  echo ""
  echo "ERROR: Could not create virtual environment."
  echo "Install the venv package for your Python version and re-run:"
  echo "  sudo apt install python${PY_VERSION}-venv"
  exit 1
}

if [[ ! -d "${SCRIPT_DIR}/.venv" ]]; then
  echo "Creating virtual environment in .venv ..."
  _create_venv
fi

# If .venv directory exists but python binary is missing, it is broken.
# Recreate it.
if [[ ! -f "${VENV_PY}" ]]; then
  echo "Existing .venv appears broken — recreating ..."
  rm -rf "${SCRIPT_DIR}/.venv"
  _create_venv
fi

# ---------------------------------------------------------------------------
# Bootstrap pip if missing
# ---------------------------------------------------------------------------

if [[ ! -f "${VENV_PIP}" ]]; then
  echo "pip not found in venv — bootstrapping ..."

  if "${VENV_PY}" -m ensurepip --upgrade 2>/dev/null; then
    echo "pip bootstrapped via ensurepip."

  elif command -v curl >/dev/null 2>&1; then
    echo "ensurepip unavailable — fetching get-pip.py ..."
    curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
    "${VENV_PY}" /tmp/get-pip.py --quiet
    rm -f /tmp/get-pip.py
    echo "pip bootstrapped via get-pip.py."

  elif command -v apt-get >/dev/null 2>&1; then
    echo "Attempting to install python3-pip ..."
    apt-get install -y python3-pip >/dev/null 2>&1 || \
      sudo apt-get install -y python3-pip >/dev/null 2>&1
    "${VENV_PY}" -m ensurepip --upgrade
    echo "pip bootstrapped."

  else
    echo "ERROR: pip missing from venv and could not bootstrap automatically."
    echo "Fix: sudo apt install python3-pip, then re-run install.sh"
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# Install KernelIQ
# ---------------------------------------------------------------------------

echo "Installing KernelIQ (editable) and dependencies ..."
"${VENV_PIP}" install -q -U pip
"${VENV_PIP}" install -q -e "${SCRIPT_DIR}"

# ---------------------------------------------------------------------------
# systemd user units
# ---------------------------------------------------------------------------

mkdir -p "${USER_SYSTEMD}"

write_unit_daemon() {
  cat >"${SERVICE_DEST}" <<EOF
# Generated by install.sh — do not edit; re-run ./install.sh after moving the repo.
[Unit]
Description=KernelIQ Background Telemetry Daemon
After=network.target

[Service]
Type=simple
ExecStart=${VENV_PY} -m daemon.loop
WorkingDirectory=${SCRIPT_DIR}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
}

write_unit_retention() {
  cat >"${RETENTION_SERVICE_DEST}" <<EOF
# Generated by install.sh — do not edit; re-run ./install.sh after moving the repo.
[Unit]
Description=KernelIQ Database Retention Cleanup

[Service]
Type=oneshot
ExecStart=${VENV_PY} -m db.retention
WorkingDirectory=${SCRIPT_DIR}
StandardOutput=journal
StandardError=journal
EOF
}

write_unit_daemon
write_unit_retention

if [[ ! -f "${RETENTION_TIMER_SRC}" ]]; then
  echo "ERROR: kerneliq-retention.timer not found in repo root."
  exit 1
fi
cp "${RETENTION_TIMER_SRC}" "${RETENTION_TIMER_DEST}"

systemctl --user daemon-reload
systemctl --user enable kerneliq-daemon
systemctl --user restart kerneliq-daemon
echo "KernelIQ daemon installed and started (user systemd)."

systemctl --user enable kerneliq-retention.timer
systemctl --user start kerneliq-retention.timer
echo "KernelIQ retention timer installed."

# ---------------------------------------------------------------------------
# Shell alias
# ---------------------------------------------------------------------------

Q_DIR="$(printf '%q' "${SCRIPT_DIR}")"
ALIAS_LINE="alias kerneliq='cd ${Q_DIR} && ${Q_DIR}/.venv/bin/kerneliq'"
BASHRC="${HOME}/.bashrc"

if [[ -f "${BASHRC}" ]] && grep -Fq "alias kerneliq=" "${BASHRC}"; then
  echo "Note: ~/.bashrc already has a kerneliq alias; update it manually if the repo path changed."
else
  printf '\n# KernelIQ interactive REPL\n%s\n' "${ALIAS_LINE}" >>"${BASHRC}"
  echo "Appended kerneliq alias to ~/.bashrc"
fi

# ---------------------------------------------------------------------------
# Config — auto-create from example if not present
# ---------------------------------------------------------------------------

if [[ ! -f "${SCRIPT_DIR}/kerneliq.toml" ]]; then
  cp "${SCRIPT_DIR}/kerneliq.toml.example" "${SCRIPT_DIR}/kerneliq.toml"
  echo ""
  echo "kerneliq.toml created from example."
  echo "Edit it to set your LLM backend:"
  echo "  nano ${SCRIPT_DIR}/kerneliq.toml"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " KernelIQ installed successfully."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo " Configure your LLM backend:"
echo "   nano ${SCRIPT_DIR}/kerneliq.toml"
echo ""
echo " In future terminals just type:"
echo "   kerneliq"
echo ""
echo " Daemon logs:  journalctl --user -u kerneliq-daemon -f"
echo " Restart:      systemctl --user restart kerneliq-daemon"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo " Launching KernelIQ..."
echo ""

# Replace this shell with the kerneliq REPL immediately.
# The user lands in the REPL without needing to open a new terminal.
# When they exit kerneliq they return to their original terminal context.
# In future terminals the 'kerneliq' alias works directly.
exec < /dev/tty
exec "${VENV_KERNELIQ}"