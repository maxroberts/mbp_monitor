#!/usr/bin/env bash
# Quick sanity-check that data is flowing into InfluxDB.
# Reads connection info from .env; credentials come from ~/.netrc via curl --netrc.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${PROJECT_DIR}/.env"

if [ ! -f "${ENV_FILE}" ]; then
    echo "Error: ${ENV_FILE} not found"
    exit 1
fi

# shellcheck disable=SC1090
set -a; source "${ENV_FILE}"; set +a

SCHEME="http"
[ "${INFLUX_SSL:-false}" = "true" ] && SCHEME="https"

DEFAULT_QUERY="SELECT last(\"cpu_temp_c\") AS cpu_temp_c, last(\"cpu_percent\") AS cpu_percent, last(\"memory_percent\") AS memory_percent FROM \"${INFLUX_MEASUREMENT:-system_metrics}\" WHERE host='${HOSTNAME_OVERRIDE:-$(hostname)}'"
QUERY="${1:-${DEFAULT_QUERY}}"

echo "==> Querying ${SCHEME}://${INFLUX_HOST}:${INFLUX_PORT}/query  db=${INFLUX_DB}"
echo "==> ${QUERY}"
echo

# `curl --netrc` reads ~/.netrc and sends Basic Auth automatically when the host matches.
curl --netrc -sG "${SCHEME}://${INFLUX_HOST}:${INFLUX_PORT}/query" \
    --data-urlencode "db=${INFLUX_DB}" \
    --data-urlencode "q=${QUERY}" | python3 -m json.tool
