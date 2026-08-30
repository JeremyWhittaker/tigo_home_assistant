# Tigo Energy Cloud for Home Assistant

[![CI](https://github.com/JeremyWhittaker/tigo_home_assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/JeremyWhittaker/tigo_home_assistant/actions/workflows/ci.yml)
[![Home Assistant validation](https://github.com/JeremyWhittaker/tigo_home_assistant/actions/workflows/validate.yml/badge.svg)](https://github.com/JeremyWhittaker/tigo_home_assistant/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/JeremyWhittaker/tigo_home_assistant)](LICENSE)

Tigo Energy Cloud is a read-only custom integration that brings system and
module-level Tigo solar production data into Home Assistant. It is designed for
Tigo EI accounts that do not have paid API access and includes an optional,
native-card Lovelace dashboard.

> [!IMPORTANT]
> This is an unofficial community project. It is not affiliated with, endorsed
> by, or supported by Tigo Energy. It uses the private JSON endpoints used by
> Tigo's mobile application; those endpoints can change without notice. Do not
> rely on this integration for billing, safety, protection, or equipment
> control.

The integration does **not** use RS-485, access the CCA locally, scrape HTML, or
send control commands.

## What it provides

- Home Assistant UI setup, system discovery, reauthentication, and poll options.
- System power, today's peak power, and production totals for today, week,
  month, year, and lifetime.
- Configured DC nameplate capacity when every panel has a valid wattage in the
  Tigo build configuration.
- Power and daily energy for every module reported across all system CCAs.
- Separate cloud-connectivity and data-freshness status.
- Stable entity and device identities derived from Tigo system/module IDs.
- Automatic discovery of newly added modules during the daily topology refresh.
- A responsive, optional dashboard built entirely with Home Assistant cards,
  including a fail-closed EG4 comparison when one inverter can be identified
  exactly.
- Sanitized diagnostics, automated tests, HACS validation, and hassfest checks.

This integration is read-only. It cannot change inverter, optimizer, gateway,
or account settings.

## Understand the cloud delay

Tigo's Basic cloud data has 15-minute granularity. Tigo normally publishes a
sample about 10–20 minutes after it was measured, although CCA connectivity or
cloud processing can make the delay substantially longer.

The default schedule checks every 5 minutes during daylight and every 30 minutes
at night. Faster checks can discover a newly published sample sooner, but they
cannot make Tigo produce real-time data. The integration preserves Tigo's source
timestamp, reports the data age, and marks daylight data stale after 45 minutes.
It never presents an old sample as live data.

## Understand power, capacity, and the 18kPV label

These values answer different questions and should not be treated as
interchangeable limits:

| Reading | Meaning |
| --- | --- |
| Configured DC capacity | Sum of the panel wattages stored in the Tigo build configuration; this is array nameplate, not expected real-time output |
| Tigo current power | Latest delayed Tigo cloud system reading, or the valid module-sample sum when the homepage value is absent |
| Observed peak today | Highest Tigo sample seen during the current Tigo day; this is telemetry, not an equipment rating |
| Graph-axis maximum | A display scale chosen automatically by Home Assistant; a rounded tick such as 6 kW is not a configured cap |
| EG4 18kPV | Inverter model/capability label; the inverter supports 18 kW utilized PV input and 12 kW total continuous AC output, but that does not define the installed array size |

The dashboard never doubles Tigo to force agreement with an inverter. Its
optional **Compare** view uses only an exactly matched EG4 device, validates the
total-PV and solar-yield entity semantics, and presents the sources side by
side. Tigo and EG4 update on different cadences, so completed-day energy is a
fairer comparison than two screen values captured at the same wall-clock time.
A persistent large discrepancy should be investigated as a monitoring or
commissioning issue, not hidden with a correction factor.

See the official [EG4 18kPV specifications](https://eg4electronics.com/wp-content/uploads/2024/04/EG4-18KPV-12LV-Spec-Sheet.pdf),
[Tigo monitoring terminology](https://support.tigoenergy.com/hc/en-us/articles/205575867-Commonly-Used-Terms),
and [Tigo cloud-update guidance](https://support.tigoenergy.com/hc/en-us/articles/12583024225043-How-fast-does-data-update-in-the-EI-portal-and-does-Premium-speed-up-this-process).

## Installation

The minimum supported Home Assistant release is 2025.1.0. Automated framework
tests run against Home Assistant 2025.1.4, and release acceptance also verifies
the integration on a current Home Assistant installation.

### HACS custom repository

1. Open HACS in Home Assistant.
2. Open the HACS menu and choose **Custom repositories**.
3. Add `https://github.com/JeremyWhittaker/tigo_home_assistant` as an
   **Integration** repository.
4. Search for **Tigo Energy Cloud**, choose **Download**, and restart Home
   Assistant when prompted.
5. Go to **Settings → Devices & services → Add integration**, search for
   **Tigo Energy Cloud**, and complete setup.

The repository is not claiming inclusion in the default HACS catalog. Adding it
as a custom repository is required unless that changes in a future release.

### Manual installation

1. Download `tigo_energy.zip` from a tagged GitHub release.
2. Extract it into the Home Assistant configuration directory so the component
   is located at `custom_components/tigo_energy`.
3. Restart Home Assistant.
4. Add **Tigo Energy Cloud** from **Settings → Devices & services**.

Do not copy the repository root into `custom_components`; Home Assistant needs
the directory containing `manifest.json` at exactly
`custom_components/tigo_energy`.

## Configuration

Enter the same email address and password used for the Tigo EI application. The
flow validates the account and discovers its systems. If the account has more
than one system, select the one to add. Repeat the flow to add another system.

The integration stores the account credentials in Home Assistant's local
config-entry storage because Tigo requires them to create a session. Home
Assistant storage is not an encrypted secrets vault, so protect the host and its
backups. Access tokens are held in memory only and are not intentionally written
to diagnostics or logs.

After setup, open the integration's **Configure** dialog to adjust three values:

| Option | Default | Allowed range |
| --- | --- | --- |
| Daylight polling interval | 300 seconds (5 minutes) | 120–3,600 seconds |
| Night polling interval | 1,800 seconds (30 minutes) | 120–3,600 seconds |
| Daylight stale-data threshold | 2,700 seconds (45 minutes) | 900–21,600 seconds |

Very aggressive polling creates unnecessary traffic and does not improve
Tigo's source granularity. The defaults are recommended for a Basic account.

## Entities

The system device provides:

| Entity | Unit/type | Purpose |
| --- | --- | --- |
| Current power | W | Tigo homepage value, with valid module-sample sum as fallback |
| Peak power today | W | Highest power reported for the current Tigo day |
| Energy today | kWh | Current-day production |
| Energy this week | kWh | Current-week production |
| Energy this month | kWh | Current-month production |
| Energy this year | kWh | Current-year production |
| Lifetime energy | kWh | Authoritative lifetime total from Tigo |
| Reporting modules | count | Modules with a valid current sample |
| Last cloud update | timestamp | Timestamp attached to the Tigo sample |
| Cloud data age | min | Age of that source sample |
| Cloud connected | binary | Whether the cloud API request path is healthy |
| Data stale | binary | Whether daylight data has exceeded the freshness limit |
| Account tier | text | Basic/premium capability classification reported by Tigo |
| Module count | count | Modules in the cached system topology |
| Configured DC capacity | W | Sum of verified per-module panel ratings; unavailable unless every configured module has a rating |
| Polling interval | min | Cadence currently selected for daylight/night conditions |
| Integration version | text | Installed custom-integration version |

Each module device provides **Power** and **Energy today**. A missing module
value is unavailable, not zero. Basic-tier fields that Tigo returns empty—such
as module voltage, current, RSSI, and reclaimed power—are deliberately omitted.

Entity IDs are generated by Home Assistant and can be renamed. Automations and
the dashboard identify entities through registry metadata rather than assuming
a particular generated entity ID.

## Dashboard

The optional dashboard includes **Overview**, **Energy**, **Modules**, and
**Diagnostics** views at `/tigo-energy/overview`. A fifth **Compare** view is
added only when the generator can prove one Tigo-to-EG4 inverter match and
validate the required EG4 total sensors. It uses only built-in Home Assistant
cards and does not install frontend resources.

See [Dashboard installation and recovery](docs/DASHBOARD.md) for installation,
validation, backup, restore, and visual-QA instructions. Installing the custom
integration does not silently modify Lovelace; dashboard deployment is a
separate, explicit action.

### Home Assistant Energy warning

The dashboard does not modify Home Assistant's global Energy configuration. If
an EG4 or another inverter integration already represents the same array, adding
Tigo production there as a second solar source will double-count production.
Choose exactly one authoritative source for a physical array. The optional
Compare charts remain safe because they display the sources separately and
never add them together.

Version 0.2 does not backfill historical Recorder statistics. Tigo's current
day/week/month/year/lifetime totals appear after setup, while history charts
accumulate samples from the time the integration is installed.

## Troubleshooting

### Authentication fails

- Confirm the credentials work in the Tigo EI mobile app.
- Use the account email address rather than a display name.
- Complete any account lockout or password-reset flow directly with Tigo.
- Reauthenticate from the integration entry after changing the password.

### Cloud connected, but the sample is stale

These are intentionally separate conditions. Successful API requests only prove
that Tigo Cloud is reachable; the newest uploaded CCA sample can still be old.
Check the Tigo EI app, CCA networking/power, and Tigo service status. Increasing
the Home Assistant polling rate will not repair delayed source data.

### One or more modules are unavailable

The integration preserves missing Tigo values instead of displaying false
zeroes. Compare the module with the Tigo application and review the integration
diagnostics. A temporary missing daily-energy value can occur even when the
module exists in the system layout.

If hardware was just added, allow up to 24 hours for the cached topology to
refresh. New module devices and entities are then added automatically without
renaming existing entities. Removed modules are retained in the Home Assistant
registry as unavailable so their history and automation references are not
silently destroyed.

### The integration no longer loads data

Private mobile endpoints can change. Enable debug logging briefly, download
sanitized diagnostics, and open a GitHub issue using the issue template. Never
post credentials, access tokens, full raw responses, CCA identifiers, site
addresses, or unreviewed Home Assistant diagnostics.

```yaml
logger:
  logs:
    custom_components.tigo_energy: debug
```

Remove debug logging after reproducing the problem.

## Privacy and security

Requests go directly from Home Assistant to Tigo's cloud service; this project
does not operate an intermediary service or collect telemetry. Home Assistant
stores the credentials locally, subject to your installation's security and
backup practices. See [SECURITY.md](SECURITY.md) for the disclosure policy and
[Architecture](docs/ARCHITECTURE.md) for the request/data flow.

## Project status

This is an early `0.2.x` integration built against behavior observed on Tigo
Basic accounts. Endpoint changes, account differences, and previously unseen
topologies are expected. Read the release notes before upgrading and retain a
working Home Assistant backup.

Bug reports and carefully scoped contributions are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License and acknowledgements

Licensed under the [Apache License 2.0](LICENSE). [NOTICE](NOTICE) records
projects whose licensed work and public behavior informed the design. Tigo and
related names and marks belong to their respective owners.
