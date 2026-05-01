# Max's MBP Metrics Service

A lightweight macOS LaunchAgent that periodically samples system metrics (CPU
temperature, per-core CPU usage, memory, swap, disk, network, load avg) and
pushes them to a local **InfluxDB v1.x** instance.

- **Host:** Max's MacBook Pro 15 (Intel Core i9-9980HK)
- **Project location:** `~/Dropbox/Computers/Maxs_MBP/`
- **InfluxDB target:** `192.168.0.218:8086`, database `maxs_mbp`
- **LaunchAgent label:** `com.local.maxs-mbp-metrics`

---

## 1. Project layout

```
~/Dropbox/Computers/Maxs_MBP/
├── metrics_service.py                  # the service (main loop)
├── requirements.txt                    # frozen Python deps
├── .env                                # non-secret config (host, db, interval, ...)
├── .env.example                        # template / reference
├── com.local.maxs-mbp-metrics.plist    # launchd job definition
├── install.sh                          # set up venv + load LaunchAgent
├── uninstall.sh                        # stop & remove LaunchAgent
├── query.sh                            # quick curl query against InfluxDB
├── grafana_dashboard.json              # importable Grafana dashboard
├── logs/
│   ├── stdout.log                      # service log (rotates? no — see "Log rotation")
│   └── stderr.log
└── venv/                               # Python virtualenv (not committed)
```

> **Secrets** (`INFLUX_USER`, `INFLUX_PASSWORD`) are **not** stored in this
> directory. They live in `~/.netrc`. See section 4 below.

---

## 2. What gets recorded

Every `POLL_INTERVAL` seconds (default **10s**), the service writes one point
to the measurement **`system_metrics`** with the tag **`host=Maxs_MBP`** and
the following fields:

| Field                    | Description                                |
| ------------------------ | ------------------------------------------ |
| `cpu_temp_c`             | CPU package temp (°C) via `osx-cpu-temp`   |
| `cpu_percent`            | Overall CPU usage (%)                      |
| `cpu_core_0` … `cpu_core_N` | Per-logical-core CPU usage (%)          |
| `cpu_count_logical`      | Logical CPU count                          |
| `cpu_count_physical`     | Physical CPU count                         |
| `cpu_freq_mhz`           | Current CPU frequency in MHz               |
| `load_avg_1m/5m/15m`     | Standard Unix load averages                |
| `memory_total_bytes`     | Total physical RAM                         |
| `memory_used_bytes`      | RAM used                                   |
| `memory_available_bytes` | RAM available                              |
| `memory_percent`         | RAM usage %                                |
| `swap_total_bytes`       | Swap total                                 |
| `swap_used_bytes`        | Swap used                                  |
| `swap_percent`           | Swap usage %                               |
| `disk_total/used/free_bytes` | Root volume (`/`) usage                |
| `disk_percent`           | Root volume usage %                        |
| `net_bytes_sent/recv`    | Cumulative network bytes (since boot)      |
| `net_packets_sent/recv`  | Cumulative network packets                 |
| `uptime_seconds`         | Seconds since boot                         |
| `process_count`          | Number of running processes                |

> **Note:** Network and uptime are **cumulative counters**. To get rates
> (e.g. MB/sec), use Influx's `derivative()` or `non_negative_derivative()`
> in your query.

---

## 3. First-time setup

Already done once — included here for reference / future re-installs.

```bash
# 1. System dep for CPU temperature
brew install osx-cpu-temp

# 2. Set up project + install Python deps + load LaunchAgent
cd ~/Dropbox/Computers/Maxs_MBP
chmod +x install.sh uninstall.sh query.sh
./install.sh
```

`install.sh` is **idempotent**: re-run it any time you change the plist or
update Python deps.

---

## 4. ⚠️ Credentials live in `~/.netrc`

The service reads InfluxDB username/password from `~/.netrc`, **not** from
`.env`. This keeps secrets out of the project directory (which lives in
Dropbox) and lets other tools (`curl`, `query.sh`) reuse the same credentials.

### One-time setup

1. Open (or create) `~/.netrc`:

   ```bash
   nano ~/.netrc
   ```

2. Add a stanza for the InfluxDB host. The `machine` value **must match
   `INFLUX_HOST` exactly** as it appears in `.env` (here: `192.168.0.218`):

   ```
   machine 192.168.0.218
     login YOUR_INFLUX_USER
     password YOUR_INFLUX_PASSWORD
   ```

   You can have multiple `machine ...` blocks in the same file (e.g. one for
   GitHub, one for Influx) — they don't interfere.

3. Lock down the file. **This is required** — Python's `netrc` module
   refuses to read a file that contains a password unless it's mode 600:

   ```bash
   chmod 600 ~/.netrc
   ```

4. Restart the service so it picks up the new credentials:

   ```bash
   launchctl kickstart -k "gui/$(id -u)/com.local.maxs-mbp-metrics"
   ```

### How resolution works

On startup the service tries the following, in order:

1. `~/.netrc` entry whose `machine` matches `INFLUX_HOST` (or `NETRC_MACHINE`
   if you set it in `.env`). ← **preferred path**
2. `INFLUX_USER` / `INFLUX_PASSWORD` env vars (commented out in `.env.example`
   — uncomment only if you really can't use netrc).
3. No auth (writes will fail with 401).

The startup log line tells you which path was used:

```
[INFO] Starting metrics service (host=Maxs_MBP, ..., creds_source=netrc)
```

`creds_source` will be one of `netrc`, `env`, or `none`.

### Changing the password later

1. Edit the relevant stanza in `~/.netrc`.
2. `launchctl kickstart -k "gui/$(id -u)/com.local.maxs-mbp-metrics"`.

### `query.sh` and netrc

`query.sh` uses `curl --netrc`, which reads the same `~/.netrc` automatically.
No additional configuration needed.

---

## 5. Verifying it works

### a) Tail the logs

```bash
tail -f ~/Dropbox/Computers/Maxs_MBP/logs/stdout.log \
        ~/Dropbox/Computers/Maxs_MBP/logs/stderr.log
```

You should see lines like:

```
2026-05-01 06:30:00 [INFO] Starting metrics service (host=Maxs_MBP, interval=10.0s, measurement=system_metrics)
2026-05-01 06:30:00 [INFO] Connecting to InfluxDB at http://192.168.0.218:8086 (db=maxs_mbp, user=...)
```

(Successful writes are silent at INFO level — bump `LOG_LEVEL=DEBUG` in `.env`
if you want to see each write.)

### b) Query InfluxDB directly

```bash
~/Dropbox/Computers/Maxs_MBP/query.sh
```

Or run any custom query:

```bash
~/Dropbox/Computers/Maxs_MBP/query.sh \
  'SELECT mean("cpu_temp_c") FROM "system_metrics" WHERE time > now() - 5m GROUP BY time(30s)'
```

### c) Check LaunchAgent status

```bash
launchctl list | grep maxs-mbp-metrics
# Output columns: PID  STATUS  LABEL
# A PID > 0 means it's running. STATUS 0 means last exit was clean.
```

---

## 5b. Grafana dashboard

A pre-built dashboard JSON is included at `grafana_dashboard.json`. It has
panels for everything the service collects:

- **Overview row:** stat tiles for CPU temp, CPU%, memory %, swap %, load 1m,
  uptime — colour-coded thresholds (green/yellow/orange/red).
- **CPU row:** time-series of CPU temperature (°C, with thresholds), per-core
  + overall CPU%, load averages (1/5/15 min), CPU frequency.
- **Memory & Swap row:** stacked-style usage charts in bytes (used vs.
  available vs. total).
- **Disk & Network row:** root-disk used/free, network throughput (bytes/sec
  via `non_negative_derivative`), packet rate (pps), and process count.
- **Templating:** a `$host` dropdown auto-populated from
  `SHOW TAG VALUES FROM system_metrics WITH KEY = host`, so you can re-use
  the same dashboard for other machines if you ever start sending metrics
  from them.
- Auto-refresh every 10s (matches the default poll interval).

### Import

1. Open Grafana → **Dashboards → New → Import**.
2. Click **Upload JSON file** and select
   `~/Dropbox/Computers/Maxs_MBP/grafana_dashboard.json`.
3. When prompted for the **InfluxDB** datasource, pick the one pointing at
   `http://192.168.0.218:8086`, database `maxs_mbp`. (If you don't have one
   configured yet, create it under **Connections → Data sources → Add new
   data source → InfluxDB**, query language **InfluxQL**, URL above, db
   `maxs_mbp`, basic-auth login from your `~/.netrc`.)
4. Click **Import**.

The dashboard is saved with UID `maxs-mbp-system-metrics` so re-importing
will overwrite the previous version cleanly.

### Or via API (optional)

```bash
GRAFANA_URL=http://192.168.0.218:3000
GRAFANA_TOKEN=...   # service-account token w/ Editor role
DS_UID=$(curl -sS -H "Authorization: Bearer $GRAFANA_TOKEN" \
   "$GRAFANA_URL/api/datasources/name/InfluxDB" | jq -r .uid)

jq --arg uid "$DS_UID" '
  {dashboard: (. | del(.__inputs, .__requires, .id) ),
   inputs: [{name:"DS_INFLUXDB", type:"datasource", pluginId:"influxdb", value:$uid}],
   overwrite: true}
' grafana_dashboard.json | curl -sS -X POST \
   -H "Authorization: Bearer $GRAFANA_TOKEN" \
   -H "Content-Type: application/json" \
   "$GRAFANA_URL/api/dashboards/import" --data @-
```

---

## 6. Common operations

### Change the polling interval

Edit `.env`:

```
POLL_INTERVAL=5     # every 5 seconds
```

Then restart:

```bash
launchctl kickstart -k "gui/$(id -u)/com.local.maxs-mbp-metrics"
```

### Change which database / host / measurement is written to

Edit the corresponding variables in `.env` (`INFLUX_HOST`, `INFLUX_DB`,
`INFLUX_MEASUREMENT`, etc.) and `kickstart` the agent.

### Add a new metric

1. Open `metrics_service.py`, find `collect_metrics()`.
2. Add a new key/value to the `fields` dict — anything `psutil` exposes is fair
   game, e.g.:

   ```python
   sensors = psutil.sensors_fans()  # if available
   ...
   fields["fan_rpm"] = ...
   ```

3. Restart:

   ```bash
   launchctl kickstart -k "gui/$(id -u)/com.local.maxs-mbp-metrics"
   ```

InfluxDB v1 schemas are dynamic — new fields will start appearing
automatically the next write cycle.

### Stop the service temporarily

```bash
launchctl bootout "gui/$(id -u)/com.local.maxs-mbp-metrics"
```

### Start it again

```bash
launchctl bootstrap "gui/$(id -u)" \
   ~/Library/LaunchAgents/com.local.maxs-mbp-metrics.plist
```

### Run it once in the foreground (for debugging)

```bash
cd ~/Dropbox/Computers/Maxs_MBP
source venv/bin/activate
LOG_LEVEL=DEBUG python metrics_service.py
# Ctrl-C to stop
```

### Update Python dependencies

```bash
cd ~/Dropbox/Computers/Maxs_MBP
source venv/bin/activate
pip install --upgrade psutil influxdb python-dotenv
pip freeze > requirements.txt
launchctl kickstart -k "gui/$(id -u)/com.local.maxs-mbp-metrics"
```

### Fully uninstall

```bash
~/Dropbox/Computers/Maxs_MBP/uninstall.sh
```

That stops the agent and removes the plist from `~/Library/LaunchAgents`.
The project files (including `venv/` and your `.env`) are left in place.

---

## 7. How the service is wired up

### Process supervisor: `launchd`

The plist `com.local.maxs-mbp-metrics.plist` is installed into
`~/Library/LaunchAgents/`. macOS's `launchd` then:

- Starts the script at login (`RunAtLoad=true`)
- Restarts it if it ever exits (`KeepAlive=true`)
- Throttles restart attempts to once per 30s (`ThrottleInterval`)
- Runs it as a **Background** process with reduced CPU/IO priority

Stdout and stderr are captured to `logs/stdout.log` and `logs/stderr.log`.

### Python script: `metrics_service.py`

A simple infinite loop that:

1. Loads `.env` from the script directory.
2. Initializes an InfluxDB v1 client.
3. Every `POLL_INTERVAL` seconds, calls `collect_metrics()` and writes one
   point.
4. Handles SIGTERM / SIGINT gracefully (so `launchctl bootout` is clean).
5. Uses exponential backoff (1s → 2s → 4s … capped at 60s) when InfluxDB is
   unreachable, and rebuilds the client on recovery.

### Why `/usr/bin/python3` (Apple Python) instead of Homebrew's?

This is deliberate, and `install.sh` enforces it. The first attempt used
Homebrew's Python 3.10 from `/usr/local/Cellar/python@3.10`, but on this Mac
all outbound TCP traffic from that interpreter fails with
`OSError(57, 'Socket is not connected')` immediately after `connect()`
returns — even at the raw `socket` level. `curl`, `ping`, and the system
Python (`/usr/bin/python3`, which Apple ships pre-exempted in the
application firewall list) all work fine to the same host.

It looks like a per-binary network sandbox / TCC restriction that's
silently dropping send/recv for unsigned Homebrew binaries even with the
firewall reportedly "off". Rather than chase the exact macOS plumbing,
`install.sh` builds the venv from `/usr/bin/python3` (currently 3.9.6 from
the Xcode CLT) and pins compatible package versions in `requirements.txt`.
If you ever need to override this:

```bash
SYSTEM_PYTHON=/path/to/your/python3 ./install.sh
```

### CPU temperature: `osx-cpu-temp`

We shell out to the Homebrew-installed binary:

```bash
$ osx-cpu-temp -c
66.2°C
```

This works on Intel Macs (T2/SMC). On Apple Silicon, `osx-cpu-temp` is not
reliable — the script will simply omit `cpu_temp_c` if the reading is missing
or zero, and continue logging everything else.

---

## 8. Log rotation

Logs are **not auto-rotated** by macOS. They grow slowly (a few KB/hour at
INFO level) but if you care, drop a `newsyslog` rule or just truncate
periodically:

```bash
# Truncate without stopping the service
: > ~/Dropbox/Computers/Maxs_MBP/logs/stdout.log
: > ~/Dropbox/Computers/Maxs_MBP/logs/stderr.log
```

Or add to your crontab:

```cron
0 3 * * 0  : > ~/Dropbox/Computers/Maxs_MBP/logs/stdout.log; : > ~/Dropbox/Computers/Maxs_MBP/logs/stderr.log
```

---

## 9. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `logs/stdout.log`: `creds_source=none` and writes return 401 | `~/.netrc` has no entry for `INFLUX_HOST`, or the `machine` name doesn't match `INFLUX_HOST` exactly. See section 4. |
| `logs/stdout.log`: `Could not parse netrc` | `~/.netrc` exists but isn't `chmod 600`, or has bad syntax. Run `chmod 600 ~/.netrc` and verify the stanza format. |
| `logs/stderr.log`: `401 Unauthorized` (creds_source=netrc) | Username/password in `~/.netrc` are wrong for that Influx server. Edit netrc, then `launchctl kickstart -k "gui/$(id -u)/com.local.maxs-mbp-metrics"`. |
| `logs/stderr.log`: `Connection refused` / `No route to host` | InfluxDB at `192.168.0.218:8086` isn't reachable. Check `curl http://192.168.0.218:8086/ping`. The service will retry with backoff. |
| `cpu_temp_c` missing from points | `osx-cpu-temp` returned 0 or isn't installed. Try `osx-cpu-temp -c` manually; reinstall with `brew reinstall osx-cpu-temp`. |
| `launchctl list \| grep maxs-mbp-metrics` shows status `78` or other non-zero | The script exited with an error — check `logs/stderr.log`. |
| Service doesn't start at login | Run `./install.sh` again. Confirm the plist is in `~/Library/LaunchAgents/`. |
| Want to relocate the project | Move the directory, then **edit absolute paths in `com.local.maxs-mbp-metrics.plist`** (4 places: `ProgramArguments`, `WorkingDirectory`, `StandardOutPath`, `StandardErrorPath`), then re-run `./install.sh`. |

---

## 10. Quick reference

| Task                           | Command |
| ------------------------------ | ------- |
| Install / reinstall            | `./install.sh` |
| Uninstall                      | `./uninstall.sh` |
| Restart after editing `.env` or `~/.netrc` | `launchctl kickstart -k "gui/$(id -u)/com.local.maxs-mbp-metrics"` |
| Edit credentials               | `nano ~/.netrc && chmod 600 ~/.netrc` |
| Tail logs                      | `tail -f logs/stdout.log logs/stderr.log` |
| Status                         | `launchctl list \| grep maxs-mbp-metrics` |
| One-shot foreground run (debug) | `source venv/bin/activate && LOG_LEVEL=DEBUG python metrics_service.py` |
| Sanity-check query             | `./query.sh` |
