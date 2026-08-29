#!/usr/bin/env bash
set -euo pipefail

SWAP_FILE="${1:-/swapfile}"
SWAP_SIZE="${2:-8G}"

if [[ ! -f "${SWAP_FILE}" ]]; then
  fallocate -l "${SWAP_SIZE}" "${SWAP_FILE}"
  chmod 600 "${SWAP_FILE}"
  mkswap "${SWAP_FILE}"
fi

if ! swapon --show=NAME --noheadings | grep -Fxq "${SWAP_FILE}"; then
  swapon "${SWAP_FILE}"
fi

if ! grep -Eq "^${SWAP_FILE//\//\\/}[[:space:]]" /etc/fstab; then
  printf '%s none swap sw 0 0\n' "${SWAP_FILE}" >> /etc/fstab
fi

printf 'vm.swappiness=10\n' > /etc/sysctl.d/99-drive-whisper-swap.conf
sysctl -p /etc/sysctl.d/99-drive-whisper-swap.conf

free -h
swapon --show
