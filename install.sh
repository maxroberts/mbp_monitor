#!/usr/bin/env bash
# Install / re-install the maxs-mbp-metrics LaunchAgent.
#
# Idempotent: it's safe to re-run this script after editing the plist
# or after pulling new code.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.local.maxs-mbp-metrics"
PLIST_SRC="${PROJECT_DIR}/${LABEL}.plist"
PLIST_DEST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

echo "==> Project dir: ${PROJECT_DIR}"

# 1. Ensure dependencies
if ! command -v osx-cpu-temp >/dev/null 2>&1; then
    echo "==> Installing osx-cpu-temp via Homebrew"
    brew install osx-cpu-temp
fi

# 2. Ensure venv exists & dependencies installed
#
# IMPORTANT: we deliberately use the macOS system Python (/usr/bin/python3,
# from Xcode CLT) instead of Homebrew's. Homebrew Python on this Mac hits a
# macOS network sandbox issue where outbound TCP connections appear to
# succeed but every recv() fails with OSError(57, "Socket is not connected").
# The system Python is exempted in the application firewall list and works
# without any quirks. See README "Why /usr/bin/python3?" for details.
SYSTEM_PYTHON="${SYSTEM_PYTHON:-/usr/bin/python3}"
if [ ! -d "${PROJECT_DIR}/venv" ]; then
    echo "==> Creating Python virtualenv with ${SYSTEM_PYTHON}"
    "${SYSTEM_PYTHON}" -m venv "${PROJECT_DIR}/venv"
fi
echo "==> Installing/updating Python deps"
"${PROJECT_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${PROJECT_DIR}/venv/bin/pip" install --quiet -r "${PROJECT_DIR}/requirements.txt"

# 3. Ensure logs dir
mkdir -p "${PROJECT_DIR}/logs"

# 4. Ensure .env exists
if [ ! -f "${PROJECT_DIR}/.env" ]; then
    echo "==> Creating .env from .env.example"
    cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
fi

# 4b. Verify ~/.netrc has an entry for the InfluxDB host
INFLUX_HOST="$(grep -E '^INFLUX_HOST=' "${PROJECT_DIR}/.env" | head -n1 | cut -d= -f2-)"
NETRC_FILE="${HOME}/.netrc"
if [ -n "${INFLUX_HOST}" ]; then
    if [ ! -f "${NETRC_FILE}" ] || ! grep -qE "^[[:space:]]*machine[[:space:]]+${INFLUX_HOST}([[:space:]]|$)" "${NETRC_FILE}"; then
        cat <<EOF

⚠️  No ~/.netrc entry found for ${INFLUX_HOST}.
    The service will start, but every write will fail with 401 until you add one.

    Add this stanza to ${NETRC_FILE}:

        machine ${INFLUX_HOST}
          login YOUR_INFLUX_USER
          password YOUR_INFLUX_PASSWORD

    Then lock it down:

        chmod 600 ${NETRC_FILE}

    And restart the service:

        launchctl kickstart -k "gui/\$(id -u)/com.local.maxs-mbp-metrics"

EOF
    else
        # Sanity-check permissions
        PERMS="$(stat -f '%Lp' "${NETRC_FILE}")"
        if [ "${PERMS}" != "600" ]; then
            echo "⚠️  ${NETRC_FILE} has perms ${PERMS}; Python's netrc requires 600. Run: chmod 600 ${NETRC_FILE}"
        else
            echo "==> ~/.netrc has an entry for ${INFLUX_HOST} (perms 600 ✓)"
        fi
    fi
fi

# 5. Install LaunchAgent
mkdir -p "${HOME}/Library/LaunchAgents"
cp "${PLIST_SRC}" "${PLIST_DEST}"
echo "==> Installed plist at ${PLIST_DEST}"

# 6. (Re)load it
if launchctl list | grep -q "${LABEL}"; then
    echo "==> Unloading existing agent"
    launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || launchctl unload "${PLIST_DEST}" 2>/dev/null || true
fi
echo "==> Loading agent"
launchctl bootstrap "gui/$(id -u)" "${PLIST_DEST}" 2>/dev/null || launchctl load "${PLIST_DEST}"
launchctl enable "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl kickstart -k "gui/$(id -u)/${LABEL}" 2>/dev/null || true

echo
echo "==> Done. Service status:"
launchctl list | grep "${LABEL}" || echo "(not yet listed - check logs)"
echo
echo "Tail logs with:  tail -f '${PROJECT_DIR}/logs/stdout.log' '${PROJECT_DIR}/logs/stderr.log'"
