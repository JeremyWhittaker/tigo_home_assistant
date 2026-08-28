# Tigo Energy Cloud v0.1 quality checklist

This is the implementation and release acceptance record for v0.1.0. Update
every row before release with one of `implemented`, `blocked`, `deferred`, or
`not applicable`, plus specific evidence (test name, command output, path,
commit, live observation, or external blocker). `pending` is the initial state,
not an acceptable release state.

## Scope and safety

| ID | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| S-01 | Integration is unofficial, read-only, and never presented as endorsed or supported by Tigo. | pending | — |
| S-02 | Runtime uses mobile JSON endpoints only; no paid official API dependency. | pending | — |
| S-03 | No RS-485, local CCA access, HTML scraping, control commands, hosted proxy, or telemetry. | pending | — |
| S-04 | Repository is public and contains an Apache-2.0 license plus accurate attribution. | pending | — |
| S-05 | `.env`, credentials, tokens, CCA identifiers, raw payloads, private backups, and credential screenshots are absent from Git history and staged changes. | pending | — |
| S-06 | No unlicensed repository code was copied; reused code is license-compatible and attributed. | pending | — |

## Cloud client and normalization

| ID | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| A-01 | Async client uses Home Assistant's shared HTTP session and no third-party runtime package. | pending | — |
| A-02 | Login, account system discovery, layout/equipment topology, homepage totals, module power/energy, and daily peak are decoded through one client boundary. | pending | — |
| A-03 | Bearer token remains in memory and a request retries authentication at most once after HTTP 401. | pending | — |
| A-04 | Conditional requests persist/reuse ETags correctly and HTTP 304 retains the last validated value. | pending | — |
| A-05 | HTTP 429/503 `Retry-After`, timeouts, and transient failures use bounded backoff without a request loop. | pending | — |
| A-06 | Structural/malformed responses fail safely without logging raw payloads or secrets. | pending | — |
| A-07 | Module power maps values through the returned `order` object-ID array. | pending | — |
| A-08 | Latest valid power is selected independently per module; final/future `"-"` rows do not erase valid values. | pending | — |
| A-09 | Omitted module daily energy remains unavailable and is not converted to zero. | pending | — |
| A-10 | Wh-to-kWh conversion and Tigo source timestamps are applied exactly once. | pending | — |
| A-11 | Topology loads at setup, caches for 24 hours, and preserves identity across reordering/change. | pending | — |
| A-12 | Defaults are 5-minute daylight polling, 30-minute night polling, and a 45-minute stale threshold; UI bounds are 2–60 minutes for polling and 15 minutes–6 hours for stale data. | pending | — |

## Home Assistant integration

| ID | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| H-01 | Domain is `tigo_energy`; manifest, translations, HACS metadata, and branding/name agree. | pending | — |
| H-02 | UI flow validates credentials, discovers systems, handles one/multiple systems, and prevents a duplicate system ID. | pending | — |
| H-03 | Invalid authentication, cannot-connect, unknown-error, and reauthentication paths are implemented and tested. | pending | — |
| H-04 | Options flow validates polling bounds without requiring YAML configuration. | pending | — |
| H-05 | One config entry represents one system and uses the Tigo system ID as its stable unique ID. | pending | — |
| H-06 | Coordinator preserves last valid context while exposing fetch health and source freshness independently. | pending | — |
| H-07 | Daylight data older than 45 minutes is marked stale; expected nighttime inactivity does not raise a false stale problem. | pending | — |
| H-08 | System device exposes current/peak power; day/week/month/year/lifetime energy; reporting count; source time/age; cloud connectivity; and stale status. | pending | — |
| H-09 | Every discovered module device exposes enabled power and daily-energy entities. | pending | — |
| H-10 | Basic-tier fields observed empty (voltage/current/RSSI/reclaimed power) are not exposed as misleading entities. | pending | — |
| H-11 | Power/energy units, device classes, state classes, precision, and availability match `docs/DATA_MODEL.md`. | pending | — |
| H-12 | Device/entity unique IDs remain stable when names or module ordering change. | pending | — |
| H-13 | Diagnostics redact credentials, tokens, headers, account/location data, CCA IDs, serials, and raw payloads. | pending | — |
| H-14 | Integration can unload/reload cleanly and recovers after transient API failure without restarting Home Assistant. | pending | — |
| H-15 | No automatic historical Recorder backfill is performed in v0.1.0. | pending | — |

## Dashboard and deployment tooling

| ID | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| D-01 | Dashboard is an explicit, optional deployment; integration setup never mutates Lovelace. | pending | — |
| D-02 | Dashboard title/slug/path are `Tigo Energy`, `tigo-energy`, and `/tigo-energy/overview`. | pending | — |
| D-03 | Overview, Energy, Modules, and System views are complete and responsive. | pending | — |
| D-04 | Generated configuration uses built-in Home Assistant cards only, with no frontend dependency or control action. | pending | — |
| D-05 | Entity/device registry discovery avoids hard-coded generated entity IDs and supports variable module counts/topology. | pending | — |
| D-06 | `preflight` validates authentication, API support, config entry, registries, and required entities without mutation. | pending | — |
| D-07 | `plan` is read-only and presents the proposed Lovelace change. | pending | — |
| D-08 | `deploy` makes a checksummed mode-0600 backup in a unique directory before the first Home Assistant write. | pending | — |
| D-09 | Deployment validates generated config, writes transactionally, validates read-back, and rolls back on failure. | pending | — |
| D-10 | `restore` verifies checksum and refuses drift unless the user explicitly passes `--force`. | pending | — |
| D-11 | Dashboard clearly shows Tigo source age/staleness and partial/unavailable module reporting. | pending | — |
| D-12 | Tooling never changes global Home Assistant Energy preferences, preventing accidental EG4/Tigo double counting. | pending | — |
| D-13 | Dashboard/token/backup handling follows `SECURITY.md`; no secrets appear in output or generated Lovelace. | pending | — |

## Automated verification

| ID | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| T-01 | Parser tests cover real-shape synthetic topology, ordering, independent samples, placeholders, omitted energy, units, totals, and timestamps. | pending | — |
| T-02 | Client tests cover login, expiry/401, 304, 429/503, timeout, malformed responses, invalid credentials, and secret-free errors. | pending | — |
| T-03 | Coordinator tests cover topology caching, daylight/night cadence, stale thresholds, partial modules, request failure, and recovery. | pending | — |
| T-04 | Config-flow tests cover single/multiple systems, duplicate entry, auth/connect/unknown errors, reauth, and option bounds. | pending | — |
| T-05 | Entity tests cover unique IDs, hierarchy, metadata, state class, source timestamp, and availability. | pending | — |
| T-06 | Dashboard tests cover discovery, optional/missing entities, generation, native-card validation, backup/checksum, rollback, restore, and drift refusal. | pending | — |
| T-07 | `ruff check .` passes from a clean checkout. | pending | — |
| T-08 | Full `pytest` suite passes without a live Tigo account or network dependency. | pending | — |
| T-09 | `npm run check` passes with Node.js 22. | pending | — |
| T-10 | Home Assistant hassfest and HACS action validation pass. | pending | — |

## Documentation and release

| ID | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| R-01 | README covers limitations/delay, installation, setup/options, entities, dashboard, security/privacy, troubleshooting, double-count warning, and project status. | pending | — |
| R-02 | Architecture, data model, dashboard/recovery, security, and contribution documents match shipped behavior. | pending | — |
| R-03 | HACS custom-repository installation and manual installation work from a clean tagged checkout. | pending | — |
| R-04 | GitHub Actions cover Python lint/tests, dashboard tests, hassfest, HACS validation, and release tag/version hygiene. | pending | — |
| R-05 | Manifest, package, HACS minimum Home Assistant, docs, tag, and GitHub release agree on v0.1.0 compatibility/version. | pending | — |
| R-06 | Task-owned changes are committed coherently and `main` plus tag `v0.1.0` are pushed to the public origin. | pending | — |

## Live Home Assistant and visual QA

| ID | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| L-01 | HACS installs the public repository and Home Assistant 2026.8.3 restarts with the integration loaded. | pending | — |
| L-02 | The configured live Tigo system loads and successfully refreshes without secrets in logs. | pending | — |
| L-03 | Live topology shows 44 module devices, each with power/daily-energy entities; source-missing energy remains unavailable. | pending | — |
| L-04 | Live system totals, reporting count, source timestamp/age, connectivity, and stale status agree with sanitized Tigo observations. | pending | — |
| L-05 | Dashboard deploys at `/tigo-energy/overview`, produces a private backup, and round-trip validation succeeds. | pending | — |
| L-06 | Overview and Modules render at 1440×1000 and 390×844 in light and dark themes. | pending | — |
| L-07 | Energy and System views, full-page scroll, navigation, stale/unavailable states, and direct routes render without Lovelace errors. | pending | — |
| L-08 | Browser console is clean; forms/links work; no `prototype`, `rebuild`, staging notes, placeholders, or credential content is publicly visible. | pending | — |
| L-09 | No accidental noindex/canonical/robots/public-site behavior is introduced; Home Assistant remains governed by its existing access controls. | pending | — |
