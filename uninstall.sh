#!/usr/bin/env bash
# Stop and remove the maxs-mbp-metrics LaunchAgent.
set -euo pipefail

LABEL="com.local.maxs-mbp-metrics"
PLIST_DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

if launchctl list | grep -q "${LABEL}"; then
    echo "==> Stopping & unloading agent"
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || launchctl unload "${PLIST_DEST}" 2>/dev/null || true
fi

if [ -f "${PLIST_DEST}" ]; then
    rm -f "${PLIST_DEST}"
    echo "==> Removed ${PLIST_DEST}"
fi

echo "Done. Project files in $(cd "$(dirname "$0")" && pwd) are untouched."
