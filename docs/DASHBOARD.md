# Dashboard installation and recovery

The optional **Tigo Energy** dashboard is a native Lovelace dashboard generated
from the Home Assistant entity and device registries. It is separate from the
custom integration: installing the integration never edits a user's dashboard.

The deployed path is:

```text
/tigo-energy/overview
```

## Design

The dashboard contains four core responsive views and one conditional view:

- **Overview** — latest Tigo power, observed peak, current-day energy,
  reporting-module count, source age, and 24-hour power history. Normal cloud
  health does not consume a card; connection and stale-data exceptions appear
  only when action is needed.
- **Energy** — day/week/month/year/lifetime totals, daily Recorder history, and
  a 48-hour power trend without policy or diagnostic copy.
- **Modules** — compact per-string lists for module power and daily energy,
  grouped from topology rather than generated entity names.
- **Compare** — present only after an exact Tigo/EG4 inverter identity match and
  strict validation of EG4 total-PV power and daily solar-yield metadata. Values
  remain side by side and are never summed or silently scaled.
- **Diagnostics** — cloud health, exact source timing, unavailable readings,
  sanitized topology, configured array capacity, polling/version details, and
  measurement guidance.

Only built-in Home Assistant cards are used. The generator does not install
custom cards or frontend resources, hard-code generated entity IDs, expose
controls, or change the global Energy configuration.

## Prerequisites

- Tigo Energy Cloud is installed, configured, and has completed one successful
  refresh.
- Node.js 22 or newer is available on the machine running the deployment tool.
- The repository is checked out on that machine.
- A Home Assistant administrator long-lived access token is available.
- The machine can reach the Home Assistant base URL over HTTP(S) and WebSocket.

The dashboard tooling has no runtime npm package dependencies. Run commands from
the repository root.

Set connection details in the current shell. Avoid putting a token directly in
shell history:

```bash
export HA_BASE_URL='https://home-assistant.example'
read -rsp 'Home Assistant token: ' HA_TOKEN
export HA_TOKEN
printf '\n'
```

`TIGO_HA_TOKEN` is also accepted and takes precedence over `HA_TOKEN`. The tool
does not load `.env` files and never needs the Tigo account password.

When Home Assistant contains multiple Tigo system devices, select one by its
Home Assistant device ID or Tigo system ID:

```bash
export TIGO_SYSTEM_DEVICE_ID='123456'
```

EG4 matching is automatic only when one numeric inverter serial in the Tigo
topology exactly matches an `eg4_web_monitor` device identifier and that device
owns the required compatible total sensors. A linked station or battery device
is not eligible. If automatic identity is unavailable, an exact Home Assistant
EG4 device ID or device name can be selected explicitly:

```bash
export EG4_INVERTER_DEVICE_ID='home-assistant-device-id'
```

The selector does not bypass unit/state-class validation, and multi-inverter
Tigo systems remain fail-closed because one EG4 inverter would not represent a
whole-system Tigo total.

`HA_TIMEOUT_MS` can override the default 15,000 ms API timeout for a slow
connection.

## Preflight and plan

Preflight is read-only. It verifies authentication, the required Home Assistant
APIs, the Tigo config entry, registry relationships, and available entities:

```bash
npm run dashboard -- preflight
```

Generate and inspect a read-only deployment plan before changing Lovelace:

```bash
npm run dashboard -- plan
```

Resolve missing/inactive entities or authentication errors before deployment.
The generator tolerates legitimate unavailable module values, but it must be
able to identify the configured Tigo system unambiguously.

## Deploy

```bash
npm run dashboard -- deploy
```

When a create or update is required, deployment performs the following
transaction. An unchanged dashboard exits without a write or unnecessary
backup:

1. Read the current Lovelace dashboard registry and any existing `tigo-energy`
   storage dashboard.
2. Re-run registry discovery and validate the generated native-card document.
3. Write a checksummed backup with file mode `0600` in a newly created
   `/tmp/tigo-energy-dashboard-*` directory.
4. Register/update the dashboard and save its Lovelace configuration.
5. Read the result back and validate it.
6. Roll back automatically if a write or round-trip validation fails.

The command prints a line like:

```text
backup=/tmp/tigo-energy-dashboard-abc123/backup.json
```

`/tmp` is normally temporary. Copy that exact backup file to encrypted/private
storage if it may be needed after a reboot. Although mode `0600` limits local
access, the backup can reveal entity IDs and household configuration metadata;
never commit it to Git.

Open **Tigo Energy** from the Home Assistant sidebar or navigate directly to
`/tigo-energy/overview` after a successful deployment. A sparse history graph
immediately after installation is expected because v0.2 does not backfill
Home Assistant Recorder history.

## Restore

Restore the exact backup printed during deployment:

```bash
npm run dashboard -- restore /path/to/backup.json
```

Restore validates the checksum and refuses to overwrite a dashboard that has
drifted since the backup was created. If the current changes have been reviewed
and discarding them is intentional, explicitly override that safeguard:

```bash
npm run dashboard -- restore /path/to/backup.json --force
```

Use `--force` only after preserving the current dashboard or confirming that its
changes are disposable.

## Visual acceptance gate

Before treating a public or live deployment as complete, inspect at least:

- desktop at approximately 1440 × 1000;
- mobile at approximately 390 × 844;
- light and dark themes;
- every deployed view and its full scroll height;
- current, stale, unavailable, and partial-reporting presentation;
- browser console and Home Assistant/Lovelace errors;
- links/navigation and the `/tigo-energy/overview` direct route;
- public copy for development labels, placeholders, or credential data.

The repository's visual-QA utility reads the deployed view list, then automates
every view at the desktop and mobile sizes in light/dark color modes. It uses
Chromium's DevTools protocol and creates mode-`0600` screenshots plus a JSON
report:

```bash
# Optional when Chromium is not installed at /usr/bin/chromium-browser
export CHROMIUM_BIN='/path/to/chromium'
npm run qa:visual -- --output-dir /tmp/tigo-energy-visual-qa
```

Review every captured scroll segment and `report.json`; passing automation is
not a substitute for inspecting the rendered result. The output contains
household states and is a local private QA artifact, not release material.

## Energy-dashboard double-count warning

The Tigo dashboard never registers an Energy source. If EG4 or another inverter
integration already measures the same physical array, adding Tigo lifetime
production to Home Assistant Energy will count that production twice. Choose one
authoritative source for each physical array.

This does not prevent using the Tigo dashboard alongside an EG4 dashboard. The
optional Compare view is observational: it uses EG4 **PV Total Power** and
**Yield**, displays each source independently, and never changes the global
Energy configuration.

## Reading Tigo versus EG4

- Tigo cloud measurements are delayed and normally have 15-minute granularity;
  use **Source age**, not the Home Assistant entity refresh time.
- Compare **Tigo cloud system power** with EG4 **PV Total Power**. Do not compare
  it with load, grid import/export, AC output, or one PV input.
- Daily charts use Recorder's `change` statistic on each source's daily-energy
  sensor. This avoids a one-time lifetime-counter initialization discontinuity
  and does not manufacture historical data before installation.
- Completed-day energy is more meaningful than unsynchronized live values.
- The displayed **Configured DC capacity** is the sum of verified panel ratings
  from the Tigo build configuration. Observed peak is not nameplate, and a
  rounded chart-axis tick is not a cap.
- An inverter label such as 18kPV describes input capability, not actual array
  size or continuous AC output.

## Common failures

| Symptom | Action |
| --- | --- |
| `HA_BASE_URL` is missing | Export the externally reachable base URL without a trailing slash requirement. |
| Token rejected | Create/use an administrator long-lived access token and export it again. |
| WebSocket connection fails | Confirm proxy WebSocket support and that the URL scheme/host is correct. |
| No Tigo integration found | Configure the integration and wait for its first successful refresh. |
| Required system entities missing | Reload/update the integration before regenerating the plan. |
| Some module values unavailable | Compare Tigo Cloud data; missing values are intentionally not coerced to zero. |
| Compare view is absent | Confirm one EG4 inverter owns compatible `PV Total Power` (W, measurement) and `Yield` (kWh, total_increasing) sensors; use `EG4_INVERTER_DEVICE_ID` only for an exact intended device. |
| Daily chart is sparse | Recorder starts when the integration/entities exist; the dashboard does not fabricate a historical backfill. |
| Restore reports drift | Review current changes, preserve them if needed, and use `--force` only intentionally. |
| Old sample displayed as stale | Check the CCA/Tigo cloud path; increasing poll frequency cannot create a new source sample. |
