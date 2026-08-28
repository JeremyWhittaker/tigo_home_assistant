# Contributing

Thank you for helping improve Tigo Energy Cloud. Contributions should preserve
the project's read-only, privacy-conscious scope and remain useful across Tigo
systems rather than encoding one installation's entity IDs or topology.

## Before opening a change

- Search existing issues and release notes.
- For a bug, collect the Home Assistant and integration versions, a sanitized
  diagnostics download, and exact observed/expected behavior.
- For an endpoint change, describe the payload shape with all account, site,
  gateway, module, token, and location data replaced—not merely blurred.
- Discuss large entity-model, compatibility, or dashboard changes in an issue
  before implementation.

Never attach credentials, bearer/refresh tokens, authorization or cookie
headers, CCA request identifiers, site coordinates/addresses, module serial
numbers, raw `.storage` files, dashboard backups, or an unreviewed endpoint
capture.

## Development setup

The Python test configuration in `pyproject.toml` is authoritative. A typical
local environment is:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
ruff check .
pytest
```

Dashboard tooling requires Node.js 22 or newer and has no runtime npm package
dependencies:

```bash
npm run check
```

Do not run live-account tests in CI. Unit tests must use intentionally synthetic
fixtures and mocked HTTP responses. A local `.env` is ignored as a precaution,
but the integration itself and dashboard tooling do not load Tigo credentials
from it.

## Design rules

- Keep mobile endpoint paths, headers, authentication, and payload parsing
  inside the asynchronous client boundary.
- Use Home Assistant's shared HTTP session and coordinator/entity patterns.
- Preserve source timestamps and distinguish connection health from freshness.
- Treat a missing Tigo value as unavailable, never as zero.
- Map module arrays through the server-provided object-ID ordering.
- Keep stable config-entry, device, and entity unique IDs; add migrations before
  changing persisted identity.
- Never add equipment controls, RS-485/local CCA transport, HTML scraping,
  telemetry, or an intermediary service under an unrelated change.
- Keep the supplied dashboard native-card-only and discover entities through
  registries rather than generated entity IDs.
- Do not modify global Home Assistant Energy preferences from the integration or
  dashboard tool.

Code or behavior informed by another project must be license-compatible and
attributed where required. Do not copy code from a repository without an
explicit compatible license.

## Tests expected with changes

Relevant changes should test both the successful behavior and the important
failure path. Examples include:

- authentication, one-time 401 retry, throttling, ETag/304, and malformed data;
- module ordering, independent last-value selection, missing daily energy, and
  Wh-to-kWh conversion;
- config flow, reauthentication, duplicate systems, and options bounds;
- stable entity/device identity, units, state classes, and availability;
- stale daylight versus expected nighttime inactivity;
- dashboard discovery, generated configuration, transaction rollback, checksum,
  restore drift protection, and native-card validation.

Run `ruff check .`, `pytest`, and `npm run check` before submitting. GitHub also
runs hassfest and HACS validation.

## Pull requests

Keep each pull request focused. Update user documentation, fixtures, and release
notes when behavior changes. Complete the pull-request checklist and explain any
test or visual-QA step that could not be run.

By submitting a contribution, you agree that it is licensed under the Apache
License 2.0 in this repository.
