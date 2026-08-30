# Dashboard review and measurement-audit checklist

This checklist tracks the August 2026 Tigo dashboard review requested by Jeremy.
Every material request remains open until it has implementation and validation
evidence. Values from Tigo and EG4 are considered comparable only after their
units, physical measurement points, and source timestamps are verified.

| ID | Review item | Status | Evidence |
| --- | --- | --- | --- |
| UX-01 | Make the Overview page concise, professional, and useful at a glance on desktop and mobile. | implemented | `src/dashboard.mjs`; final 1440×1000 and 390×844 light/dark captures in `/tmp/tigo-energy-v0.2.1-final-visual-qa` |
| UX-02 | Move explanatory and diagnostic copy off Overview into a dedicated diagnostics view. | implemented | `src/dashboard.mjs`: Overview has exception-only notices; `/system` is titled Diagnostics |
| UX-03 | Keep source freshness visible without allowing cloud-health details to dominate normal production monitoring. | implemented | Overview source-age badge plus conditional connection/stale cards; full status in Diagnostics |
| UX-04 | Preserve useful Energy and module-level views while improving hierarchy, labels, and scanability. | implemented | Compact two-list module sections, four live topology groups, daily-energy Recorder charts |
| UX-05 | Use only native, read-only Home Assistant cards and retain transactional backup/restore behavior. | implemented | `npm run check`; deployer rejects custom/control cards; live transactional updates, backups, and read-backs passed |
| UX-06 | Keep a globally stale cloud interval from flooding Diagnostics with one unavailable row per module while retaining genuine module exceptions. | implemented | `src/dashboard.mjs`; stale power suppression test; final Diagnostics shows only the genuine C4 daily-energy gap |
| MEAS-01 | Identify the exact Tigo and EG4 power/energy entities, units, state classes, and physical measurement points. | implemented | `/tmp/jeremy-foreman-supervisor/tigo-dashboard-review-20260830/measurement-audit.md`; strict metadata contract in `src/discovery.mjs` |
| MEAS-02 | Compare Tigo and EG4 using source-time-aligned samples so the Tigo cloud delay is not mistaken for a calculation error. | implemented | Matched-history audit and Compare view cadence warning; completed-day comparison documented |
| MEAS-03 | Verify that Tigo system power agrees with the valid module-power sum within the documented source behavior. | implemented | Live audit: system 5,682 W versus module sum 5,678 W (0.07% difference) |
| MEAS-04 | Explain whether the apparent 6 kW value is a chart scale, observed daily peak, configured cap, or equipment limit. | implemented | Diagnostics capacity guidance and README measurement table; observed peak about 5.7 kW and graph auto-scale verified |
| MEAS-05 | Distinguish EG4 18kPV model/input capability, inverter AC rating, installed array nameplate, and observed production. | implemented | Tigo build configuration verified 44 × 400 W = 17.6 kW DC; official specifications linked in README |
| SAFE-01 | Do not combine Tigo and EG4 energy totals or change Home Assistant Energy sources, avoiding double counting. | implemented | Read-only validation, fail-closed matching, independent chart series, and Diagnostics warning |
| QA-01 | Add focused generator/discovery tests and pass the complete Python and Node test suites. | implemented | Final v0.2.1 run: 66 Python tests and 16 Node tests passed; Ruff and format checks passed; GitHub CI and Home Assistant validation succeeded for `ad41bf0` |
| QA-02 | Deploy through the documented transaction, validate read-back, and inspect every view in light/dark desktop/mobile rendering. | implemented | Live plan is unchanged after transactional deployment; `/tmp/tigo-energy-v0.2.1-final-visual-qa/report.json` records 20 cases, 58 screenshots, and zero actionable errors; every screenshot received manual review |
| QA-03 | Verify public copy, navigation, direct routes, console/Lovelace errors, and secret-free generated configuration. | implemented | Sidebar and all five direct routes passed; no Lovelace error card or actionable browser error; staged-diff credential scan passed; indexing controls are not applicable to an authenticated Home Assistant panel |
| DOC-01 | Update README and dashboard/data-model documentation with the final design and measurement guidance. | implemented | `README.md`, `docs/DASHBOARD.md`, `docs/DATA_MODEL.md`, and `docs/ARCHITECTURE.md` |
| REL-01 | Commit only task-owned changes, push them publicly, and publish/install a coherent release if versioned code changes. | implemented | Task commits through `ad41bf0` pushed to `main`; public v0.2.1 release and asset published; HACS reports installed/latest v0.2.1; Home Assistant restarted with the Tigo entry loaded and live integration version 0.2.1 |
