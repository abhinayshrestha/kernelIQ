#!/usr/bin/env bash
# Remote one-liner entrypoint: clone KernelIQ and run install.sh.
#
#   curl -fsSL https://raw.githubusercontent.com/abhinayshrestha/kernelIQ/main/scripts/bootstrap.sh | bash
#
# Optional env overrides:
#   KERNELIQ_HOME      — clone destination (default: ~/kernelIQ)
#   KERNELIQ_GIT_URL   — override the git clone URL

set -euo pipefail

KERNELIQ_HOME="${KERNELIQ_HOME:-${HOME}/kernelIQ}"
KERNELIQ_GIT_URL="${KERNELIQ_GIT_URL:-https://github.com/abhinayshrestha/kernelIQ.git}"

if [[ -e "${KERNELIQ_HOME}" ]]; then
  echo "ERROR: ${KERNELIQ_HOME} already exists. Remove it, pick KERNELIQ_HOME, or run install.sh inside the repo."
  exit 1
fi

git clone --depth 1 "${KERNELIQ_GIT_URL}" "${KERNELIQ_HOME}"
bash "${KERNELIQ_HOME}/install.sh"
