# Data model

Tigo Energy Cloud normalizes several Tigo mobile-endpoint responses into one
coherent Home Assistant snapshot. This document defines the v0.1 entity
semantics so dashboards and automations do not have to understand raw payloads.

## Identity and hierarchy

- A config entry represents exactly one Tigo system.
- The config-entry unique ID is the decimal Tigo system ID.
- The system device identifier is namespaced by the integration domain and that
  system ID.
- Each module device is identified by its stable Tigo module object ID together
  with the system ID.
- Inverter, MPPT, and string membership are descriptive topology metadata. They
  must not be inferred from entity names or array position.

Home Assistant chooses an entity ID from the system/module display name. Entity
IDs can therefore change when a user renames an entity. Integration unique IDs
and registry relationships are the stable interface used by the dashboard.

## System entities

| Meaning | Native unit | Device class | State class | Source |
| --- | --- | --- | --- | --- |
| Current power | W | power | measurement | Tigo homepage value, falling back to a sum of valid module samples |
| Peak power today | W | power | measurement | Tigo current-day aggregate maximum |
| Energy today | kWh | energy | total_increasing | Tigo homepage daily production; unitless cloud values are Wh |
| Energy this week | kWh | energy | total_increasing | Tigo homepage weekly production; unitless cloud values are Wh |
| Energy this month | kWh | energy | total_increasing | Tigo homepage monthly production; unitless cloud values are Wh |
| Energy this year | kWh | energy | total_increasing | Tigo homepage yearly production; unitless cloud values are Wh |
| Lifetime energy | kWh | energy | total | Tigo authoritative lifetime production; unitless cloud values are Wh |
| Reporting modules | modules | — | measurement | Modules contributing a current power sample |
| Last cloud update | timestamp | timestamp | — | Newest accepted Tigo source timestamp |
| Cloud data age | min | duration | measurement | Current time minus source timestamp |
| Cloud connected | binary | connectivity | — | Latest cloud update request succeeded |
| Data stale | binary | problem | — | Daylight sample age exceeds the freshness limit |

Day/week/month/year and module-daily counters use `total_increasing`; Home
Assistant recognizes the drop at a period boundary as a new metering cycle.
Lifetime energy uses `total` because it is authoritative and non-resetting.
Values received in watt-hours are converted to kilowatt-hours exactly once at
the normalization boundary.

`Current power` uses Tigo's system homepage value when supplied. When that value
is absent, it falls back to the sum of modules with valid power samples. In
either case, read it alongside `Reporting modules` and the cloud source age;
partial module reporting is not manufactured into a complete module dataset.

## Module entities

Each discovered module has two enabled entities:

| Meaning | Native unit | Device class | State class | Availability |
| --- | --- | --- | --- | --- |
| Power | W | power | measurement | Newest valid module sample exists and is fresh |
| Energy today | kWh | energy | total_increasing | Current-day aggregate includes that module |

Useful topology/rating information belongs in device information or stable
attributes only when it is non-sensitive. Passwords, tokens, gateway request
identifiers, addresses, and raw serial/topology payloads are never attributes.

## Module sample alignment

The module-power response contains an `order` array and a sequence of timestamped
rows. Array index alone is not a module identity.

For each module object ID:

1. Locate its position in `order`.
2. Walk the timestamped rows newest-to-oldest at that position.
3. Accept the first finite numeric value.
4. Preserve the timestamp from that specific row.
5. Return unavailable if no valid value exists.

This scan is independent for every module. A future placeholder (`"-"`) or a
missing value in the last row must not erase an earlier valid value, and a
module's timestamp must not be borrowed from another module.

Daily module energy is keyed by Tigo object ID. If the service omits a key, that
module's entity is unavailable. Missing never means zero. Power and energy
entities retain their own source timestamps; one metric never borrows the
other's timestamp.

For systems with multiple CCAs, the same selection is performed for every CCA.
Results are merged by module object ID, and the newest valid per-module sample
wins. A missing value from one CCA cannot erase a valid value from another.

## Freshness and availability

Tigo Basic samples normally use a 15-minute grid and arrive roughly 10–20
minutes late. Fetch time is not sample time.

- During daylight, a sample older than 45 minutes is stale.
- Stale module power becomes unavailable so automations cannot mistake it for a
  live measurement.
- Period energy totals remain available when their Tigo response is valid; an
  old power sample does not fabricate a reset or zero energy value.
- Overnight, a lack of new solar samples is expected and does not by itself set
  the stale-data problem.
- Cloud connectivity describes request success independently of freshness.

When a refresh fails, the coordinator may retain its last accepted snapshot for
context, but Home Assistant availability and status entities communicate that
the snapshot is not current. Source age and daylight staleness continue to
advance during the outage rather than freezing at the last successful fetch.
Recovery occurs on the next fully validated response without requiring a
restart.

## Topology changes

Topology is cached for up to 24 hours. On refresh:

- new modules acquire devices/entities using their object IDs;
- reordered modules retain their identities;
- changed Tigo labels and hierarchy attributes update while user-assigned
  Home Assistant names remain authoritative;
- removed modules are no longer updated but are not silently deleted from the
  Home Assistant registry;
- a temporary missing module value does not change topology.

Registry cleanup remains a deliberate user action so transient Tigo payload
problems cannot destroy names, history, or automation references.

## Recorder and Energy behavior

Recorder begins recording integration states when the integration is installed.
Version 0.1 does not import Tigo's calendar/history response into past Home
Assistant statistics.

The lifetime entity is technically suitable for long-term energy statistics,
but users must not add it as a second solar source when another integration
already measures the same physical production. The supplied dashboard reads
only Tigo entities and never edits the global Home Assistant Energy
configuration.

## Sanitized diagnostics

Diagnostics may include:

- integration and Home Assistant versions;
- system ID only in redacted or non-account-correlating form;
- account tier/capability flags;
- counts of inverters, strings, and modules;
- configured cadence and cache age;
- request outcome categories and freshness age;
- which optional response fields were present.

Diagnostics must exclude credentials, tokens, cookies, authorization/request
headers, email address, site name/address/coordinates, CCA identifiers, module
serial numbers, and raw endpoint payloads.
