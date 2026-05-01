#!/usr/bin/env python3
"""
System Metrics Service
----------------------
Lightweight macOS service that periodically samples CPU temperature, CPU usage
(overall and per-core), memory, swap, disk, network, and load average, then
pushes the data to a local InfluxDB v1.x instance.

Non-secret configuration (host, db, poll interval) is loaded from a `.env`
file living next to this script. InfluxDB credentials are read from
`~/.netrc` (the entry whose `machine` matches `INFLUX_HOST`).
"""


from __future__ import annotations

import logging
import netrc
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil
from dotenv import load_dotenv
from influxdb import InfluxDBClient
from influxdb.exceptions import InfluxDBClientError, InfluxDBServerError
from requests.exceptions import ConnectionError as RequestsConnectionError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")


def _load_credentials_from_netrc(machine: str, netrc_path: str | None) -> tuple[str, str]:
    """Look up (login, password) for `machine` in a netrc file.

    Returns ("", "") if no entry is found or the file is missing.
    Logs a clear warning if the file exists but has bad permissions / format.
    """
    try:
        rc = netrc.netrc(netrc_path) if netrc_path else netrc.netrc()
    except FileNotFoundError:
        return "", ""
    except netrc.NetrcParseError as exc:
        # Most common cause on POSIX: file isn't chmod 600
        logging.getLogger("metrics_service").warning(
            "Could not parse netrc (%s). Make sure it is chmod 600 and well-formed.", exc
        )
        return "", ""

    auth = rc.authenticators(machine)
    if not auth:
        return "", ""
    login, _account, password = auth
    return login or "", password or ""


INFLUX_HOST = os.getenv("INFLUX_HOST", "127.0.0.1")
INFLUX_PORT = int(os.getenv("INFLUX_PORT", "8086"))
INFLUX_DB = os.getenv("INFLUX_DB", "maxs_mbp")

# Credentials: prefer ~/.netrc, fall back to env vars (handy for ad-hoc testing).
NETRC_PATH = os.getenv("NETRC_PATH") or None  # None => default ~/.netrc
NETRC_MACHINE = os.getenv("NETRC_MACHINE") or INFLUX_HOST
_netrc_user, _netrc_password = _load_credentials_from_netrc(NETRC_MACHINE, NETRC_PATH)
INFLUX_USER = _netrc_user or os.getenv("INFLUX_USER", "")
INFLUX_PASSWORD = _netrc_password or os.getenv("INFLUX_PASSWORD", "")
INFLUX_CREDS_SOURCE = (
    "netrc" if _netrc_user else ("env" if INFLUX_USER else "none")
)

INFLUX_SSL = os.getenv("INFLUX_SSL", "false").lower() in ("1", "true", "yes")
INFLUX_VERIFY_SSL = os.getenv("INFLUX_VERIFY_SSL", "false").lower() in ("1", "true", "yes")
INFLUX_TIMEOUT = int(os.getenv("INFLUX_TIMEOUT", "5"))
INFLUX_MEASUREMENT = os.getenv("INFLUX_MEASUREMENT", "system_metrics")

POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "10"))
HOSTNAME = os.getenv("HOSTNAME_OVERRIDE") or socket.gethostname()
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

OSX_CPU_TEMP_BIN = os.getenv("OSX_CPU_TEMP_BIN") or shutil.which("osx-cpu-temp") or "/usr/local/bin/osx-cpu-temp"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("metrics_service")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

_shutdown = False


def _handle_signal(signum: int, _frame: Any) -> None:
    global _shutdown
    log.info("Received signal %s, shutting down...", signum)
    _shutdown = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

# ---------------------------------------------------------------------------
# Metric collection
# ---------------------------------------------------------------------------


def read_cpu_temp_celsius() -> float | None:
    """Read CPU temperature in Celsius using osx-cpu-temp."""
    if not Path(OSX_CPU_TEMP_BIN).exists():
        log.debug("osx-cpu-temp not found at %s", OSX_CPU_TEMP_BIN)
        return None
    try:
        out = subprocess.check_output([OSX_CPU_TEMP_BIN, "-c"], timeout=5).decode().strip()
        # Output looks like "66.2°C"
        cleaned = out.replace("°C", "").replace("C", "").strip()
        value = float(cleaned)
        if value <= 0:
            # SMC sometimes returns 0.0 if it can't read the sensor
            return None
        return value
    except (subprocess.SubprocessError, ValueError) as exc:
        log.warning("Failed to read CPU temperature: %s", exc)
        return None


def collect_metrics() -> dict[str, float | int]:
    """Gather a snapshot of system metrics."""
    fields: dict[str, float | int] = {}

    # CPU usage. percpu=True returns a list of per-core %
    per_core = psutil.cpu_percent(interval=None, percpu=True)
    for idx, pct in enumerate(per_core):
        fields[f"cpu_core_{idx}"] = float(pct)
    fields["cpu_percent"] = float(psutil.cpu_percent(interval=None))
    fields["cpu_count_logical"] = psutil.cpu_count(logical=True) or 0
    fields["cpu_count_physical"] = psutil.cpu_count(logical=False) or 0

    # Frequency (may be empty on Apple Silicon, fine on Intel)
    try:
        freq = psutil.cpu_freq()
        if freq:
            fields["cpu_freq_mhz"] = float(freq.current)
    except Exception:  # noqa: BLE001
        pass

    # Load averages
    try:
        load1, load5, load15 = os.getloadavg()
        fields["load_avg_1m"] = float(load1)
        fields["load_avg_5m"] = float(load5)
        fields["load_avg_15m"] = float(load15)
    except OSError:
        pass

    # Memory
    vm = psutil.virtual_memory()
    fields["memory_total_bytes"] = int(vm.total)
    fields["memory_used_bytes"] = int(vm.used)
    fields["memory_available_bytes"] = int(vm.available)
    fields["memory_percent"] = float(vm.percent)

    sm = psutil.swap_memory()
    fields["swap_total_bytes"] = int(sm.total)
    fields["swap_used_bytes"] = int(sm.used)
    fields["swap_percent"] = float(sm.percent)

    # Root disk
    try:
        du = psutil.disk_usage("/")
        fields["disk_total_bytes"] = int(du.total)
        fields["disk_used_bytes"] = int(du.used)
        fields["disk_free_bytes"] = int(du.free)
        fields["disk_percent"] = float(du.percent)
    except OSError:
        pass

    # Network (cumulative since boot)
    try:
        net = psutil.net_io_counters()
        fields["net_bytes_sent"] = int(net.bytes_sent)
        fields["net_bytes_recv"] = int(net.bytes_recv)
        fields["net_packets_sent"] = int(net.packets_sent)
        fields["net_packets_recv"] = int(net.packets_recv)
    except Exception:  # noqa: BLE001
        pass

    # Uptime / process count
    fields["uptime_seconds"] = int(time.time() - psutil.boot_time())
    fields["process_count"] = len(psutil.pids())

    # CPU temperature
    temp = read_cpu_temp_celsius()
    if temp is not None:
        fields["cpu_temp_c"] = temp

    return fields


# ---------------------------------------------------------------------------
# InfluxDB client
# ---------------------------------------------------------------------------


def make_client() -> InfluxDBClient:
    log.info(
        "Connecting to InfluxDB at %s://%s:%d (db=%s, user=%s)",
        "https" if INFLUX_SSL else "http",
        INFLUX_HOST,
        INFLUX_PORT,
        INFLUX_DB,
        INFLUX_USER or "<none>",
    )
    return InfluxDBClient(
        host=INFLUX_HOST,
        port=INFLUX_PORT,
        username=INFLUX_USER or None,
        password=INFLUX_PASSWORD or None,
        database=INFLUX_DB,
        ssl=INFLUX_SSL,
        verify_ssl=INFLUX_VERIFY_SSL,
        timeout=INFLUX_TIMEOUT,
    )


def write_point(client: InfluxDBClient, fields: dict[str, float | int]) -> bool:
    point = {
        "measurement": INFLUX_MEASUREMENT,
        "tags": {
            "host": HOSTNAME,
        },
        "fields": fields,
    }
    try:
        return bool(client.write_points([point]))
    except (InfluxDBClientError, InfluxDBServerError, RequestsConnectionError, OSError) as exc:
        log.warning("Failed to write point: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> int:
    log.info(
        "Starting metrics service (host=%s, interval=%.1fs, measurement=%s, creds_source=%s)",
        HOSTNAME,
        POLL_INTERVAL,
        INFLUX_MEASUREMENT,
        INFLUX_CREDS_SOURCE,
    )
    if INFLUX_CREDS_SOURCE == "none":
        log.warning(
            "No InfluxDB credentials found. Add an entry for '%s' to ~/.netrc "
            "(machine %s / login ... / password ...) and restart the service.",
            NETRC_MACHINE, NETRC_MACHINE,
        )

    # Prime psutil.cpu_percent so the first reading isn't 0.0
    psutil.cpu_percent(interval=None, percpu=True)
    psutil.cpu_percent(interval=None)

    client = make_client()
    backoff = 1.0
    max_backoff = 60.0

    next_tick = time.monotonic()
    while not _shutdown:
        try:
            fields = collect_metrics()
        except Exception as exc:  # noqa: BLE001
            log.exception("Error collecting metrics: %s", exc)
            fields = {}

        if fields:
            ok = write_point(client, fields)
            if ok:
                if backoff != 1.0:
                    log.info("InfluxDB write recovered.")
                backoff = 1.0
                log.debug("Wrote %d fields to InfluxDB", len(fields))
            else:
                log.warning("Write failed, backing off %.1fs", backoff)
                # Sleep for the backoff interval but stay responsive to signals
                slept = 0.0
                while slept < backoff and not _shutdown:
                    time.sleep(min(0.5, backoff - slept))
                    slept += 0.5
                backoff = min(max_backoff, backoff * 2)
                # Recreate client in case socket is dead
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass
                client = make_client()
                next_tick = time.monotonic()
                continue

        # Schedule next tick on a fixed cadence (avoids drift from work time)
        next_tick += POLL_INTERVAL
        sleep_for = next_tick - time.monotonic()
        if sleep_for < 0:
            # We fell behind; reset cadence
            next_tick = time.monotonic()
            sleep_for = POLL_INTERVAL

        # Sleep in small chunks so SIGTERM is handled promptly
        end = time.monotonic() + sleep_for
        while not _shutdown and time.monotonic() < end:
            time.sleep(min(0.5, end - time.monotonic()))

    log.info("Metrics service stopped cleanly.")
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
