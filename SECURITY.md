# Security policy

Tigo Energy Cloud handles credentials for a third-party cloud account. Security
reports are taken seriously even though this is an unofficial, volunteer
project.

## Supported versions

Only the newest published release receives security fixes. During the `0.2.x`
development series, users should update to the latest patch before reporting a
problem already fixed there.

| Version | Supported |
| --- | --- |
| Latest release | Yes |
| Older releases and unreleased forks | No |

## Reporting a vulnerability

Do not disclose a suspected vulnerability, credential, token, private endpoint
payload, or site information in a public issue.

Use **Report a vulnerability** in the repository's **Security** tab to open a
private GitHub security advisory. Include:

- the affected version and Home Assistant version;
- a concise impact statement;
- minimal reproduction steps;
- whether credentials, tokens, private data, or account actions are involved;
- a proposed mitigation, if known.

If private vulnerability reporting is unavailable, open a public issue that
contains only a request for a private contact channel. Do not include technical
details until a private channel is established.

The maintainer will acknowledge a complete private report when available,
validate it, coordinate a fix/release, and credit the reporter unless anonymity
is requested. This volunteer project cannot promise a fixed response-time SLA.

## Credential and token handling

- Home Assistant stores the Tigo username and password in local config-entry
  storage so the integration can establish a new session after restart.
- Home Assistant storage is not an encrypted secrets vault. Protect the host,
  `.storage` directory, backups, support bundles, and administrator accounts.
- Bearer and refresh tokens remain in process memory and are never intentionally
  persisted by this integration.
- Credentials, tokens, cookies, authorization headers, CCA request identifiers,
  account email, location details, serial numbers, and raw response payloads are
  excluded from diagnostics and normal logs.
- Dashboard deployment requires a Home Assistant administrator token only for
  the deployment process. It is not stored in the dashboard or integration.
- Rotate both Tigo credentials and Home Assistant tokens immediately after a
  suspected disclosure.

Never commit `.env`, Home Assistant dashboard backups, debug logs, diagnostics
you have not reviewed, or copied `.storage` files. Repository ignore rules are a
last line of defense, not a substitute for reviewing `git diff --staged`.

## Privacy model

The integration sends requests directly from Home Assistant to Tigo's cloud
service. The project does not operate a proxy, analytics service, or telemetry
collector. Normal Home Assistant state/history handling still applies: entity
names, system production, module output, timestamps, and diagnostic states may
be stored in Recorder and included in Home Assistant backups.

The optional dashboard generator reads Home Assistant registry/state data and
writes Lovelace configuration through authenticated Home Assistant APIs. Its
mode-`0600` backup can contain household entity metadata; keep or destroy that
backup according to local policy.

## Integration security boundaries

The integration is read-only and does not expose equipment controls. It does not
use local CCA access, RS-485, shell commands, browser credential capture, or HTML
scraping. A compromised Tigo account or upstream service remains outside this
project's control.

Because private mobile endpoints are not a documented compatibility contract,
unexpected payloads are validated and rejected rather than interpreted as
commands or written to disk. HTTP error messages and payload excerpts must be
sanitized before reaching logs.

## Out of scope

The following are not vulnerabilities in this repository by themselves:

- normal 10–20-minute Tigo cloud delay or longer upstream data lag;
- missing/incorrect source measurements supplied by Tigo;
- availability of private endpoints already used by Tigo's official client;
- Home Assistant host compromise or an administrator intentionally reading
  config-entry storage;
- unsupported modifications that add control behavior or bypass TLS.

Reports showing that this integration leaks secrets, accepts unsafe payloads,
crosses config-entry boundaries, or permits unintended Home Assistant writes are
in scope.
