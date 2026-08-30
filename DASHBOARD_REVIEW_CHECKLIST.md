# Dashboard review and measurement-audit checklist

This checklist tracks the August 2026 Tigo dashboard review requested by Jeremy.
Every material request remains open until it has implementation and validation
evidence. Values from Tigo and EG4 are considered comparable only after their
units, physical measurement points, and source timestamps are verified.

| ID | Review item | Status | Evidence |
| --- | --- | --- | --- |
| UX-01 | Make the Overview page concise, professional, and useful at a glance on desktop and mobile. | pending | — |
| UX-02 | Move explanatory and diagnostic copy off Overview into a dedicated diagnostics view. | pending | — |
| UX-03 | Keep source freshness visible without allowing cloud-health details to dominate normal production monitoring. | pending | — |
| UX-04 | Preserve useful Energy and module-level views while improving hierarchy, labels, and scanability. | pending | — |
| UX-05 | Use only native, read-only Home Assistant cards and retain transactional backup/restore behavior. | pending | — |
| MEAS-01 | Identify the exact Tigo and EG4 power/energy entities, units, state classes, and physical measurement points. | pending | — |
| MEAS-02 | Compare Tigo and EG4 using source-time-aligned samples so the Tigo cloud delay is not mistaken for a calculation error. | pending | — |
| MEAS-03 | Verify that Tigo system power agrees with the valid module-power sum within the documented source behavior. | pending | — |
| MEAS-04 | Explain whether the apparent 6 kW value is a chart scale, observed daily peak, configured cap, or equipment limit. | pending | — |
| MEAS-05 | Distinguish EG4 18kPV model/input capability, inverter AC rating, installed array nameplate, and observed production. | pending | — |
| SAFE-01 | Do not combine Tigo and EG4 energy totals or change Home Assistant Energy sources, avoiding double counting. | pending | — |
| QA-01 | Add focused generator/discovery tests and pass the complete Python and Node test suites. | pending | — |
| QA-02 | Deploy through the documented transaction, validate read-back, and inspect every view in light/dark desktop/mobile rendering. | pending | — |
| QA-03 | Verify public copy, navigation, direct routes, console/Lovelace errors, and secret-free generated configuration. | pending | — |
| DOC-01 | Update README and dashboard/data-model documentation with the final design and measurement guidance. | pending | — |
| REL-01 | Commit only task-owned changes, push them publicly, and publish/install a coherent release if versioned code changes. | pending | — |
