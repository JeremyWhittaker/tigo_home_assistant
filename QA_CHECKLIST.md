# Tigo Energy Cloud v0.1.1 quality checklist

This is the implementation and release acceptance record for v0.1.1. Update
every row before release with one of `implemented`, `blocked`, `deferred`, or
`not applicable`, plus specific evidence (test name, command output, path,
commit, live observation, or external blocker). `pending` is the initial state,
not an acceptable release state.

## Scope and safety

| ID | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| S-01 | Integration is unofficial, read-only, and never presented as endorsed or supported by Tigo. | implemented | README disclaimer; `api.py` exposes retrieval operations only. |
| S-02 | Runtime uses mobile JSON endpoints only; no paid official API dependency. | implemented | `api.py` isolates the mobile JSON boundary; the manifest has no runtime requirements. |
| S-03 | No RS-485, local CCA access, HTML scraping, control commands, hosted proxy, or telemetry. | implemented | README scope and `docs/ARCHITECTURE.md`; source scan contains none of these paths. |
| S-04 | Repository is public and contains an Apache-2.0 license plus accurate attribution. | implemented | Public GitHub repository, `LICENSE`, and `NOTICE`. |
| S-05 | `.env`, credentials, tokens, CCA identifiers, raw payloads, private backups, and credential screenshots are absent from Git history and staged changes. | implemented | `.gitignore`, staged-diff secret scan, release-archive inspection, and synthetic-only fixtures. |
| S-06 | No unlicensed repository code was copied; reused code is license-compatible and attributed. | implemented | Independent implementation recorded in `NOTICE`; no third-party source is vendored. |

## Cloud client and normalization

| ID | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| A-01 | Async client uses Home Assistant's shared HTTP session and no third-party runtime package. | implemented | `coordinator.py` calls `async_get_clientsession`; `manifest.json` has no requirements. |
| A-02 | Login, account system discovery, layout/equipment topology, homepage totals, module power/energy, and daily peak are decoded through one client boundary. | implemented | Typed surface in `api.py`; parser/client coverage in `tests/test_api.py` and `tests/test_models.py`. |
| A-03 | Bearer token remains in memory and a request retries authentication at most once after HTTP 401. | implemented | `test_401_relogs_in_and_retries_original_request_exactly_once` and `test_second_401_surfaces_without_a_third_login_or_request`. |
| A-04 | Conditional requests persist/reuse ETags correctly and HTTP 304 retains the last validated value. | implemented | `test_etag_304_returns_defensive_cached_copy`. |
| A-05 | HTTP 429/503 `Retry-After`, timeouts, and transient failures use bounded backoff without a request loop. | implemented | Retry-After API tests plus `test_retry_after_extends_interval_and_surfaces_update_failure`. |
| A-06 | Structural/malformed responses fail safely without logging raw payloads or secrets. | implemented | Sanitized authentication/network-error tests and typed `TigoDataError` validation. |
| A-07 | Module power maps values through the returned `order` object-ID array. | implemented | `test_panel_power_uses_order_and_scans_each_module_independently`. |
| A-08 | Latest valid power is selected independently per module; final/future `"-"` rows do not erase valid values. | implemented | Independent-sample and unmatched-order model tests. |
| A-09 | Omitted module daily energy remains unavailable and is not converted to zero. | implemented | `test_panel_energy_converts_wh_and_keeps_missing_module_unavailable` and live missing-value check. |
| A-10 | Wh-to-kWh conversion and Tigo source timestamps are applied exactly once. | implemented | Unit normalization tests; live 21.47525 kWh system total versus 21.477 kWh rounded module sum. |
| A-11 | Topology loads at setup, caches for 24 hours, and preserves identity across reordering/change. | implemented | `test_topology_refreshes_after_twenty_four_hours` and stable-identity entity tests. |
| A-12 | Defaults are 5-minute daylight polling, 30-minute night polling, and a 45-minute stale threshold; UI bounds are 2–60 minutes for polling and 15 minutes–6 hours for stale data. | implemented | Coordinator cadence tests and options-flow boundary tests. |

## Home Assistant integration

| ID | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| H-01 | Domain is `tigo_energy`; manifest, translations, HACS metadata, and branding/name agree. | implemented | Manifest, strings, English translation, and `hacs.json` inspection. |
| H-02 | UI flow validates credentials, discovers systems, handles one/multiple systems, and prevents a duplicate system ID. | implemented | Single/multiple/duplicate config-flow tests. |
| H-03 | Invalid authentication, cannot-connect, unknown-error, and reauthentication paths are implemented and tested. | implemented | Credential-error and both reauthentication tests in `tests/test_config_flow.py`. |
| H-04 | Options flow validates polling bounds without requiring YAML configuration. | implemented | Valid and minimum-bound options-flow tests; config flow is UI-only. |
| H-05 | One config entry represents one system and uses the Tigo system ID as its stable unique ID. | implemented | `test_single_system_creates_entry` and live loaded entry. |
| H-06 | Coordinator preserves last valid context while exposing fetch health and source freshness independently. | implemented | Update-failure entity test and coordinator failure/recovery coverage. |
| H-07 | Daylight data older than 45 minutes is marked stale; expected nighttime inactivity does not raise a false stale problem. | implemented | Old-daylight and night-slow-interval tests; live delayed source flagged stale. |
| H-08 | System device exposes current/peak power; day/week/month/year/lifetime energy; reporting count; source time/age; cloud connectivity; and stale status. | implemented | Entity metadata test and live 16-entity system device. |
| H-09 | Every discovered module device exposes enabled power and daily-energy entities. | implemented | Module hierarchy test and live 44 devices/88 module entities. |
| H-10 | Basic-tier fields observed empty (voltage/current/RSSI/reclaimed power) are not exposed as misleading entities. | implemented | Entity specification contains only validated power and daily-energy module metrics. |
| H-11 | Power/energy units, device classes, state classes, precision, and availability match `docs/DATA_MODEL.md`. | implemented | Entity state-metadata tests and post-restart live-state inspection. |
| H-12 | Device/entity unique IDs remain stable when names or module ordering change. | implemented | Topology-order model test, stable-entity test, and dashboard renamed-entity discovery test. |
| H-13 | Diagnostics redact credentials, tokens, headers, account/location data, CCA IDs, serials, and raw payloads. | implemented | Allowlisted summary in `diagnostics.py`; no credential/payload object is returned. |
| H-14 | Integration can unload/reload cleanly and recovers after transient API failure without restarting Home Assistant. | implemented | Coordinator recovery tests and successful HACS upgrade/restart/reload on Home Assistant 2026.8.3. |
| H-15 | No automatic historical Recorder backfill is performed in v0.1.1. | implemented | No Recorder writes in source; README and dashboard guide document the expected initial history gap. |

## Dashboard and deployment tooling

| ID | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| D-01 | Dashboard is an explicit, optional deployment; integration setup never mutates Lovelace. | implemented | Separate Node deployment command and dashboard guide; Python setup has no Lovelace calls. |
| D-02 | Dashboard title/slug/path are `Tigo Energy`, `tigo-energy`, and `/tigo-energy/overview`. | implemented | `dashboardMetadata` and live direct-route check. |
| D-03 | Overview, Energy, Modules, and System views are complete and responsive. | implemented | 16-case desktop/mobile, light/dark visual report and manual full-scroll review. |
| D-04 | Generated configuration uses built-in Home Assistant cards only, with no frontend dependency or control action. | implemented | Native/read-only validation test and generated-config preflight. |
| D-05 | Entity/device registry discovery avoids hard-coded generated entity IDs and supports variable module counts/topology. | implemented | Renamed-entity and 1/7/44-module generation tests. |
| D-06 | `preflight` validates authentication, API support, config entry, registries, and required entities without mutation. | implemented | Live `preflight-ok`: Home Assistant 2026.8.3, 44 modules, 104 entities. |
| D-07 | `plan` is read-only and presents the proposed Lovelace change. | implemented | Live post-deploy `plan-ok action=unchanged config_changed=false metadata_changed=false`. |
| D-08 | `deploy` makes a checksummed mode-0600 backup in a unique directory before the first Home Assistant write. | implemented | Backup-mode test and live private backup under `/tmp/tigo-energy-dashboard-UYYlcY/`. |
| D-09 | Deployment validates generated config, writes transactionally, validates read-back, and rolls back on failure. | implemented | Create/update/rollback and round-trip restoration tests; live deployment round trip succeeded. |
| D-10 | `restore` verifies checksum and refuses drift unless the user explicitly passes `--force`. | implemented | Backup/checksum/drift and prior-dashboard restore tests. |
| D-11 | Dashboard clearly shows Tigo source age/staleness and partial/unavailable module reporting. | implemented | Live stale banner, reporting count, source age, and unavailable module tiles reviewed. |
| D-12 | Tooling never changes global Home Assistant Energy preferences, preventing accidental EG4/Tigo double counting. | implemented | No Energy API calls; generated dashboard copy and dashboard guide state the boundary. |
| D-13 | Dashboard/token/backup handling follows `SECURITY.md`; no secrets appear in output or generated Lovelace. | implemented | Environment-only token, safe summaries, private backup modes, and staged secret scan. |

## Automated verification

| ID | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| T-01 | Parser tests cover real-shape synthetic topology, ordering, independent samples, placeholders, omitted energy, units, totals, and timestamps. | implemented | 11 focused model tests in `tests/test_models.py`. |
| T-02 | Client tests cover login, expiry/401, 304, 429/503, timeout, malformed responses, invalid credentials, and secret-free errors. | implemented | 13 focused client tests in `tests/test_api.py`. |
| T-03 | Coordinator tests cover topology caching, daylight/night cadence, stale thresholds, partial modules, request failure, and recovery. | implemented | Nine tests in `tests/test_coordinator.py`. |
| T-04 | Config-flow tests cover single/multiple systems, duplicate entry, auth/connect/unknown errors, reauth, and option bounds. | implemented | Eight tests in `tests/test_config_flow.py`. |
| T-05 | Entity tests cover unique IDs, hierarchy, metadata, state class, source timestamp, and availability. | implemented | Four Home Assistant framework tests in `tests/test_entities.py`. |
| T-06 | Dashboard tests cover discovery, optional/missing entities, generation, native-card validation, backup/checksum, rollback, restore, and drift refusal. | implemented | 14 Node tests in `test/dashboard.test.mjs`. |
| T-07 | `ruff check .` passes from a clean checkout. | implemented | Local Ruff check and format check pass; CI repeats both. |
| T-08 | Full `pytest` suite passes without a live Tigo account or network dependency. | implemented | `pytest -q`: 46 passed using synthetic fixtures/fakes. |
| T-09 | `npm run check` passes with Node.js 22. | implemented | Local check: 14 passed; package requires Node 22 or newer. |
| T-10 | Home Assistant hassfest and HACS action validation pass. | implemented | GitHub `validate.yml` hassfest and HACS jobs pass on `main`. |

## Documentation and release

| ID | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| R-01 | README covers limitations/delay, installation, setup/options, entities, dashboard, security/privacy, troubleshooting, double-count warning, and project status. | implemented | README section review against this checklist. |
| R-02 | Architecture, data model, dashboard/recovery, security, and contribution documents match shipped behavior. | implemented | `docs/`, `SECURITY.md`, and `CONTRIBUTING.md` reviewed after live QA. |
| R-03 | HACS custom-repository installation and manual installation work from a clean tagged checkout. | implemented | Root-layout 14-file archive inspection and clean HACS install/upgrade using tagged release assets. |
| R-04 | GitHub Actions cover Python lint/tests, dashboard tests, hassfest, HACS validation, and release tag/version hygiene. | implemented | `ci.yml`, `validate.yml`, and `release.yml`. |
| R-05 | Manifest, package, HACS minimum Home Assistant, docs, tag, and GitHub release agree on v0.1.1 compatibility/version. | implemented | Version parity check reports `0.1.1`; release workflow rejects tag mismatch. |
| R-06 | Task-owned changes are committed coherently and `main` plus tag `v0.1.1` are pushed to the public origin. | implemented | Public `main`, annotated `v0.1.1` tag, and GitHub release URL recorded in the final handoff. |

## Live Home Assistant and visual QA

| ID | Requirement | Status | Evidence |
| --- | --- | --- | --- |
| L-01 | HACS installs the public repository and Home Assistant 2026.8.3 restarts with the integration loaded. | implemented | HACS tagged-archive install and post-restart API probe; entity version is `0.1.1`. |
| L-02 | The configured live Tigo system loads and successfully refreshes without secrets in logs. | implemented | Live refresh succeeded; post-restart Tigo runtime log has zero warnings/errors. |
| L-03 | Live topology shows 44 module devices, each with power/daily-energy entities; source-missing energy remains unavailable. | implemented | Registry: one system device, 44 module devices, 16 system plus 88 module entities, 104 unique IDs. |
| L-04 | Live system totals, reporting count, source timestamp/age, connectivity, and stale status agree with sanitized Tigo observations. | implemented | Daily total agrees with summed modules; 43/44 reporting, connected on, delayed source correctly stale. |
| L-05 | Dashboard deploys at `/tigo-energy/overview`, produces a private backup, and round-trip validation succeeds. | implemented | Live update backup, deploy read-back, and subsequent no-op plan all succeeded. |
| L-06 | Overview and Modules render at 1440×1000 and 390×844 in light and dark themes. | implemented | `/tmp/tigo-energy-final-visual-qa-5wktJP/report.json` plus manual segment inspection. |
| L-07 | Energy and System views, full-page scroll, navigation, stale/unavailable states, and direct routes render without Lovelace errors. | implemented | All 16 capture cases and 58 scroll segments passed with zero actionable errors. |
| L-08 | Browser console is clean; forms/links work; no `prototype`, `rebuild`, staging notes, placeholders, or credential content is publicly visible. | implemented | Manual copy/navigation review; five filtered messages are pre-existing external camera-card/source-map errors. |
| L-09 | No accidental noindex/canonical/robots/public-site behavior is introduced; Home Assistant remains governed by its existing access controls. | not applicable | Native authenticated Home Assistant dashboard; no public site, canonical, robots, or noindex files are created. |
