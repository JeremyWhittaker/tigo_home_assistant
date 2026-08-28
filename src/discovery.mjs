const ENTITY_ID_PATTERN = /^[a-z_]+\.[a-z0-9_]+$/;
const PLATFORM = "tigo_energy";

const SYSTEM_ENTITIES = Object.freeze({
  currentPower: { domain: "sensor", suffix: "current_power", names: ["Current Power", "System Power"], required: true },
  peakPowerToday: { domain: "sensor", suffix: "peak_power_today", names: ["Peak Power Today"], required: false },
  energyToday: { domain: "sensor", suffix: "energy_today", names: ["Energy Today"], required: true },
  energyWeek: { domain: "sensor", suffix: "energy_week", names: ["Energy This Week", "Energy Week"], required: false },
  energyMonth: { domain: "sensor", suffix: "energy_month", names: ["Energy This Month", "Energy Month"], required: false },
  energyYear: { domain: "sensor", suffix: "energy_year", names: ["Energy This Year", "Energy Year"], required: false },
  energyLifetime: { domain: "sensor", suffix: "energy_lifetime", names: ["Lifetime Energy", "Energy Lifetime"], required: true },
  reportingModules: { domain: "sensor", suffix: "reporting_modules", names: ["Reporting Modules"], required: true },
  lastCloudUpdate: { domain: "sensor", suffix: "last_cloud_update", names: ["Last Cloud Update"], required: true },
  cloudDataAge: { domain: "sensor", suffix: "cloud_data_age", names: ["Cloud Data Age"], required: true },
  cloudConnected: { domain: "binary_sensor", suffix: "cloud_connected", names: ["Cloud Connected", "API Connectivity"], required: true },
  dataStale: { domain: "binary_sensor", suffix: "data_stale", names: ["Cloud Data Stale", "Data Stale"], required: true },
});

const MODULE_ENTITIES = Object.freeze({
  power: { domain: "sensor", suffix: "power", names: ["Power", "Module Power"], required: true },
  energyToday: { domain: "sensor", suffix: "energy_today", names: ["Energy Today", "Module Energy Today"], required: true },
});

function normalize(value) {
  return String(value ?? "").trim().toLocaleLowerCase();
}

function deviceIdentifiers(device) {
  return Array.isArray(device.identifiers)
    ? device.identifiers.filter((identifier) => Array.isArray(identifier) && identifier.length >= 2)
    : [];
}

function tigoIdentifier(device) {
  return deviceIdentifiers(device).find(([domain]) => domain === PLATFORM)?.[1] ?? null;
}

function nameMatches(entity, names) {
  const candidates = [entity.original_name, entity.name, entity.translation_key]
    .filter(Boolean)
    .map(normalize);
  return names.some((name) => candidates.includes(normalize(name)));
}

function uniqueIdMatches(entity, expectedPrefix, suffix) {
  const uniqueId = String(entity.unique_id ?? "");
  return uniqueId === `${expectedPrefix}_${suffix}` || uniqueId.endsWith(`_${suffix}`);
}

function stateIndex(states) {
  return new Map(states.map((state) => [state.entity_id, state]));
}

function resolveEntity({ registry, liveStates, deviceId, expectedPrefix, key, specification }) {
  const candidates = registry.filter((entity) =>
    entity.platform === PLATFORM
    && entity.device_id === deviceId
    && entity.disabled_by == null
    && entity.entity_id?.startsWith(`${specification.domain}.`)
  );
  const byUniqueId = candidates.filter((entity) => uniqueIdMatches(entity, expectedPrefix, specification.suffix));
  const matches = byUniqueId.length > 0
    ? byUniqueId
    : candidates.filter((entity) => nameMatches(entity, specification.names));
  if (matches.length === 0 && !specification.required) return null;
  if (matches.length !== 1) {
    const detail = matches.map((entity) => entity.entity_id).join(", ") || "none";
    throw new Error(`Expected one enabled Tigo ${key} entity on device ${deviceId}; found ${matches.length}: ${detail}`);
  }
  const entityId = matches[0].entity_id;
  if (!ENTITY_ID_PATTERN.test(entityId)) throw new Error(`Discovered invalid entity id for ${key}: ${entityId}`);
  if (!liveStates.has(entityId)) throw new Error(`Discovered ${key} is absent from live state: ${entityId}`);
  return entityId;
}

function resolveMap({ specification, registry, liveStates, deviceId, expectedPrefix }) {
  return Object.fromEntries(
    Object.entries(specification).map(([key, entitySpecification]) => [
      key,
      resolveEntity({
        registry,
        liveStates,
        deviceId,
        expectedPrefix,
        key,
        specification: entitySpecification,
      }),
    ]),
  );
}

function selectorMatches(device, systemId, selector) {
  if (!selector) return true;
  return [device.id, device.name, device.name_by_user, systemId]
    .filter(Boolean)
    .some((candidate) => normalize(candidate) === normalize(selector));
}

function stateAttributes(index, entityId) {
  const attributes = index.get(entityId)?.attributes;
  return attributes && typeof attributes === "object" ? attributes : {};
}

function preferredAttribute(attributeSets, key) {
  for (const attributes of attributeSets) {
    if (attributes[key] !== undefined && attributes[key] !== null && String(attributes[key]).trim()) {
      return String(attributes[key]).trim();
    }
  }
  return null;
}

const naturalOrder = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

export function discoverTigo({ devices, entities, states, selector = "" }) {
  if (!Array.isArray(devices) || !Array.isArray(entities) || !Array.isArray(states)) {
    throw new TypeError("devices, entities, and states must be arrays");
  }
  const liveStates = stateIndex(states);
  const systemCandidates = devices
    .filter((device) => device.disabled_by == null)
    .map((device) => ({ device, systemId: tigoIdentifier(device) }))
    .filter(({ device, systemId }) =>
      systemId
      && !String(systemId).includes("_module_")
      && selectorMatches(device, systemId, selector)
      && entities.some((entity) => entity.platform === PLATFORM && entity.device_id === device.id)
    );

  if (systemCandidates.length !== 1) {
    const hint = selector
      ? `selector ${JSON.stringify(selector)}`
      : "set TIGO_SYSTEM_DEVICE_ID to a Home Assistant device id or Tigo system id";
    throw new Error(`Expected exactly one enabled Tigo system (${hint}); found ${systemCandidates.length}`);
  }
  const { device: systemDevice, systemId } = systemCandidates[0];
  const systemEntities = resolveMap({
    specification: SYSTEM_ENTITIES,
    registry: entities,
    liveStates,
    deviceId: systemDevice.id,
    expectedPrefix: systemId,
  });

  const moduleDevices = devices.filter((device) => {
    if (device.disabled_by != null) return false;
    const identifier = tigoIdentifier(device);
    return identifier?.startsWith(`${systemId}_module_`) && device.via_device_id === systemDevice.id;
  });
  const modules = moduleDevices.map((device) => {
    const identifier = tigoIdentifier(device);
    const objectId = identifier.slice(`${systemId}_module_`.length);
    const moduleEntities = resolveMap({
      specification: MODULE_ENTITIES,
      registry: entities,
      liveStates,
      deviceId: device.id,
      expectedPrefix: identifier,
    });
    const attributeSets = [
      stateAttributes(liveStates, moduleEntities.power),
      stateAttributes(liveStates, moduleEntities.energyToday),
    ];
    const panelLabel = preferredAttribute(attributeSets, "panel_label")
      ?? device.name_by_user
      ?? device.name
      ?? `Module ${objectId}`;
    const inverterLabel = preferredAttribute(attributeSets, "inverter_label") ?? "Inverter";
    const mpptLabel = preferredAttribute(attributeSets, "mppt_label");
    const stringLabel = preferredAttribute(attributeSets, "string_label") ?? "Unassigned string";
    return {
      deviceId: device.id,
      objectId,
      panelLabel,
      inverterLabel,
      mpptLabel,
      stringLabel,
      groupLabel: [inverterLabel, mpptLabel, stringLabel].filter(Boolean).join(" · "),
      model: device.model || "TS4 Optimizer",
      entities: Object.freeze(moduleEntities),
    };
  });
  if (modules.length === 0) throw new Error(`No enabled Tigo modules were found for system ${systemId}`);
  modules.sort((left, right) =>
    naturalOrder.compare(left.groupLabel, right.groupLabel)
    || naturalOrder.compare(left.panelLabel, right.panelLabel)
    || naturalOrder.compare(left.objectId, right.objectId)
  );

  return {
    system: {
      deviceId: systemDevice.id,
      systemId,
      name: systemDevice.name_by_user || systemDevice.name || "Tigo Energy",
      model: systemDevice.model || "Tigo Energy Cloud",
      firmware: systemDevice.sw_version || null,
      areaId: systemDevice.area_id || null,
    },
    entities: Object.freeze(systemEntities),
    modules: Object.freeze(modules),
  };
}

export const discoveryContract = Object.freeze({
  platform: PLATFORM,
  system: SYSTEM_ENTITIES,
  module: MODULE_ENTITIES,
});
