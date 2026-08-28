#!/usr/bin/env node

import { spawn } from "node:child_process";
import { accessSync, chmodSync, constants, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { dashboardMetadata } from "../src/dashboard.mjs";

const LOAD_TIMEOUT_MS = 60_000;

function pause(milliseconds) {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds));
}

function parseArguments(argv) {
  const result = { outputDir: "/tmp/tigo-energy-visual-qa" };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === "--output-dir") result.outputDir = argv[++index];
    else if (argv[index] === "--help" || argv[index] === "-h") {
      console.log("Usage: npm run qa:visual -- [--output-dir DIR]");
      process.exit(0);
    } else throw new Error(`Unknown argument: ${argv[index]}`);
  }
  if (!result.outputDir) throw new Error("--output-dir requires a directory");
  return result;
}

async function freePort() {
  return new Promise((resolvePromise, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => resolvePromise(address.port));
    });
  });
}

async function waitForJson(url, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(2_000) });
      if (response.ok) return response.json();
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await pause(100);
  }
  throw new Error(`Chromium DevTools endpoint did not become ready: ${lastError?.message ?? "timeout"}`);
}

class CdpSession {
  constructor(url, timeoutMs = 15_000) {
    this.url = url;
    this.timeoutMs = timeoutMs;
    this.socket = null;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
  }

  async connect() {
    this.socket = new WebSocket(this.url);
    await new Promise((resolvePromise, reject) => {
      const timer = setTimeout(() => reject(new Error("CDP WebSocket connection timed out")), this.timeoutMs);
      this.socket.addEventListener("open", () => {
        clearTimeout(timer);
        resolvePromise();
      }, { once: true });
      this.socket.addEventListener("error", () => {
        clearTimeout(timer);
        reject(new Error("CDP WebSocket connection failed"));
      }, { once: true });
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      if (message.id && this.pending.has(message.id)) {
        const pending = this.pending.get(message.id);
        this.pending.delete(message.id);
        clearTimeout(pending.timer);
        if (message.error) pending.reject(new Error(`${message.error.code}: ${message.error.message}`));
        else pending.resolve(message.result);
        return;
      }
      for (const listener of this.listeners.get(message.method) ?? []) listener(message.params ?? {});
    });
  }

  call(method, params = {}) {
    const id = this.nextId;
    this.nextId += 1;
    return new Promise((resolvePromise, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP command timed out: ${method}`));
      }, this.timeoutMs);
      this.pending.set(id, { resolve: resolvePromise, reject, timer });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }

  on(method, listener) {
    const listeners = this.listeners.get(method) ?? [];
    listeners.push(listener);
    this.listeners.set(method, listeners);
  }

  close() {
    this.socket?.close();
  }
}

async function evaluate(session, expression) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const result = await session.call("Runtime.evaluate", {
        expression,
        returnByValue: true,
        awaitPromise: true,
      });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "Browser evaluation failed");
      return result.result.value;
    } catch (error) {
      const navigated = String(error.message).includes("Inspected target navigated or closed")
        || String(error.message).includes("Execution context was destroyed");
      if (!navigated || attempt === 2) throw error;
      await pause(250);
    }
  }
  throw new Error("Browser evaluation retries were exhausted");
}

const INSPECTION = `(() => {
  const tags = [];
  const alerts = [];
  const links = [];
  const visit = (root) => {
    for (const element of root.querySelectorAll('*')) {
      tags.push(element.localName);
      if (['hui-error-card', 'hui-warning', 'ha-alert'].includes(element.localName)) {
        const text = (element.innerText || element.textContent || '').trim();
        if (text) alerts.push(text.slice(0, 500));
      }
      if (element.localName === 'a' && element.href) {
        try { links.push(new URL(element.href, location.href).pathname); } catch {}
      }
      if (element.shadowRoot) visit(element.shadowRoot);
    }
  };
  visit(document);
  return {
    path: location.pathname,
    loginVisible: tags.includes('ha-authorize') || tags.includes('ha-auth-form'),
    hasLovelace: tags.includes('ha-panel-lovelace') && (tags.includes('hui-root') || tags.includes('hui-view')),
    hasSidebarLink: links.some((path) => path === '/${dashboardMetadata.urlPath}' || path.startsWith('/${dashboardMetadata.urlPath}/')),
    errorCount: tags.filter((tag) => tag === 'hui-error-card' || tag === 'hui-warning').length,
    alerts: [...new Set(alerts)],
    renderedCards: tags.filter((tag) => tag.startsWith('hui-')).length,
  };
})()`;

async function waitForDashboard(session, expectedPath) {
  const deadline = Date.now() + LOAD_TIMEOUT_MS;
  let result;
  while (Date.now() < deadline) {
    result = await evaluate(session, INSPECTION);
    if (result.loginVisible) throw new Error("Home Assistant showed a login form after token injection");
    if (result.hasLovelace && result.path === expectedPath && result.renderedCards >= 3) break;
    await pause(500);
  }
  if (!result?.hasLovelace || result.path !== expectedPath) {
    throw new Error(`Dashboard did not render at ${expectedPath}; last path was ${result?.path ?? "unknown"}`);
  }
  await pause(2_000);
  result = await evaluate(session, INSPECTION);
  if (result.errorCount > 0) throw new Error(`Home Assistant rendered ${result.errorCount} dashboard error card(s)`);
  return result;
}

async function scrollDashboard(session, offset) {
  return evaluate(session, `(() => {
    const candidates = [];
    const visit = (root) => {
      for (const element of root.querySelectorAll('*')) {
        const rect = element.getBoundingClientRect();
        const overflow = element.scrollHeight - element.clientHeight;
        if (overflow > 100 && rect.width > 200 && rect.height > 200) candidates.push({ element, score: overflow * rect.width });
        if (element.shadowRoot) visit(element.shadowRoot);
      }
    };
    visit(document);
    candidates.sort((left, right) => right.score - left.score);
    const target = candidates[0]?.element;
    if (!target) {
      window.scrollTo(0, ${Math.max(0, Math.floor(offset))});
      return { target: 'window', top: window.scrollY, height: document.documentElement.scrollHeight, viewport: window.innerHeight };
    }
    target.scrollTop = ${Math.max(0, Math.floor(offset))};
    return { target: target.localName, top: target.scrollTop, height: target.scrollHeight, viewport: target.clientHeight };
  })()`);
}

async function screenshotCase(session, { baseUrl, outputDir, view, viewport, theme }) {
  await session.call("Emulation.setDeviceMetricsOverride", viewport);
  await session.call("Emulation.setEmulatedMedia", {
    media: "screen",
    features: [{ name: "prefers-color-scheme", value: theme }],
  });
  const path = `/${dashboardMetadata.urlPath}/${view}`;
  await session.call("Page.navigate", { url: `${baseUrl}${path}` });
  const firstInspection = await waitForDashboard(session, path);
  const prefix = `${viewport.mobile ? "mobile" : "desktop"}-${view}-${theme}`;
  const segments = [];
  let requestedOffset = 0;
  for (let index = 0; index < 50; index += 1) {
    const scroll = await scrollDashboard(session, requestedOffset);
    await pause(250);
    const inspection = await evaluate(session, INSPECTION);
    if (inspection.errorCount > 0) throw new Error(`Dashboard rendered an error while scrolling ${path}`);
    const capture = await session.call("Page.captureScreenshot", {
      format: "png",
      fromSurface: true,
      captureBeyondViewport: false,
    });
    const filename = `${prefix}-${String(index + 1).padStart(2, "0")}.png`;
    const capturePath = join(outputDir, filename);
    writeFileSync(capturePath, Buffer.from(capture.data, "base64"), { mode: 0o600 });
    chmodSync(capturePath, 0o600);
    segments.push({ filename, scroll, inspection });
    const maximum = Math.max(0, scroll.height - scroll.viewport);
    if (scroll.top >= maximum - 2) break;
    const next = Math.min(maximum, scroll.top + Math.max(200, Math.floor(scroll.viewport * 0.8)));
    if (next <= scroll.top) throw new Error(`Could not advance the scroll container at ${path}`);
    requestedOffset = next;
    if (index === 49) throw new Error(`${path} exceeded the 50-segment visual QA limit`);
  }
  return { view, theme, viewport, firstInspection, segments };
}

async function stopBrowser(browser) {
  if (browser.exitCode != null) return;
  browser.kill("SIGTERM");
  const exited = new Promise((resolvePromise) => browser.once("exit", resolvePromise));
  const stopped = await Promise.race([exited.then(() => true), pause(3_000).then(() => false)]);
  if (!stopped && browser.exitCode == null) browser.kill("SIGKILL");
}

async function main() {
  const args = parseArguments(process.argv.slice(2));
  const baseUrl = String(process.env.HA_BASE_URL ?? "").replace(/\/$/, "");
  const token = process.env.TIGO_HA_TOKEN ?? process.env.HA_TOKEN;
  if (!baseUrl) throw new Error("Set HA_BASE_URL");
  if (!token) throw new Error("Set HA_TOKEN or TIGO_HA_TOKEN");
  const chromium = process.env.CHROMIUM_BIN ?? "/usr/bin/chromium-browser";
  accessSync(chromium, constants.X_OK);
  const outputDir = resolve(args.outputDir);
  mkdirSync(outputDir, { recursive: true, mode: 0o700 });
  chmodSync(outputDir, 0o700);
  const profileDir = mkdtempSync(join(tmpdir(), "tigo-energy-chromium-"));
  const port = await freePort();
  const browser = spawn(chromium, [
    "--headless=new",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profileDir}`,
    "about:blank",
  ], { stdio: ["ignore", "ignore", "ignore"] });
  let session;
  try {
    await waitForJson(`http://127.0.0.1:${port}/json/version`);
    const page = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent("about:blank")}`, { method: "PUT" })
      .then((response) => response.json());
    session = new CdpSession(page.webSocketDebuggerUrl);
    await session.connect();
    await Promise.all([
      session.call("Page.enable"),
      session.call("Runtime.enable"),
      session.call("Log.enable"),
      session.call("Network.enable"),
    ]);
    const browserErrors = [];
    session.on("Runtime.exceptionThrown", ({ exceptionDetails }) => {
      const text = exceptionDetails?.exception?.description ?? exceptionDetails?.text;
      if (text) browserErrors.push({ kind: "exception", text: String(text).slice(0, 500) });
    });
    session.on("Log.entryAdded", ({ entry }) => {
      if (entry?.level === "error" && !String(entry.text).startsWith("Failed to load resource:")) {
        browserErrors.push({ kind: "log", text: String(entry.text).slice(0, 500) });
      }
    });

    await session.call("Page.navigate", { url: `${baseUrl}/` });
    const origin = new URL(baseUrl).origin;
    const deadline = Date.now() + LOAD_TIMEOUT_MS;
    while (Date.now() < deadline && await evaluate(session, "location.origin") !== origin) await pause(250);
    if (await evaluate(session, "location.origin") !== origin) throw new Error("Chromium did not reach the Home Assistant origin");
    const authData = {
      hassUrl: baseUrl,
      clientId: null,
      expires: Date.now() + 1e11,
      refresh_token: "",
      access_token: token,
      expires_in: 1e11,
    };
    await evaluate(session, `localStorage.setItem('hassTokens', ${JSON.stringify(JSON.stringify(authData))}); true`);
    browserErrors.length = 0;

    const desktop = { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false };
    const mobile = { width: 390, height: 844, deviceScaleFactor: 2, mobile: true };
    const cases = ["overview", "energy", "modules", "system"].flatMap((view) =>
      ["light", "dark"].flatMap((theme) => [
        { view, theme, viewport: desktop },
        { view, theme, viewport: mobile },
      ])
    );
    const captures = [];
    for (const captureCase of cases) {
      captures.push(await screenshotCase(session, { baseUrl, outputDir, ...captureCase }));
    }
    const uniqueErrors = [...new Map(browserErrors.map((error) => [JSON.stringify(error), error])).values()];
    const report = {
      checked_at: new Date().toISOString(),
      dashboard_path: dashboardMetadata.urlPath,
      captures,
      browser_errors: uniqueErrors,
    };
    const reportPath = join(outputDir, "report.json");
    writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
    chmodSync(reportPath, 0o600);
    if (uniqueErrors.length > 0) throw new Error(`Browser logged ${uniqueErrors.length} error(s); inspect ${reportPath}`);
    if (!captures.some((capture) => capture.firstInspection.hasSidebarLink)) {
      throw new Error(`Sidebar link for ${dashboardMetadata.urlPath} was not found; inspect ${reportPath}`);
    }
    const screenshots = captures.reduce((count, capture) => count + capture.segments.length, 0);
    console.log(`visual-qa-ok routes=4 cases=${captures.length} screenshots=${screenshots} themes=light+dark report=${reportPath}`);
  } finally {
    session?.close();
    await stopBrowser(browser);
    rmSync(profileDir, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(`ERROR: ${error.message}`);
  process.exitCode = 1;
});
