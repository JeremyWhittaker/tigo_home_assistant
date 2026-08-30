function monitoringActions() {
  return {
    tap_action: { action: "more-info" },
    hold_action: { action: "none" },
    double_tap_action: { action: "none" },
    icon_tap_action: { action: "more-info" },
  };
}

function heading(text, icon, style = "title") {
  return { type: "heading", heading: text, heading_style: style, icon };
}

function tile(entity, name, icon, columns = 6) {
  if (!entity) return null;
  return {
    type: "tile",
    entity,
    name,
    icon,
    vertical: true,
    state_content: ["state"],
    grid_options: { columns, rows: 2 },
    ...monitoringActions(),
  };
}

function badge(entity, name, icon) {
  if (!entity) return null;
  return { type: "entity", entity, name, icon, ...monitoringActions() };
}

function entityRow(entity, name, icon) {
  return entity ? { entity, name, ...(icon ? { icon } : {}) } : null;
}

function compact(values) {
  return values.filter(Boolean);
}

function markdownInline(value) {
  return String(value ?? "")
    .replace(/[\r\n\t]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replaceAll("{", "&#123;")
    .replaceAll("}", "&#125;")
    .replace(/([\\`*_\[\]<>#|])/g, "\\$1");
}

function sourceStatusSummary(entities, moduleCount) {
  return `{% set missing = ['unknown', 'unavailable', 'none', ''] %}
{% set connected = is_state('${entities.cloudConnected}', 'on') %}
{% set stale = is_state('${entities.dataStale}', 'on') %}
{% set age = states('${entities.cloudDataAge}') %}
{% set reporting = states('${entities.reportingModules}') %}
{% if not connected %}
## 🔴 Tigo Cloud unavailable
The latest fetch failed. Retained totals may be old and module power may be unavailable.
{% elif stale %}
## 🟠 Connected · daylight sample delayed
The API is reachable, but the newest Tigo source sample exceeded the freshness limit.
{% else %}
## 🟢 Connected · sample within freshness window
The latest Tigo source sample is within the configured daylight freshness limit.
{% endif %}
**Reporting:** {{ reporting if reporting | lower not in missing else 'unavailable' }} of ${moduleCount} modules · **Source age:** {{ age if age | lower not in missing else 'unavailable' }} min`;
}

function exceptionCard(conditions, title, body) {
  return {
    type: "conditional",
    conditions,
    card: {
      type: "markdown",
      content: `## ${title}\n${body}`,
    },
    grid_options: { columns: "full" },
  };
}

function unavailableCard(entities, modules = []) {
  return {
    type: "entity-filter",
    state_filter: ["unknown", "unavailable"],
    show_empty: false,
    entities: compact([
      entityRow(entities.currentPower, "Tigo array power", "mdi:solar-power"),
      entityRow(entities.energyToday, "Tigo energy today", "mdi:calendar-today"),
      entityRow(entities.energyLifetime, "Tigo lifetime energy", "mdi:counter"),
      ...modules.flatMap((module) => [
        entityRow(module.entities.power, `${module.panelLabel} power`, "mdi:flash"),
        entityRow(module.entities.energyToday, `${module.panelLabel} energy today`, "mdi:solar-panel"),
      ]),
    ]),
    card: {
      type: "entities",
      title: "Unavailable readings",
      show_header_toggle: false,
      state_color: true,
    },
    grid_options: { columns: "full" },
  };
}

function topologySummary(discovery) {
  const groups = [...new Set(discovery.modules.map((module) => module.groupLabel))];
  const firmware = discovery.system.firmware
    ? `\n- Integration/device version: ${markdownInline(discovery.system.firmware)}`
    : "";
  return `## Tigo system
- Cloud model: ${markdownInline(discovery.system.model)}
- Configured modules: ${discovery.modules.length}
- MPPT / string groups: ${groups.length}${firmware}

${groups.map((group) => `- ${markdownInline(group)}`).join("\n")}`;
}

function capacitySummary(entities) {
  if (!entities.ratedArrayPower) {
    return `## Capacity context
Verified array nameplate is unavailable. The integration needs a valid Tigo build rating for every configured module; an inverter model name is not a substitute for panel nameplate.`;
  }
  const peakLine = entities.peakPowerToday
    ? `{% set peak_raw = states('${entities.peakPowerToday}') %}
{% set peak_unit = state_attr('${entities.peakPowerToday}', 'unit_of_measurement') %}
{% if peak_raw | lower not in missing %}Today's highest Tigo sample: **{% if peak_unit == 'W' %}{{ (peak_raw | float / 1000) | round(1) }} kW{% else %}{{ peak_raw }} {{ peak_unit or '' }}{% endif %}**.{% endif %}`
    : "Today's highest Tigo sample is unavailable in this integration version.";
  return `{% set missing = ['unknown', 'unavailable', 'none', ''] %}
{% set capacity_raw = states('${entities.ratedArrayPower}') %}
{% set capacity_unit = state_attr('${entities.ratedArrayPower}', 'unit_of_measurement') %}
## Capacity context
{% if capacity_raw | lower not in missing %}Configured panel nameplate: **{% if capacity_unit == 'W' %}{{ (capacity_raw | float / 1000) | round(1) }} kW DC{% else %}{{ capacity_raw }} {{ capacity_unit or '' }} DC{% endif %}**.{% else %}Configured panel nameplate is unavailable.{% endif %}
${peakLine}

The observed peak is telemetry—not an equipment limit. Home Assistant auto-scales graph axes, so a rounded axis tick does not define the system's maximum.`;
}

function measurementGuide(hasComparison) {
  const comparison = hasComparison
    ? "The Compare view places Tigo cloud production and EG4 inverter PV-input readings side by side. Their update cadences differ, so the lines are observational and are never added together."
    : "If an inverter integration covers this array, compare its total PV-input power and solar yield—not AC output, load, grid export, or individual inputs added to a total.";
  return `## How to read this dashboard

- Tigo Basic module samples normally use 15-minute granularity and reach the cloud later. **Source age** is authoritative; Home Assistant refresh time is not measurement time.
- **Observed peak today** is the largest Tigo sample captured today, not installed capacity or an inverter rating.
- An inverter model label such as **18kPV** describes PV-input capability. It does not mean the installed array must produce 18 kW, and it is not the inverter's continuous AC rating.
- ${comparison}
- Choose one authoritative solar source in Home Assistant Energy. Adding both Tigo and an inverter source for the same array double-counts production.

Missing values remain unavailable and are never converted to zero or silently scaled.`;
}

function moduleSection(groupLabel, modules) {
  return {
    type: "grid",
    cards: [
      heading(groupLabel, "mdi:solar-panel-large"),
      {
        type: "entities",
        title: "Power · latest sample",
        show_header_toggle: false,
        entities: modules.map((module) => entityRow(
          module.entities.power,
          module.panelLabel,
          "mdi:flash",
        )),
      },
      {
        type: "entities",
        title: "Energy today",
        show_header_toggle: false,
        entities: modules.map((module) => entityRow(
          module.entities.energyToday,
          module.panelLabel,
          "mdi:chart-bell-curve-cumulative",
        )),
      },
    ],
  };
}

function comparisonView(discovery) {
  const tigo = discovery.entities;
  const eg4 = discovery.comparison.entities;
  const source = `${discovery.comparison.provider} ${discovery.comparison.model}`.trim();
  return {
    title: "Compare",
    path: "compare",
    icon: "mdi:compare-horizontal",
    type: "sections",
    max_columns: 2,
    dense_section_placement: true,
    badges: compact([
      badge(tigo.currentPower, "Tigo power", "mdi:solar-panel"),
      badge(eg4.pvPower, `${source} PV`, "mdi:solar-power"),
      badge(tigo.cloudDataAge, "Tigo source age", "mdi:cloud-clock"),
    ]),
    sections: [
      {
        type: "grid",
        cards: compact([
          heading("Latest reported readings", "mdi:compare-horizontal"),
          tile(tigo.currentPower, "Tigo cloud system power", "mdi:solar-panel", 6),
          tile(eg4.pvPower, `${source} PV-input power`, "mdi:solar-power", 6),
          tile(tigo.energyToday, "Tigo production today", "mdi:calendar-today", 6),
          tile(eg4.energyToday, `${source} solar yield today`, "mdi:white-balance-sunny", 6),
          {
            type: "markdown",
            content: "Tigo and EG4 update on different cadences. These values are side by side—not time-synchronized. Completed-day energy is the fairest comparison.",
            grid_options: { columns: "full" },
          },
        ]),
      },
      {
        type: "grid",
        cards: [
          heading("Production trends", "mdi:chart-multiple"),
          {
            type: "history-graph",
            title: "Reported power · 24 hours",
            hours_to_show: 24,
            entities: [
              entityRow(tigo.currentPower, "Tigo cloud · delayed"),
              entityRow(eg4.pvPower, `${source} PV input`),
            ],
            grid_options: { columns: "full", rows: 6 },
          },
          {
            type: "statistics-graph",
            title: "Daily solar energy · 30 days",
            entities: [
              entityRow(tigo.energyLifetime, "Tigo production"),
              entityRow(eg4.energyLifetime, `${source} solar yield`),
            ],
            stat_types: ["change"],
            period: "day",
            days_to_show: 30,
            chart_type: "bar",
            hide_legend: false,
            grid_options: { columns: "full", rows: 7 },
          },
        ],
      },
    ],
  };
}

export function buildDashboard(discovery) {
  if (!discovery?.entities || !Array.isArray(discovery.modules) || discovery.modules.length === 0) {
    throw new TypeError("buildDashboard requires a completed Tigo discovery result with modules");
  }
  const e = discovery.entities;
  const moduleGroups = new Map();
  for (const module of discovery.modules) {
    const group = moduleGroups.get(module.groupLabel) ?? [];
    group.push(module);
    moduleGroups.set(module.groupLabel, group);
  }

  const views = [
    {
      title: "Overview",
      path: "overview",
      icon: "mdi:solar-power",
      type: "sections",
      max_columns: 2,
      dense_section_placement: true,
      badges: compact([
        badge(e.currentPower, "Tigo power", "mdi:solar-power"),
        badge(e.energyToday, "Today", "mdi:calendar-today"),
        badge(e.reportingModules, "Reporting", "mdi:solar-panel"),
        badge(e.cloudDataAge, "Source age", "mdi:cloud-clock"),
      ]),
      sections: [
        {
          type: "grid",
          cards: compact([
            heading("Latest production sample", "mdi:solar-power-variant"),
            exceptionCard(
              [{ condition: "state", entity: e.cloudConnected, state_not: "on" }],
              "🔴 Tigo Cloud connection unavailable",
              "Values below are retained for context and may be old. Open Diagnostics for source timing and connection details.",
            ),
            exceptionCard(
              [
                { condition: "state", entity: e.cloudConnected, state: "on" },
                { condition: "state", entity: e.dataStale, state: "on" },
              ],
              "🟠 Tigo sample delayed",
              "The newest daylight sample exceeded the freshness limit, so module power is unavailable. Open Diagnostics for details.",
            ),
            unavailableCard(e),
            tile(e.currentPower, "Tigo array power", "mdi:solar-power", 6),
            tile(e.peakPowerToday, "Observed peak today", "mdi:chart-line-variant", 6),
            tile(e.energyToday, "Energy today", "mdi:white-balance-sunny", 6),
            tile(e.reportingModules, "Modules reporting", "mdi:solar-panel-large", 6),
          ]),
        },
        {
          type: "grid",
          cards: [
            heading("Production history", "mdi:chart-areaspline"),
            {
              type: "history-graph",
              title: "Tigo power · 24 hours",
              hours_to_show: 24,
              entities: [entityRow(e.currentPower, "Tigo cloud power")],
              grid_options: { columns: "full", rows: 6 },
            },
          ],
        },
      ],
    },
    {
      title: "Energy",
      path: "energy",
      icon: "mdi:chart-bar",
      type: "sections",
      max_columns: 2,
      dense_section_placement: true,
      sections: [
        {
          type: "grid",
          cards: compact([
            heading("Production totals", "mdi:solar-power-variant"),
            tile(e.energyToday, "Today", "mdi:calendar-today", 6),
            tile(e.energyWeek, "This week", "mdi:calendar-week", 6),
            tile(e.energyMonth, "This month", "mdi:calendar-month", 6),
            tile(e.energyYear, "This year", "mdi:calendar", 6),
            tile(e.energyLifetime, "Lifetime", "mdi:counter", 12),
          ]),
        },
        {
          type: "grid",
          cards: compact([
            heading("Production history", "mdi:chart-bar"),
            {
              type: "statistics-graph",
              title: "Daily production · 30 days",
              entities: [entityRow(e.energyLifetime, "Tigo production")],
              stat_types: ["change"],
              period: "day",
              days_to_show: 30,
              chart_type: "bar",
              hide_legend: false,
              grid_options: { columns: "full", rows: 7 },
            },
            {
              type: "history-graph",
              title: "Tigo power · 48 hours",
              hours_to_show: 48,
              entities: [entityRow(e.currentPower, "Tigo cloud power")],
              grid_options: { columns: "full", rows: 6 },
            },
          ]),
        },
      ],
    },
    {
      title: "Modules",
      path: "modules",
      icon: "mdi:solar-panel-large",
      type: "sections",
      max_columns: 4,
      dense_section_placement: true,
      badges: compact([
        badge(e.reportingModules, "Reporting", "mdi:solar-panel"),
        badge(e.cloudDataAge, "Source age", "mdi:cloud-clock"),
      ]),
      sections: [...moduleGroups.entries()].map(([label, modules]) => moduleSection(label, modules)),
    },
    ...(discovery.comparison ? [comparisonView(discovery)] : []),
    {
      title: "Diagnostics",
      path: "system",
      icon: "mdi:stethoscope",
      type: "sections",
      max_columns: 2,
      dense_section_placement: true,
      sections: [
        {
          type: "grid",
          cards: [
            heading("Data status", "mdi:cloud-check-variant"),
            {
              type: "markdown",
              content: sourceStatusSummary(e, discovery.modules.length),
              grid_options: { columns: "full" },
            },
            {
              type: "entities",
              title: "Tigo Cloud",
              show_header_toggle: false,
              state_color: true,
              entities: compact([
                entityRow(e.cloudConnected, "API connected", "mdi:cloud-check"),
                entityRow(e.dataStale, "Daylight sample delayed", "mdi:clock-alert-outline"),
                entityRow(e.lastCloudUpdate, "Last source update", "mdi:cloud-clock"),
                entityRow(e.cloudDataAge, "Source age", "mdi:timer-sand"),
                entityRow(e.reportingModules, "Reporting modules", "mdi:solar-panel"),
                entityRow(e.moduleCount, "Configured modules", "mdi:solar-panel-large"),
                entityRow(e.accountTier, "Account tier", "mdi:account-badge-outline"),
                entityRow(e.pollingInterval, "Active polling interval", "mdi:timer-sync-outline"),
                entityRow(e.integrationVersion, "Integration version", "mdi:tag-outline"),
              ]),
              grid_options: { columns: "full" },
            },
            unavailableCard(e, discovery.modules),
          ],
        },
        {
          type: "grid",
          cards: [
            heading("Equipment and measurements", "mdi:solar-panel-large"),
            {
              type: "markdown",
              content: topologySummary(discovery),
              grid_options: { columns: "full" },
            },
            {
              type: "markdown",
              content: capacitySummary(e),
              grid_options: { columns: "full" },
            },
            {
              type: "markdown",
              content: measurementGuide(Boolean(discovery.comparison)),
              grid_options: { columns: "full" },
            },
          ],
        },
      ],
    },
  ];
  return { views };
}

export const dashboardMetadata = Object.freeze({
  urlPath: "tigo-energy",
  title: "Tigo Energy",
  icon: "mdi:solar-panel-large",
  showInSidebar: true,
  requireAdmin: false,
});
