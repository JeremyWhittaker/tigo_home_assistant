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
    state_content: ["state", "last_updated"],
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

function cloudSummary(entities, moduleCount) {
  return `{% set missing = ['unknown', 'unavailable', 'none', ''] %}
{% set connected = is_state('${entities.cloudConnected}', 'on') %}
{% set stale = is_state('${entities.dataStale}', 'on') %}
{% set age = states('${entities.cloudDataAge}') %}
{% set reporting = states('${entities.reportingModules}') %}
{% if not connected %}
## 🔴 Tigo Cloud is unavailable
The integration cannot currently reach the cloud. Existing readings may remain visible for context, but they are not live.
{% elif stale %}
## 🟠 Cloud data is delayed
The API is reachable, but Tigo's newest source sample is stale. Module power readings are marked unavailable; system totals remain visible for context.
{% else %}
## 🟢 Tigo Cloud data is current
The integration is connected and the most recent source sample is within its freshness window.
{% endif %}
**Reporting:** {{ reporting if reporting | lower not in missing else 'unavailable' }} of ${moduleCount} modules · **Source timestamp:** {{ states('${entities.lastCloudUpdate}') }} · **Data age:** {{ age if age | lower not in missing else 'unavailable' }} min

Cloud readings are normally delayed. Missing module values stay unavailable and are never treated as zero.`;
}

function unavailableCard(entities, modules = []) {
  return {
    type: "entity-filter",
    state_filter: ["unknown", "unavailable"],
    show_empty: false,
    entities: compact([
      entityRow(entities.currentPower, "System power", "mdi:solar-power"),
      entityRow(entities.energyToday, "Energy today", "mdi:calendar-today"),
      entityRow(entities.energyLifetime, "Lifetime energy", "mdi:counter"),
      ...modules.flatMap((module) => [
        entityRow(module.entities.power, `${module.panelLabel} power`, "mdi:flash"),
        entityRow(module.entities.energyToday, `${module.panelLabel} energy today`, "mdi:solar-panel"),
      ]),
    ]),
    card: {
      type: "entities",
      title: "Unavailable cloud readings",
      show_header_toggle: false,
      state_color: true,
    },
    grid_options: { columns: "full" },
  };
}

function topologySummary(discovery) {
  const groups = [...new Set(discovery.modules.map((module) => module.groupLabel))];
  const firmware = discovery.system.firmware ? `\n- Integration/device version: ${discovery.system.firmware}` : "";
  return `## ${discovery.system.name}
- Tigo system: ${discovery.system.systemId}
- Cloud model: ${discovery.system.model}
- Optimizers: ${discovery.modules.length}
- Inverter / MPPT / string groups: ${groups.length}${firmware}

${groups.map((group) => `- ${group}`).join("\n")}`;
}

function moduleSection(groupLabel, modules) {
  return {
    type: "grid",
    cards: [
      heading(groupLabel, "mdi:solar-panel-large"),
      ...modules.flatMap((module) => [
        heading(module.panelLabel, "mdi:solar-panel", "subtitle"),
        tile(module.entities.power, "Power", "mdi:flash", 6),
        tile(module.entities.energyToday, "Energy today", "mdi:chart-bell-curve-cumulative", 6),
      ]),
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

  return {
    views: [
      {
        title: "Overview",
        path: "overview",
        icon: "mdi:solar-power",
        type: "sections",
        max_columns: 2,
        dense_section_placement: true,
        badges: compact([
          badge(e.currentPower, "Solar", "mdi:solar-power"),
          badge(e.energyToday, "Today", "mdi:calendar-today"),
          badge(e.reportingModules, "Reporting", "mdi:solar-panel"),
          badge(e.dataStale, "Delayed", "mdi:clock-alert-outline"),
        ]),
        sections: [
          {
            type: "grid",
            cards: compact([
              heading("Cloud status", "mdi:cloud-check-variant"),
              {
                type: "markdown",
                content: cloudSummary(e, discovery.modules.length),
                grid_options: { columns: "full" },
              },
              unavailableCard(e),
              heading("Production now", "mdi:flash", "subtitle"),
              tile(e.currentPower, "System power", "mdi:solar-power", 6),
              tile(e.peakPowerToday, "Peak power today", "mdi:chart-line-variant", 6),
              tile(e.energyToday, "Energy today", "mdi:white-balance-sunny", 6),
              tile(e.energyLifetime, "Lifetime energy", "mdi:counter", 6),
            ]),
          },
          {
            type: "grid",
            cards: compact([
              heading("Recent performance", "mdi:chart-areaspline"),
              {
                type: "history-graph",
                title: "System power · 24 hours",
                hours_to_show: 24,
                entities: [entityRow(e.currentPower, "Solar power")],
                grid_options: { columns: "full", rows: 6 },
              },
              heading("Cloud freshness", "mdi:clock-check-outline", "subtitle"),
              tile(e.lastCloudUpdate, "Last source update", "mdi:cloud-clock", 6),
              tile(e.cloudDataAge, "Cloud data age", "mdi:timer-sand", 6),
              tile(e.reportingModules, "Reporting modules", "mdi:solar-panel-large", 6),
              tile(e.cloudConnected, "API connection", "mdi:cloud-check", 6),
            ]),
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
              {
                type: "markdown",
                content: "This dashboard shows Tigo production only. It does not change Home Assistant's global Energy sources, avoiding accidental double-counting with an inverter integration.",
                grid_options: { columns: "full" },
              },
            ]),
          },
          {
            type: "grid",
            cards: compact([
              heading("Energy history", "mdi:chart-bar"),
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
                title: "Power · 48 hours",
                hours_to_show: 48,
                entities: [entityRow(e.currentPower, "System power")],
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
          badge(e.lastCloudUpdate, "Source update", "mdi:cloud-clock"),
          badge(e.dataStale, "Delayed", "mdi:clock-alert-outline"),
        ]),
        sections: [...moduleGroups.entries()].map(([label, modules]) => moduleSection(label, modules)),
      },
      {
        title: "System",
        path: "system",
        icon: "mdi:information-outline",
        type: "sections",
        max_columns: 2,
        dense_section_placement: true,
        sections: [
          {
            type: "grid",
            cards: [
              heading("Cloud health", "mdi:cloud-check-variant"),
              {
                type: "entities",
                title: "Tigo Cloud",
                show_header_toggle: false,
                state_color: true,
                entities: compact([
                  entityRow(e.cloudConnected, "API connected", "mdi:cloud-check"),
                  entityRow(e.dataStale, "Cloud data delayed", "mdi:clock-alert-outline"),
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
              unavailableCard(e),
            ],
          },
          {
            type: "grid",
            cards: [
              heading("Topology", "mdi:family-tree"),
              {
                type: "markdown",
                content: topologySummary(discovery),
                grid_options: { columns: "full" },
              },
              heading("About these readings", "mdi:information-outline", "subtitle"),
              {
                type: "markdown",
                content: "Tigo cloud telemetry is read-only and can trail real time. Connectivity and data freshness are reported separately: a reachable API does not guarantee a recent module sample. Open an entity for timestamps and source attributes.",
                grid_options: { columns: "full" },
              },
            ],
          },
        ],
      },
    ],
  };
}

export const dashboardMetadata = Object.freeze({
  urlPath: "tigo-energy",
  title: "Tigo Energy",
  icon: "mdi:solar-panel-large",
  showInSidebar: true,
  requireAdmin: false,
});
