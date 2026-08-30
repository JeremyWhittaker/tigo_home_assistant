#!/usr/bin/env node

import { buildDashboard, dashboardMetadata } from "../src/dashboard.mjs";
import {
  applyDashboard,
  createBackup,
  loadBackup,
  planDashboard,
  restoreBackup,
  validateDashboard,
  validateDashboardTemplates,
  verifyDashboard,
} from "../src/deployer.mjs";
import { discoverTigo } from "../src/discovery.mjs";
import { HomeAssistantClient } from "../src/ha-client.mjs";

const COMMANDS = new Set(["preflight", "plan", "deploy", "restore"]);

function usage() {
  return `Usage:
  npm run dashboard -- preflight
  npm run dashboard -- plan
  npm run dashboard -- deploy
  npm run dashboard -- restore BACKUP.json [--force]

Environment:
  HA_BASE_URL             Home Assistant URL
  HA_TOKEN                Long-lived administrator token
  TIGO_HA_TOKEN           Optional token alias (takes precedence)
  TIGO_SYSTEM_DEVICE_ID   Device id or Tigo system id when more than one exists
  EG4_INVERTER_DEVICE_ID  Optional exact EG4 device id/name for the Compare view
  HA_TIMEOUT_MS           Optional request timeout (default: 15000)`;
}

export function parseArguments(argv) {
  if (argv.includes("--help") || argv.includes("-h")) return { command: "help", backupPath: null, force: false };
  const [command, ...rest] = argv;
  if (!COMMANDS.has(command)) throw new Error(`Choose one command: preflight, plan, deploy, or restore\n\n${usage()}`);
  if (command !== "restore") {
    if (rest.length > 0) throw new Error(`${command} does not accept positional arguments`);
    return { command, backupPath: null, force: false };
  }
  const force = rest.includes("--force");
  const unknownOptions = rest.filter((value) => value.startsWith("--") && value !== "--force");
  if (unknownOptions.length > 0) throw new Error(`Unknown restore option: ${unknownOptions[0]}`);
  const positional = rest.filter((value) => !value.startsWith("--"));
  if (positional.length !== 1) throw new Error("restore requires exactly one backup JSON path");
  return { command, backupPath: positional[0], force };
}

function safeLabel(value) {
  return String(value ?? "Tigo Energy").replace(/[\r\n\t]+/g, " ").replace(/\s+/g, " ").slice(0, 80);
}

async function collectPlan(client) {
  const [config, states, devices, entities, dashboards] = await Promise.all([
    client.request("/api/config"),
    client.request("/api/states"),
    client.call({ type: "config/device_registry/list" }),
    client.call({ type: "config/entity_registry/list" }),
    client.call({ type: "lovelace/dashboards/list" }),
  ]);
  const discovery = discoverTigo({
    devices,
    entities,
    states,
    selector: process.env.TIGO_SYSTEM_DEVICE_ID ?? "",
    comparisonSelector: process.env.EG4_INVERTER_DEVICE_ID ?? "",
  });
  const candidate = buildDashboard(discovery);
  const validation = validateDashboard(candidate, states);
  const templateValidation = await validateDashboardTemplates(client, candidate);
  const existing = dashboards.find((dashboard) => dashboard.url_path === dashboardMetadata.urlPath) ?? null;
  const existingConfig = existing?.mode === "storage"
    ? await client.call({ type: "lovelace/config", url_path: dashboardMetadata.urlPath, force: true })
    : null;
  const plan = planDashboard({ existing, existingConfig, candidate, metadata: dashboardMetadata });
  return {
    config,
    states,
    discovery,
    candidate,
    validation,
    templateValidation,
    existing,
    existingConfig,
    plan,
  };
}

function summary(client, context) {
  return [
    `ha=${context.config.version ?? client.version ?? "unknown"}`,
    `system=${JSON.stringify(safeLabel(context.discovery.system.name))}`,
    `modules=${context.discovery.modules.length}`,
    `entities=${context.validation.references.length}`,
    `cards=${context.validation.cardCount}`,
    `views=${context.validation.viewCount}`,
    `comparison=${context.discovery.comparison ? "enabled" : "not-matched"}`,
    `templates=${context.templateValidation.templateCount}`,
    `dashboard=${dashboardMetadata.urlPath}`,
    `action=${context.plan.action}`,
  ].join(" ");
}

async function main() {
  const args = parseArguments(process.argv.slice(2));
  if (args.command === "help") {
    console.log(usage());
    return;
  }
  const client = new HomeAssistantClient({
    baseUrl: process.env.HA_BASE_URL,
    token: process.env.TIGO_HA_TOKEN ?? process.env.HA_TOKEN,
    timeoutMs: Number(process.env.HA_TIMEOUT_MS ?? 15_000),
  });
  await client.connect();
  try {
    const user = await client.call({ type: "auth/current_user" });
    if (!user?.is_admin) throw new Error("The Home Assistant token must belong to an administrator");

    if (args.command === "restore") {
      const backup = loadBackup(args.backupPath);
      const result = await restoreBackup({ ws: client, backup, force: args.force });
      console.log(`restore-ok action=${result.action} dashboard=${backup.dashboard_path}`);
      return;
    }

    const context = await collectPlan(client);
    const details = summary(client, context);
    if (args.command === "preflight") {
      console.log(`preflight-ok ${details}`);
      return;
    }
    if (args.command === "plan") {
      console.log(`plan-ok ${details} config_changed=${context.plan.configChanged} metadata_changed=${context.plan.metadataChanged}`);
      return;
    }
    if (context.plan.action === "unchanged") {
      await verifyDashboard({ ws: client, metadata: dashboardMetadata, candidate: context.candidate });
      console.log(`deployment-ok ${details}`);
      return;
    }

    const { path: backupPath } = createBackup({
      baseUrl: client.baseUrl,
      haVersion: context.config.version ?? client.version,
      metadata: dashboardMetadata,
      existing: context.existing,
      existingConfig: context.existingConfig,
      candidate: context.candidate,
    });
    console.log(`backup=${backupPath}`);
    const result = await applyDashboard({
      ws: client,
      existing: context.existing,
      existingConfig: context.existingConfig,
      candidate: context.candidate,
      metadata: dashboardMetadata,
      verify: () => verifyDashboard({ ws: client, metadata: dashboardMetadata, candidate: context.candidate }),
    });
    console.log(`deployment-ok action=${result.action} dashboard=${dashboardMetadata.urlPath} views=${context.validation.viewCount} entities=${context.validation.references.length}`);
  } finally {
    client.close();
  }
}

const isEntryPoint = process.argv[1] && new URL(import.meta.url).pathname === process.argv[1];
if (isEntryPoint) {
  main().catch((error) => {
    console.error(`ERROR: ${error.message}`);
    process.exitCode = 1;
  });
}
