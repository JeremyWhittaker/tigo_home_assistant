import assert from "node:assert/strict";
import { readFileSync, rmSync, statSync } from "node:fs";
import { dirname } from "node:path";
import test from "node:test";

import { parseArguments } from "../scripts/dashboard.mjs";
import { buildDashboard, dashboardMetadata } from "../src/dashboard.mjs";
import {
  applyDashboard,
  collectDashboardTemplates,
  collectEntityReferences,
  createBackup,
  loadBackup,
  planDashboard,
  restoreBackup,
  stableString,
  validateDashboard,
  validateDashboardTemplates,
} from "../src/deployer.mjs";
import { discoverTigo, discoveryContract } from "../src/discovery.mjs";

function fixture({ moduleCount = 8, systemId = "123456" } = {}) {
  const systemDeviceId = "device-system";
  const devices = [{
    id: systemDeviceId,
    identifiers: [["tigo_energy", systemId]],
    manufacturer: "Tigo Energy",
    model: "Tigo Energy Cloud",
    name: "South Roof Solar",
    disabled_by: null,
  }];
  const entities = [];
  const states = [];
  let entitySequence = 0;
  const addEntity = ({
    deviceId,
    domain,
    uniqueId,
    attributes = {},
    platform = "tigo_energy",
    originalName = "Localized display name",
  }) => {
    entitySequence += 1;
    const entity = {
      entity_id: `${domain}.operator_renamed_${String(entitySequence).padStart(3, "0")}`,
      unique_id: uniqueId,
      device_id: deviceId,
      platform,
      original_name: originalName,
      disabled_by: null,
    };
    entities.push(entity);
    states.push({ entity_id: entity.entity_id, state: "1", attributes });
    return entity.entity_id;
  };

  for (const specification of Object.values(discoveryContract.system)) {
    addEntity({
      deviceId: systemDeviceId,
      domain: specification.domain,
      uniqueId: `${systemId}_${specification.suffix}`,
    });
  }
  for (let index = 1; index <= moduleCount; index += 1) {
    const objectId = String(10_000 + index);
    const deviceId = `device-module-${index}`;
    const stringNumber = Math.ceil(index / 4);
    devices.push({
      id: deviceId,
      identifiers: [["tigo_energy", `${systemId}_module_${objectId}`]],
      manufacturer: "Tigo Energy",
      model: "TS4 Optimizer",
      name: `Optimizer ${index}`,
      via_device_id: systemDeviceId,
      disabled_by: null,
    });
    const attributes = {
      panel_label: `Panel ${String(index).padStart(2, "0")}`,
      inverter_label: "Inverter 1",
      mppt_label: `MPPT ${Math.ceil(stringNumber / 2)}`,
      string_label: `String ${stringNumber}`,
      sample_time: "2026-08-28T12:15:00-07:00",
    };
    for (const specification of Object.values(discoveryContract.module)) {
      addEntity({
        deviceId,
        domain: specification.domain,
        uniqueId: `${systemId}_module_${objectId}_${specification.suffix}`,
        attributes,
      });
    }
  }
  return { devices, entities, states, systemDeviceId, systemId, moduleCount };
}

function attachEg4(data, { serial = "1234567890", deviceId = "device-eg4", match = true } = {}) {
  data.devices.push({
    id: deviceId,
    identifiers: [["eg4_web_monitor", serial]],
    manufacturer: "EG4 Electronics",
    model: "18KPV",
    name: `EG4 18KPV ${serial}`,
    disabled_by: null,
  });
  if (match) {
    for (const state of data.states) {
      if (state.attributes?.inverter_label) state.attributes.inverter_label = `EG4 18KPV ${serial}`;
    }
  }
  const specifications = [
    ["pv_power", "PV Total Power", "power", "W", "measurement"],
    ["yield", "Yield", "energy", "kWh", "total_increasing"],
    ["yield_lifetime", "Yield (Lifetime)", "energy", "kWh", "total_increasing"],
  ];
  const result = {};
  for (const [key, originalName, deviceClass, unit, stateClass] of specifications) {
    const entityId = `sensor.eg4_${deviceId.replaceAll("-", "_")}_${key}`;
    data.entities.push({
      entity_id: entityId,
      unique_id: `${serial}_${key}`,
      device_id: deviceId,
      platform: "eg4_web_monitor",
      original_name: originalName,
      disabled_by: null,
    });
    data.states.push({
      entity_id: entityId,
      state: "1",
      attributes: {
        device_class: deviceClass,
        unit_of_measurement: unit,
        state_class: stateClass,
      },
    });
    result[key] = entityId;
  }
  const pv1 = `sensor.eg4_${deviceId.replaceAll("-", "_")}_pv_1_power`;
  data.entities.push({
    entity_id: pv1,
    unique_id: `${serial}_pv_1_power`,
    device_id: deviceId,
    platform: "eg4_web_monitor",
    original_name: "PV 1 Power",
    disabled_by: null,
  });
  data.states.push({
    entity_id: pv1,
    state: "1",
    attributes: { device_class: "power", unit_of_measurement: "W", state_class: "measurement" },
  });
  return { ...result, pv1, deviceId, serial };
}

test("discovers a system and modules through identifiers and unique ids after entity renames", () => {
  const data = fixture({ moduleCount: 44 });
  const discovery = discoverTigo(data);
  assert.equal(discovery.system.deviceId, data.systemDeviceId);
  assert.equal(discovery.system.systemId, data.systemId);
  assert.equal(discovery.modules.length, 44);
  assert.equal(discovery.modules[0].panelLabel, "Panel 01");
  assert.equal(discovery.modules[0].groupLabel, "MPPT 1 · String 1");
  assert.match(discovery.entities.currentPower, /^sensor\.operator_renamed_/);
  assert.notEqual(discovery.entities.currentPower, `sensor.${data.systemId}_current_power`);
});

test("discovery handles multiple systems with an explicit selector and rejects missing state", () => {
  const first = fixture({ moduleCount: 1, systemId: "111" });
  const second = fixture({ moduleCount: 1, systemId: "222" });
  second.devices = second.devices.map((device) => ({ ...device, id: `second-${device.id}`, via_device_id: device.via_device_id ? `second-${device.via_device_id}` : undefined }));
  second.entities = second.entities.map((entity) => ({ ...entity, device_id: `second-${entity.device_id}`, entity_id: entity.entity_id.replace("operator_renamed", "second_renamed") }));
  second.states = second.states.map((state) => ({ ...state, entity_id: state.entity_id.replace("operator_renamed", "second_renamed") }));
  const combined = {
    devices: [...first.devices, ...second.devices],
    entities: [...first.entities, ...second.entities],
    states: [...first.states, ...second.states],
  };
  assert.throws(() => discoverTigo(combined), /exactly one enabled Tigo system/);
  assert.equal(discoverTigo({ ...combined, selector: "222" }).system.systemId, "222");

  const missing = fixture({ moduleCount: 1 });
  missing.states.shift();
  assert.throws(() => discoverTigo(missing), /absent from live state/);
});

test("optional system diagnostics enrich the Diagnostics view without becoming prerequisites", () => {
  const complete = fixture({ moduleCount: 2 });
  const discovery = discoverTigo(complete);
  const systemView = buildDashboard(discovery).views.find((view) => view.path === "system");
  const serialized = stableString(systemView);
  for (const key of ["accountTier", "moduleCount", "ratedArrayPower", "pollingInterval", "integrationVersion"]) {
    assert.ok(discovery.entities[key]);
    assert.ok(serialized.includes(discovery.entities[key]));
  }

  const optionalSuffixes = new Set([
    "account_tier", "module_count", "rated_array_power", "polling_interval", "integration_version",
  ]);
  const optionalIds = new Set(
    complete.entities
      .filter((entity) => [...optionalSuffixes].some((suffix) => entity.unique_id.endsWith(`_${suffix}`)))
      .map((entity) => entity.entity_id),
  );
  const withoutOptional = {
    ...complete,
    entities: complete.entities.filter((entity) => !optionalIds.has(entity.entity_id)),
    states: complete.states.filter((state) => !optionalIds.has(state.entity_id)),
  };
  const fallback = discoverTigo(withoutOptional);
  assert.equal(fallback.entities.accountTier, null);
  assert.equal(fallback.entities.moduleCount, null);
  assert.equal(fallback.entities.ratedArrayPower, null);
  assert.doesNotThrow(() => validateDashboard(buildDashboard(fallback), withoutOptional.states));
});

test("dashboard uses responsive native-only, read-only views for variable module counts", () => {
  for (const moduleCount of [1, 7, 44]) {
    const data = fixture({ moduleCount });
    const dashboard = buildDashboard(discoverTigo(data));
    const validation = validateDashboard(dashboard, data.states);
    assert.deepEqual(dashboard.views.map((view) => view.path), ["overview", "energy", "modules", "system"]);
    assert.ok(dashboard.views.every((view) => view.type === "sections"));
    assert.ok(dashboard.views.find((view) => view.path === "modules").sections.length >= 1);
    assert.equal(collectEntityReferences(dashboard).size, validation.references.length);
    const serialized = stableString(dashboard);
    assert.ok(!serialized.includes("custom:"));
    assert.ok(!serialized.includes('"action":"toggle"'));
    assert.ok(!serialized.includes('"type":"energy-'));
    assert.ok(!serialized.includes("last_updated"));
    assert.ok(serialized.includes("double-counts production"));
    assert.ok(!stableString(dashboard.views.find((view) => view.path === "overview"))
      .includes("sample within freshness window"));
    assert.ok(validation.references.every((entityId) => data.states.some((state) => state.entity_id === entityId)));
    const statisticsCards = dashboard.views.flatMap((view) => view.sections)
      .flatMap((section) => section.cards)
      .filter((card) => card.type === "statistics-graph");
    assert.ok(statisticsCards.every((card) => card.stat_types[0] === "change"));
  }
});

test("overview stays compact while Diagnostics separates stale and module-level exceptions", () => {
  const data = fixture({ moduleCount: 44 });
  const discovery = discoverTigo(data);
  const dashboard = buildDashboard(discovery);
  const moduleEntityIds = new Set(
    discovery.modules.flatMap((module) => [module.entities.power, module.entities.energyToday]),
  );

  const filtersFor = (path) => dashboard.views.find((candidate) => candidate.path === path).sections
    .flatMap((section) => section.cards)
    .filter((card) => card.type === "entity-filter");
  const overviewFilters = filtersFor("overview");
  assert.equal(overviewFilters.length, 1);
  assert.ok(overviewFilters[0].entities.every((row) => !moduleEntityIds.has(row.entity)));

  const diagnosticsFilters = filtersFor("system");
  assert.equal(diagnosticsFilters.length, 1);
  assert.ok(diagnosticsFilters[0].entities.some((row) => discovery.modules
    .some((module) => row.entity === module.entities.energyToday)));
  assert.ok(diagnosticsFilters[0].entities.every((row) => !discovery.modules
    .some((module) => row.entity === module.entities.power)));

  const diagnosticsCards = dashboard.views.find((candidate) => candidate.path === "system").sections
    .flatMap((section) => section.cards);
  const modulePowerExceptions = diagnosticsCards.find((card) =>
    card.type === "conditional" && card.card?.type === "entity-filter");
  assert.deepEqual(modulePowerExceptions.conditions, [
    { condition: "state", entity: discovery.entities.dataStale, state: "off" },
  ]);
  assert.ok(modulePowerExceptions.card.entities.every((row) => discovery.modules
    .some((module) => row.entity === module.entities.power)));
  assert.ok(modulePowerExceptions.card.entities.every((row) => moduleEntityIds.has(row.entity)));
});

test("module view groups panels by inverter, MPPT, and string attributes", () => {
  const data = fixture({ moduleCount: 9 });
  const dashboard = buildDashboard(discoverTigo(data));
  const modulesView = dashboard.views.find((view) => view.path === "modules");
  assert.equal(modulesView.sections.length, 3);
  const headings = modulesView.sections.map((section) => section.cards[0].heading);
  assert.deepEqual(headings, [
    "MPPT 1 · String 1",
    "MPPT 1 · String 2",
    "MPPT 2 · String 3",
  ]);
  assert.ok(modulesView.sections.every((section) => section.cards.slice(1)
    .every((card) => card.type === "entities")));
});

test("unavailable module topology is recovered only from an unambiguous panel-label family", () => {
  const data = fixture({ moduleCount: 8 });
  const stringTwoStates = data.states.filter((state) => state.attributes?.string_label === "String 2");
  for (let index = 0; index < stringTwoStates.length; index += 2) {
    stringTwoStates[index].attributes.panel_label = `C${(index / 2) + 1}`;
    stringTwoStates[index].attributes.string_label = "String C";
    stringTwoStates[index + 1].attributes.panel_label = `C${(index / 2) + 1}`;
    stringTwoStates[index + 1].attributes.string_label = "String C";
  }
  for (const state of stringTwoStates.slice(-2)) {
    const { panel_label: panelLabel } = state.attributes;
    state.attributes = { panel_label: panelLabel };
  }
  const discovery = discoverTigo(data);
  const recovered = discovery.modules.find((module) => module.panelLabel === "C4");
  assert.equal(recovered.groupLabel, "MPPT 1 · String C");
  assert.ok(!discovery.modules.some((module) => module.groupLabel === "Unassigned modules"));

  const partial = fixture({ moduleCount: 1 });
  for (const state of partial.states.filter((candidate) => candidate.attributes?.panel_label)) {
    state.attributes = { panel_label: "Panel 01", inverter_label: "Only inverter survives" };
  }
  assert.equal(discoverTigo(partial).modules[0].groupLabel, "Unassigned modules");
});

test("Compare is enabled only for one serial-matched EG4 device with strict total metadata", () => {
  const data = fixture({ moduleCount: 4 });
  const eg4 = attachEg4(data);
  data.devices.push({
    id: "device-eg4-battery-bank",
    manufacturer: "EG4 Electronics",
    model: "Battery Bank",
    name: `Battery Bank ${eg4.serial}`,
    disabled_by: null,
  });
  data.entities.push({
    entity_id: "sensor.eg4_battery_bank_state_of_charge",
    unique_id: `${eg4.serial}_battery_soc`,
    device_id: "device-eg4-battery-bank",
    platform: "eg4_web_monitor",
    original_name: "State of Charge",
    disabled_by: null,
  });
  data.states.push({ entity_id: "sensor.eg4_battery_bank_state_of_charge", state: "80", attributes: {} });
  const discovery = discoverTigo(data);
  assert.deepEqual(discovery.comparison.entities, {
    pvPower: eg4.pv_power,
    energyToday: eg4.yield,
  });
  const dashboard = buildDashboard(discovery);
  assert.deepEqual(dashboard.views.map((view) => view.path), [
    "overview", "energy", "modules", "compare", "system",
  ]);
  const serialized = stableString(dashboard.views.find((view) => view.path === "compare"));
  assert.ok(serialized.includes(eg4.pv_power));
  assert.ok(!serialized.includes(eg4.pv1));
  assert.ok(!serialized.includes(eg4.serial));
  const compareStatistics = dashboard.views.find((view) => view.path === "compare").sections
    .flatMap((section) => section.cards)
    .find((card) => card.type === "statistics-graph");
  assert.deepEqual(compareStatistics.stat_types, ["change"]);
  assert.ok(compareStatistics.entities.some((row) => row.entity === eg4.yield));
  assert.ok(!compareStatistics.entities.some((row) => row.entity === eg4.yield_lifetime));

  const incompatible = fixture({ moduleCount: 2 });
  const bad = attachEg4(incompatible);
  incompatible.states.find((state) => state.entity_id === bad.pv_power)
    .attributes.unit_of_measurement = "kW";
  assert.equal(discoverTigo(incompatible).comparison, null);

  const resettableDaily = fixture({ moduleCount: 2 });
  const resettable = attachEg4(resettableDaily);
  resettableDaily.states.find((state) => state.entity_id === resettable.yield)
    .attributes.state_class = "total";
  assert.equal(discoverTigo(resettableDaily).comparison, null);

  const ambiguous = fixture({ moduleCount: 2 });
  attachEg4(ambiguous);
  attachEg4(ambiguous, { deviceId: "device-eg4-duplicate" });
  assert.equal(discoverTigo(ambiguous).comparison, null);

  const multipleTigoInverters = fixture({ moduleCount: 4 });
  attachEg4(multipleTigoInverters);
  for (const state of multipleTigoInverters.states.filter((candidate) =>
    candidate.attributes?.panel_label === "Panel 04")) {
    state.attributes.inverter_label = "Second inverter 9999999999";
  }
  assert.equal(discoverTigo(multipleTigoInverters).comparison, null);

  const displayNameCollision = fixture({ moduleCount: 2 });
  const collision = attachEg4(displayNameCollision, { serial: "8765432100" });
  for (const state of displayNameCollision.states.filter((candidate) => candidate.attributes?.inverter_label)) {
    state.attributes.inverter_label = "Shared site 1234567890";
  }
  displayNameCollision.devices.find((device) => device.id === collision.deviceId).name = "Shared site 1234567890";
  assert.equal(discoverTigo(displayNameCollision).comparison, null);
});

test("dashboard validation rejects missing state, control entities, custom cards, and write actions", () => {
  const data = fixture({ moduleCount: 2 });
  const dashboard = buildDashboard(discoverTigo(data));
  assert.throws(() => validateDashboard(dashboard, data.states.slice(1)), /missing live entities/);

  const withControl = structuredClone(dashboard);
  withControl.views[0].sections[0].cards.push({ type: "tile", entity: "switch.forbidden_control" });
  assert.throws(
    () => validateDashboard(withControl, [...data.states, { entity_id: "switch.forbidden_control" }]),
    /control entities/,
  );
  const withCustom = structuredClone(dashboard);
  withCustom.views[0].sections[0].cards.push({ type: "custom:power-flow-card" });
  assert.throws(() => validateDashboard(withCustom, data.states), /unsupported card/);
  const withAction = structuredClone(dashboard);
  withAction.views[0].sections[0].cards.push({ type: "markdown", content: "x", tap_action: { action: "call-service" } });
  assert.throws(() => validateDashboard(withAction, data.states), /mutating action/);
});

test("Home Assistant renders every generated template during preflight", async () => {
  const dashboard = buildDashboard(discoverTigo(fixture({ moduleCount: 2 })));
  assert.equal(collectDashboardTemplates(dashboard).length, 2);
  const requests = [];
  const client = {
    async request(path, options) {
      requests.push({ path, options });
      return "rendered";
    },
  };
  assert.deepEqual(await validateDashboardTemplates(client, dashboard), { templateCount: 2 });
  assert.equal(requests.length, 2);
  assert.ok(requests.every((request) => request.path === "/api/template"));
  assert.ok(requests.every((request) => request.options.method === "POST"));
  assert.ok(requests.every((request) => request.options.responseType === "text"));
});

class FakeWs {
  constructor({ failOn } = {}) {
    this.calls = [];
    this.failOn = failOn;
    this.failed = false;
  }

  async call(command) {
    this.calls.push(structuredClone(command));
    if (this.failOn && command.type === this.failOn && !this.failed) {
      this.failed = true;
      throw new Error("injected failure");
    }
    if (command.type === "lovelace/dashboards/create") return { id: "tigo_energy" };
    return null;
  }
}

function existingDashboard(overrides = {}) {
  return {
    id: "tigo_energy",
    url_path: dashboardMetadata.urlPath,
    mode: "storage",
    title: dashboardMetadata.title,
    icon: dashboardMetadata.icon,
    show_in_sidebar: dashboardMetadata.showInSidebar,
    require_admin: dashboardMetadata.requireAdmin,
    ...overrides,
  };
}

test("deployment plans create, update, and no-op without replacing YAML dashboards", () => {
  const candidate = { views: [{ title: "Overview", path: "overview" }] };
  assert.equal(planDashboard({ existing: null, existingConfig: null, candidate, metadata: dashboardMetadata }).action, "create");
  assert.equal(planDashboard({ existing: existingDashboard(), existingConfig: candidate, candidate, metadata: dashboardMetadata }).action, "unchanged");
  assert.equal(planDashboard({ existing: existingDashboard(), existingConfig: { views: [] }, candidate, metadata: dashboardMetadata }).action, "update");
  assert.throws(
    () => planDashboard({ existing: existingDashboard({ mode: "yaml" }), existingConfig: null, candidate, metadata: dashboardMetadata }),
    /refusing to replace/,
  );
});

test("deployment creates, updates, skips unchanged, and rolls back write failures", async () => {
  const candidate = { views: [{ title: "Overview", path: "overview" }] };
  const createWs = new FakeWs();
  const created = await applyDashboard({
    ws: createWs,
    existing: null,
    existingConfig: null,
    candidate,
    metadata: dashboardMetadata,
  });
  assert.equal(created.dashboardId, "tigo_energy");
  assert.deepEqual(createWs.calls.map((call) => call.type), ["lovelace/dashboards/create", "lovelace/config/save"]);

  const unchangedWs = new FakeWs();
  const unchanged = await applyDashboard({
    ws: unchangedWs,
    existing: existingDashboard(),
    existingConfig: candidate,
    candidate,
    metadata: dashboardMetadata,
  });
  assert.equal(unchanged.action, "unchanged");
  assert.deepEqual(unchangedWs.calls, []);

  const updateWs = new FakeWs();
  await applyDashboard({
    ws: updateWs,
    existing: existingDashboard(),
    existingConfig: { views: [] },
    candidate,
    metadata: dashboardMetadata,
  });
  assert.deepEqual(updateWs.calls.map((call) => call.type), ["lovelace/config/save"]);

  const failing = new FakeWs({ failOn: "lovelace/config/save" });
  await assert.rejects(
    applyDashboard({ ws: failing, existing: null, existingConfig: null, candidate, metadata: dashboardMetadata }),
    /automatic rollback completed/,
  );
  assert.deepEqual(failing.calls.map((call) => call.type), [
    "lovelace/dashboards/create",
    "lovelace/config/save",
    "lovelace/dashboards/delete",
  ]);
});

test("round-trip verification failure restores an existing dashboard and its metadata", async () => {
  const previous = { views: [{ title: "Operator dashboard", path: "operator" }] };
  const candidate = { views: [{ title: "Overview", path: "overview" }] };
  const existing = existingDashboard({ title: "Operator title", icon: "mdi:account", show_in_sidebar: false });
  const ws = new FakeWs();
  await assert.rejects(
    applyDashboard({
      ws,
      existing,
      existingConfig: previous,
      candidate,
      metadata: dashboardMetadata,
      verify: async () => { throw new Error("round-trip mismatch"); },
    }),
    /automatic rollback completed/,
  );
  assert.deepEqual(ws.calls.map((call) => call.type), [
    "lovelace/config/save",
    "lovelace/dashboards/update",
    "lovelace/config/save",
    "lovelace/dashboards/update",
  ]);
  assert.deepEqual(ws.calls[2].config, previous);
  assert.equal(ws.calls[3].title, "Operator title");
});

test("backup is private and checksummed; restore refuses drift and deletes an unchanged created dashboard", async () => {
  const candidate = { views: [{ title: "Overview", path: "overview" }] };
  const created = createBackup({
    baseUrl: "https://ha.invalid",
    haVersion: "2026.8.3",
    metadata: dashboardMetadata,
    existing: null,
    existingConfig: null,
    candidate,
  });
  try {
    assert.equal(statSync(created.path).mode & 0o777, 0o600);
    assert.equal(statSync(dirname(created.path)).mode & 0o777, 0o700);
    assert.ok(!readFileSync(created.path, "utf8").includes("secret-token"));
    const backup = loadBackup(created.path);
    const current = { ...existingDashboard(), ...backup.deployed.metadata };
    const driftedWs = {
      async call(command) {
        if (command.type === "lovelace/dashboards/list") return [current];
        if (command.type === "lovelace/config") return { views: [{ title: "Operator edit", path: "edited" }] };
        throw new Error(`unexpected command ${command.type}`);
      },
    };
    await assert.rejects(restoreBackup({ ws: driftedWs, backup }), /has drifted/);

    const calls = [];
    const matchingWs = {
      async call(command) {
        calls.push(command);
        if (command.type === "lovelace/dashboards/list") return [current];
        if (command.type === "lovelace/config") return candidate;
        if (command.type === "lovelace/dashboards/delete") return null;
        throw new Error(`unexpected command ${command.type}`);
      },
    };
    assert.deepEqual(await restoreBackup({ ws: matchingWs, backup }), { action: "deleted-created-dashboard" });
    assert.equal(calls.at(-1).type, "lovelace/dashboards/delete");
  } finally {
    rmSync(dirname(created.path), { recursive: true, force: true });
  }
});

test("restore puts a prior dashboard configuration and metadata back", async () => {
  const priorConfig = { views: [{ title: "Prior", path: "prior" }] };
  const candidate = { views: [{ title: "Overview", path: "overview" }] };
  const prior = existingDashboard({ title: "Prior title", icon: "mdi:history", show_in_sidebar: false });
  const created = createBackup({
    baseUrl: "https://ha.invalid",
    haVersion: "2026.8.3",
    metadata: dashboardMetadata,
    existing: prior,
    existingConfig: priorConfig,
    candidate,
  });
  try {
    const backup = loadBackup(created.path);
    const current = { ...existingDashboard(), ...backup.deployed.metadata };
    const calls = [];
    const ws = {
      async call(command) {
        calls.push(structuredClone(command));
        if (command.type === "lovelace/dashboards/list") return [current];
        if (command.type === "lovelace/config") return candidate;
        return null;
      },
    };
    assert.deepEqual(await restoreBackup({ ws, backup }), { action: "restored-prior-dashboard" });
    assert.deepEqual(calls.map((call) => call.type), [
      "lovelace/dashboards/list",
      "lovelace/config",
      "lovelace/config/save",
      "lovelace/dashboards/update",
    ]);
    assert.deepEqual(calls[2].config, priorConfig);
    assert.equal(calls[3].title, "Prior title");
  } finally {
    rmSync(dirname(created.path), { recursive: true, force: true });
  }
});

test("dashboard command parser exposes preflight, plan, deploy, and drift-aware restore", () => {
  assert.deepEqual(parseArguments(["preflight"]), { command: "preflight", backupPath: null, force: false });
  assert.deepEqual(parseArguments(["plan"]), { command: "plan", backupPath: null, force: false });
  assert.deepEqual(parseArguments(["deploy"]), { command: "deploy", backupPath: null, force: false });
  assert.deepEqual(parseArguments(["restore", "/tmp/backup.json", "--force"]), {
    command: "restore",
    backupPath: "/tmp/backup.json",
    force: true,
  });
  assert.throws(() => parseArguments([]), /Choose one command/);
  assert.throws(() => parseArguments(["restore"]), /exactly one backup/);
});
