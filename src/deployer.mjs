import { createHash } from "node:crypto";
import {
  chmodSync,
  closeSync,
  mkdtempSync,
  openSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BACKUP_SCHEMA = "tigo-energy-dashboard-backup/1";
const ALLOWED_TYPES = new Set([
  "sections", "grid", "heading", "markdown", "tile", "gauge", "history-graph",
  "statistics-graph", "entities", "entity-filter", "entity", "conditional",
]);
const ENTITY_REFERENCE_PATTERN = /\b[a-z_][a-z0-9_]*\.[a-z0-9_]+\b/g;
const CONTROL_DOMAINS = new Set([
  "automation", "button", "input_boolean", "input_button", "input_datetime", "input_number",
  "input_select", "input_text", "lock", "number", "remote", "script", "select", "switch", "time",
]);
const WRITE_ACTIONS = new Set(["call-service", "perform-action", "toggle"]);
const METADATA_FIELDS = ["title", "icon", "show_in_sidebar", "require_admin"];

export function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stableValue(value[key])]));
  }
  return value;
}

export function stableString(value) {
  return JSON.stringify(stableValue(value));
}

export function checksum(value) {
  return createHash("sha256").update(stableString(value)).digest("hex");
}

export function collectEntityReferences(value, references = new Set()) {
  if (typeof value === "string") {
    for (const match of value.matchAll(ENTITY_REFERENCE_PATTERN)) references.add(match[0]);
  } else if (Array.isArray(value)) {
    for (const child of value) collectEntityReferences(child, references);
  } else if (value && typeof value === "object") {
    for (const child of Object.values(value)) collectEntityReferences(child, references);
  }
  return references;
}

export function collectDashboardTemplates(value, templates = []) {
  if (typeof value === "string") {
    if (value.includes("{{") || value.includes("{%")) templates.push(value);
  } else if (Array.isArray(value)) {
    for (const child of value) collectDashboardTemplates(child, templates);
  } else if (value && typeof value === "object") {
    for (const child of Object.values(value)) collectDashboardTemplates(child, templates);
  }
  return templates;
}

export async function validateDashboardTemplates(client, config) {
  const templates = collectDashboardTemplates(config);
  for (const template of templates) {
    await client.request("/api/template", {
      method: "POST",
      body: { template },
      responseType: "text",
    });
  }
  return { templateCount: templates.length };
}

export function validateDashboard(config, liveStates) {
  if (!config || !Array.isArray(config.views) || config.views.length === 0) {
    throw new Error("Dashboard must contain at least one view");
  }
  if (!Array.isArray(liveStates)) throw new TypeError("liveStates must be an array");
  const paths = config.views.map((view) => view.path);
  if (paths.some((path) => typeof path !== "string" || path.length === 0)) {
    throw new Error("Every dashboard view needs a path");
  }
  if (new Set(paths).size !== paths.length) throw new Error("Dashboard view paths must be unique");
  if (config.views.some((view) => view.type !== "sections")) {
    throw new Error("Every dashboard view must use Home Assistant's responsive Sections layout");
  }

  const liveIds = new Set(liveStates.map((state) => state.entity_id));
  const references = collectEntityReferences(config);
  const missing = [...references].filter((entityId) => !liveIds.has(entityId));
  if (missing.length > 0) throw new Error(`Dashboard references missing live entities: ${missing.join(", ")}`);
  const controlled = [...references].filter((entityId) => CONTROL_DOMAINS.has(entityId.split(".", 1)[0]));
  if (controlled.length > 0) {
    throw new Error(`Read-only dashboard must not reference control entities: ${controlled.join(", ")}`);
  }

  let cardCount = 0;
  function inspect(value) {
    if (Array.isArray(value)) {
      for (const child of value) inspect(child);
      return;
    }
    if (!value || typeof value !== "object") return;
    if (typeof value.type === "string") {
      cardCount += 1;
      if (value.type.startsWith("custom:") || !ALLOWED_TYPES.has(value.type)) {
        throw new Error(`Dashboard uses unsupported card or layout type: ${value.type}`);
      }
    }
    if (typeof value.action === "string" && WRITE_ACTIONS.has(value.action)) {
      throw new Error(`Read-only dashboard contains a mutating action: ${value.action}`);
    }
    for (const child of Object.values(value)) inspect(child);
  }
  inspect(config);
  return { references: [...references].sort(), cardCount, viewCount: config.views.length };
}

function metadataPayload(metadata) {
  return {
    title: metadata.title,
    icon: metadata.icon,
    show_in_sidebar: metadata.showInSidebar,
    require_admin: metadata.requireAdmin,
  };
}

function metadataMatches(current, desired) {
  const expected = metadataPayload(desired);
  return METADATA_FIELDS.every((field) => (current?.[field] ?? null) === (expected[field] ?? null));
}

export function planDashboard({ existing, existingConfig, candidate, metadata }) {
  if (existing && existing.mode !== "storage") {
    throw new Error(`Dashboard ${metadata.urlPath} exists in ${existing.mode} mode; refusing to replace it`);
  }
  if (!existing) return { action: "create", configChanged: true, metadataChanged: true };
  const configChanged = stableString(existingConfig) !== stableString(candidate);
  const metadataChanged = !metadataMatches(existing, metadata);
  return {
    action: configChanged || metadataChanged ? "update" : "unchanged",
    configChanged,
    metadataChanged,
  };
}

export function createBackup({ baseUrl, haVersion, metadata, existing, existingConfig, candidate }) {
  const directory = mkdtempSync(join(tmpdir(), "tigo-energy-dashboard-"));
  chmodSync(directory, 0o700);
  const path = join(directory, "backup.json");
  const backup = {
    schema: BACKUP_SCHEMA,
    created_at: new Date().toISOString(),
    home_assistant: { base_url: baseUrl, version: haVersion },
    dashboard_path: metadata.urlPath,
    prior: existing
      ? { metadata: existing, config: existingConfig, checksum: checksum(existingConfig) }
      : null,
    deployed: {
      metadata: metadataPayload(metadata),
      config: candidate,
      checksum: checksum(candidate),
    },
  };
  const descriptor = openSync(path, "wx", 0o600);
  try {
    writeFileSync(descriptor, `${JSON.stringify(backup, null, 2)}\n`, { encoding: "utf8" });
  } finally {
    closeSync(descriptor);
  }
  return { path, backup };
}

async function restorePrior(ws, { createdId, existing, existingConfig }) {
  if (!existing) {
    if (createdId) await ws.call({ type: "lovelace/dashboards/delete", dashboard_id: createdId });
    return;
  }
  await ws.call({ type: "lovelace/config/save", url_path: existing.url_path, config: existingConfig });
  await ws.call({
    type: "lovelace/dashboards/update",
    dashboard_id: existing.id,
    title: existing.title,
    icon: existing.icon ?? null,
    show_in_sidebar: existing.show_in_sidebar,
    require_admin: existing.require_admin,
  });
}

export async function applyDashboard({ ws, existing, existingConfig, candidate, metadata, verify }) {
  const plan = planDashboard({ existing, existingConfig, candidate, metadata });
  if (plan.action === "unchanged") {
    if (verify) await verify();
    return { ...plan, dashboardId: existing.id };
  }
  let createdId = null;
  try {
    if (!existing) {
      const created = await ws.call({
        type: "lovelace/dashboards/create",
        url_path: metadata.urlPath,
        mode: "storage",
        ...metadataPayload(metadata),
      });
      createdId = created.id;
      await ws.call({ type: "lovelace/config/save", url_path: metadata.urlPath, config: candidate });
      if (verify) await verify();
      return { ...plan, dashboardId: createdId };
    }
    if (plan.configChanged) {
      await ws.call({ type: "lovelace/config/save", url_path: metadata.urlPath, config: candidate });
    }
    if (plan.metadataChanged) {
      await ws.call({
        type: "lovelace/dashboards/update",
        dashboard_id: existing.id,
        ...metadataPayload(metadata),
      });
    }
    if (verify) await verify();
    return { ...plan, dashboardId: existing.id };
  } catch (error) {
    try {
      await restorePrior(ws, { createdId, existing, existingConfig });
    } catch (rollbackError) {
      throw new Error(`${error.message}; automatic rollback also failed: ${rollbackError.message}`, { cause: error });
    }
    throw new Error(`${error.message}; automatic rollback completed`, { cause: error });
  }
}

export async function verifyDashboard({ ws, metadata, candidate }) {
  const dashboards = await ws.call({ type: "lovelace/dashboards/list" });
  const current = dashboards.find((dashboard) => dashboard.url_path === metadata.urlPath);
  if (!current || current.mode !== "storage") {
    throw new Error("Dashboard was not registered in storage mode after deployment");
  }
  if (!metadataMatches(current, metadata)) throw new Error("Dashboard metadata did not round-trip exactly");
  const currentConfig = await ws.call({
    type: "lovelace/config",
    url_path: metadata.urlPath,
    force: true,
  });
  if (stableString(currentConfig) !== stableString(candidate)) {
    throw new Error("Dashboard configuration did not round-trip exactly");
  }
  return { dashboard: current, config: currentConfig };
}

export function loadBackup(path) {
  const backup = JSON.parse(readFileSync(path, "utf8"));
  if (backup.schema !== BACKUP_SCHEMA || !backup.dashboard_path || !backup.deployed?.config) {
    throw new Error("Backup is not a Tigo Energy dashboard backup/1 document");
  }
  if (checksum(backup.deployed.config) !== backup.deployed.checksum) {
    throw new Error("Backup deployed config checksum does not match");
  }
  if (backup.prior && checksum(backup.prior.config) !== backup.prior.checksum) {
    throw new Error("Backup prior config checksum does not match");
  }
  return backup;
}

function backupMetadataMatches(current, expected) {
  return METADATA_FIELDS.every((field) => (current?.[field] ?? null) === (expected?.[field] ?? null));
}

export async function restoreBackup({ ws, backup, force = false }) {
  const dashboards = await ws.call({ type: "lovelace/dashboards/list" });
  const current = dashboards.find((dashboard) => dashboard.url_path === backup.dashboard_path);
  let currentConfig = null;
  if (current?.mode === "storage") {
    currentConfig = await ws.call({ type: "lovelace/config", url_path: backup.dashboard_path, force: true });
  }
  const deployedMatches = current
    && current.mode === "storage"
    && checksum(currentConfig) === backup.deployed.checksum
    && backupMetadataMatches(current, backup.deployed.metadata);
  const priorMatches = backup.prior
    && current
    && current.mode === "storage"
    && checksum(currentConfig) === backup.prior.checksum
    && backupMetadataMatches(current, backup.prior.metadata);
  if (priorMatches) return { action: "already-restored" };
  if (!deployedMatches && !force) {
    throw new Error("Current dashboard has drifted since this backup; refusing to overwrite it without --force");
  }
  if (!backup.prior) {
    if (!current) return { action: "already-restored" };
    if (current.mode !== "storage") {
      throw new Error("Current same-path dashboard is not storage mode; refusing to delete it");
    }
    await ws.call({ type: "lovelace/dashboards/delete", dashboard_id: current.id });
    return { action: "deleted-created-dashboard" };
  }
  if (!current || current.mode !== "storage") {
    throw new Error("Cannot restore prior dashboard because the storage dashboard is missing");
  }
  const prior = backup.prior.metadata;
  await ws.call({ type: "lovelace/config/save", url_path: backup.dashboard_path, config: backup.prior.config });
  await ws.call({
    type: "lovelace/dashboards/update",
    dashboard_id: current.id,
    title: prior.title,
    icon: prior.icon ?? null,
    show_in_sidebar: prior.show_in_sidebar,
    require_admin: prior.require_admin,
  });
  return { action: "restored-prior-dashboard" };
}
