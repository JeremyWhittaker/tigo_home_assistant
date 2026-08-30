const ENTITY_ID_PATTERN = /^[a-z_]+\.[a-z0-9_]+$/;
const PLATFORM = "tigo_energy";
const EG4_PLATFORM = "eg4_web_monitor";

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
  accountTier: { domain: "sensor", suffix: "account_tier", names: ["Account Tier"], required: false },
  moduleCount: { domain: "sensor", suffix: "module_count", names: ["Module Count"], required: false },
  ratedArrayPower: { domain: "sensor", suffix: "rated_array_power", names: ["Configured DC Capacity", "Rated Array Power"], required: false },
  pollingInterval: { domain: "sensor", suffix: "polling_interval", names: ["Polling Interval"], required: false },
  integrationVersion: { domain: "sensor", suffix: "integration_version", names: ["Integration Version"], required: false },
  cloudConnected: { domain: "binary_sensor", suffix: "cloud_connected", names: ["Cloud Connected", "API Connectivity"], required: true },
  dataStale: { domain: "binary_sensor", suffix: "data_stale", names: ["Cloud Data Stale", "Data Stale"], required: true },
});

const MODULE_ENTITIES = Object.freeze({
  power: { domain: "sensor", suffix: "power", names: ["Power", "Module Power"], required: true },
  energyToday: { domain: "sensor", suffix: "energy_today", names: ["Energy Today", "Module Energy Today"], required: true },
});

const EG4_COMPARISON_ENTITIES = Object.freeze({
  pvPower: { domain: "sensor", name: "PV Total Power", deviceClass: "power", units: ["W"], stateClasses: ["measurement"] },
  energyToday: { domain: "sensor", name: "Yield", deviceClass: "energy", units: ["kWh"], stateClasses: ["total_increasing"] },
  energyLifetime: { domain: "sensor", name: "Yield (Lifetime)", deviceClass: "energy", units: ["kWh"], stateClasses: ["total", "total_increasing"] },
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

function panelPrefix(label) {
  const match = String(label ?? "").trim().match(/^(.+?)[\s_-]*(\d+)$/);
  return match ? normalize(match[1]) : null;
}

function recoverUnavailableTopology(modules) {
  const groupsByPrefix = new Map();
  for (const module of modules.filter((candidate) => candidate.hasTopology)) {
    const prefix = panelPrefix(module.panelLabel);
    const stringSuffix = normalize(module.stringLabel).match(/([a-z0-9]+)$/)?.[1] ?? null;
    if (!prefix || ["module", "optimizer", "panel", "pv"].includes(prefix) || stringSuffix !== prefix) continue;
    const signature = [module.inverterLabel, module.mpptLabel, module.stringLabel].join("\u0000");
    const matches = groupsByPrefix.get(prefix) ?? new Map();
    matches.set(signature, module);
    groupsByPrefix.set(prefix, matches);
  }
  return modules.map((module) => {
    if (module.hasTopology) return module;
    const matches = groupsByPrefix.get(panelPrefix(module.panelLabel));
    if (matches?.size !== 1) return module;
    const reference = [...matches.values()][0];
    return {
      ...module,
      inverterLabel: reference.inverterLabel,
      mpptLabel: reference.mpptLabel,
      stringLabel: reference.stringLabel,
      hasTopology: true,
    };
  });
}

function displayModules(modules) {
  const inverterLabels = [...new Set(
    modules.filter((module) => module.hasTopology).map((module) => module.inverterLabel),
  )].sort(naturalOrder.compare);
  const aliases = new Map(inverterLabels.map((label, index) => [label, `Inverter ${index + 1}`]));
  const showInverter = inverterLabels.length > 1;
  return modules.map(({ hasTopology, ...module }) => ({
    ...module,
    groupLabel: hasTopology
      ? [showInverter ? aliases.get(module.inverterLabel) : null, module.mpptLabel, module.stringLabel]
        .filter(Boolean)
        .join(" · ")
      : "Unassigned modules",
  }));
}

function serialTokens(value) {
  return new Set(
    String(value ?? "").match(/\d{8,}/g) ?? [],
  );
}

function eg4ModelLabel(value) {
  return /\b18\s*k?pv\b/i.test(String(value ?? "")) ? "18kPV inverter" : "inverter";
}

function exactDeviceSelector(device, selector) {
  if (!selector) return false;
  return [device.id, device.name, device.name_by_user]
    .filter(Boolean)
    .some((value) => normalize(value) === normalize(selector));
}

function validateEg4Device({ device, registry, liveStates }) {
  const entityMap = {};
  for (const [key, specification] of Object.entries(EG4_COMPARISON_ENTITIES)) {
    const matches = registry.filter((entity) =>
      entity.platform === EG4_PLATFORM
      && entity.device_id === device.id
      && entity.disabled_by == null
      && entity.entity_id?.startsWith(`${specification.domain}.`)
      && normalize(entity.original_name) === normalize(specification.name)
    );
    if (matches.length !== 1) {
      return { entities: null, reason: `EG4 ${specification.name} is not uniquely available` };
    }
    const entityId = matches[0].entity_id;
    const state = liveStates.get(entityId);
    const attributes = state?.attributes ?? {};
    if (
      !ENTITY_ID_PATTERN.test(entityId)
      || !state
      || attributes.device_class !== specification.deviceClass
      || !specification.units.includes(attributes.unit_of_measurement)
      || !specification.stateClasses.includes(attributes.state_class)
    ) {
      return { entities: null, reason: `EG4 ${specification.name} metadata is incompatible` };
    }
    entityMap[key] = entityId;
  }
  return { entities: Object.freeze(entityMap), reason: null };
}

function resolveEg4Comparison({ devices, registry, liveStates, modules, selector }) {
  const tigoInverters = new Set(modules.map((module) => normalize(module.inverterLabel)));
  if (tigoInverters.size !== 1) {
    return { comparison: null, reason: "Tigo system spans multiple inverters" };
  }
  const tigoSerials = new Set(modules.flatMap((module) => [...serialTokens(module.inverterLabel)]));
  const eligible = devices
    .filter((device) => device.disabled_by == null && normalize(device.manufacturer) === "eg4 electronics")
    .map((device) => ({ device, validation: validateEg4Device({ device, registry, liveStates }) }))
    .filter(({ validation }) => validation.entities);
  const candidates = eligible.filter(({ device }) => {
    if (selector) return exactDeviceSelector(device, selector);
    if (tigoSerials.size !== 1) return false;
    const deviceSerials = new Set(deviceIdentifiers(device)
      .flatMap(([, identifier]) => [...serialTokens(identifier)]));
    return [...deviceSerials].some((serial) => tigoSerials.has(serial));
  });
  if (candidates.length !== 1) {
    return {
      comparison: null,
      reason: candidates.length === 0
        ? "no serial-matched EG4 inverter with compatible total sensors"
        : "multiple matching EG4 inverters; set EG4_INVERTER_DEVICE_ID",
    };
  }

  const { device, validation } = candidates[0];
  return {
    comparison: {
      provider: "EG4",
      model: eg4ModelLabel(device.model),
      entities: validation.entities,
    },
    reason: null,
  };
}

const naturalOrder = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

export function discoverTigo({ devices, entities, states, selector = "", comparisonSelector = "" }) {
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
  let modules = moduleDevices.map((device) => {
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
    const inverterAttribute = preferredAttribute(attributeSets, "inverter_label");
    const mpptAttribute = preferredAttribute(attributeSets, "mppt_label");
    const stringAttribute = preferredAttribute(attributeSets, "string_label");
    return {
      deviceId: device.id,
      objectId,
      panelLabel,
      inverterLabel: inverterAttribute ?? "Inverter",
      mpptLabel: mpptAttribute,
      stringLabel: stringAttribute ?? "Unassigned string",
      hasTopology: Boolean(mpptAttribute && stringAttribute),
      model: device.model || "TS4 Optimizer",
      entities: Object.freeze(moduleEntities),
    };
  });
  if (modules.length === 0) throw new Error(`No enabled Tigo modules were found for system ${systemId}`);
  modules = displayModules(recoverUnavailableTopology(modules));
  modules.sort((left, right) =>
    naturalOrder.compare(left.groupLabel, right.groupLabel)
    || naturalOrder.compare(left.panelLabel, right.panelLabel)
    || naturalOrder.compare(left.objectId, right.objectId)
  );
  const { comparison, reason: comparisonReason } = resolveEg4Comparison({
    devices,
    registry: entities,
    liveStates,
    modules,
    selector: comparisonSelector,
  });

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
    comparison,
    comparisonReason,
  };
}

export const discoveryContract = Object.freeze({
  platform: PLATFORM,
  system: SYSTEM_ENTITIES,
  module: MODULE_ENTITIES,
  comparison: EG4_COMPARISON_ENTITIES,
});
