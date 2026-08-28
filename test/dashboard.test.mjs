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
  const addEntity = ({ deviceId, domain, uniqueId, attributes = {} }) => {
    entitySequence += 1;
    const entity = {
      entity_id: `${domain}.operator_renamed_${String(entitySequence).padStart(3, "0")}`,
      unique_id: uniqueId,
      device_id: deviceId,
      platform: "tigo_energy",
      original_name: "Localized display name",
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

test("discovers a system and modules through identifiers and unique ids after entity renames", () => {
  const data = fixture({ moduleCount: 44 });
  const discovery = discoverTigo(data);
  assert.equal(discovery.system.deviceId, data.systemDeviceId);
  assert.equal(discovery.system.systemId, data.systemId);
  assert.equal(discovery.modules.length, 44);
  assert.equal(discovery.modules[0].panelLabel, "Panel 01");
  assert.equal(discovery.modules[0].groupLabel, "Inverter 1 · MPPT 1 · String 1");
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

test("optional system diagnostics enrich the System view without becoming prerequisites", () => {
  const complete = fixture({ moduleCount: 2 });
  const discovery = discoverTigo(complete);
  const systemView = buildDashboard(discovery).views.find((view) => view.path === "system");
  const serialized = stableString(systemView);
  for (const key of ["accountTier", "moduleCount", "pollingInterval", "integrationVersion"]) {
    assert.ok(discovery.entities[key]);
    assert.ok(serialized.includes(discovery.entities[key]));
  }

  const optionalSuffixes = new Set(["account_tier", "module_count", "polling_interval", "integration_version"]);
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
  assert.doesNotThrow(() => validateDashboard(buildDashboard(fallback), withoutOptional.states));
});

test("dashboard uses four responsive native-only, read-only views for variable module counts", () => {
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
    assert.ok(serialized.includes("does not change Home Assistant's global Energy sources"));
    assert.ok(validation.references.every((entityId) => data.states.some((state) => state.entity_id === entityId)));
  }
});

test("module view groups panels by inverter, MPPT, and string attributes", () => {
  const data = fixture({ moduleCount: 9 });
  const dashboard = buildDashboard(discoverTigo(data));
  const modulesView = dashboard.views.find((view) => view.path === "modules");
  assert.equal(modulesView.sections.length, 3);
  const headings = modulesView.sections.map((section) => section.cards[0].heading);
  assert.deepEqual(headings, [
    "Inverter 1 · MPPT 1 · String 1",
    "Inverter 1 · MPPT 1 · String 2",
    "Inverter 1 · MPPT 2 · String 3",
  ]);
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
  assert.equal(collectDashboardTemplates(dashboard).length, 1);
  const requests = [];
  const client = {
    async request(path, options) {
      requests.push({ path, options });
      return "rendered";
    },
  };
  assert.deepEqual(await validateDashboardTemplates(client, dashboard), { templateCount: 1 });
  assert.equal(requests[0].path, "/api/template");
  assert.equal(requests[0].options.method, "POST");
  assert.equal(requests[0].options.responseType, "text");
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
