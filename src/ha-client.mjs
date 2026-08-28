const RETRYABLE_READ_STATUSES = new Set([429, 502, 503, 504]);

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function retryDelay(response, attempt) {
  const retryAfter = Number(response.headers.get("retry-after"));
  if (Number.isFinite(retryAfter) && retryAfter >= 0) return Math.min(retryAfter * 1_000, 10_000);
  return 250 * (2 ** attempt);
}

export class HomeAssistantClient {
  constructor({ baseUrl, token, timeoutMs = 15_000, readRetries = 2 }) {
    if (!baseUrl) throw new Error("Set HA_BASE_URL to the Home Assistant URL");
    if (!token) throw new Error("Set HA_TOKEN or TIGO_HA_TOKEN to a Home Assistant administrator token");
    this.baseUrl = String(baseUrl).replace(/\/$/, "");
    this.token = token;
    this.timeoutMs = timeoutMs;
    this.readRetries = readRetries;
    this.socket = null;
    this.pending = new Map();
    this.nextId = 1;
    this.version = null;
  }

  async request(path, { method = "GET", body, allowNotFound = false, responseType = "json" } = {}) {
    const attempts = method === "GET" ? this.readRetries + 1 : 1;
    let lastError;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      try {
        const response = await fetch(`${this.baseUrl}${path}`, {
          method,
          headers: {
            Authorization: `Bearer ${this.token}`,
            ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          },
          body: body === undefined ? undefined : JSON.stringify(body),
          signal: AbortSignal.timeout(this.timeoutMs),
        });
        if (allowNotFound && response.status === 404) return null;
        if (RETRYABLE_READ_STATUSES.has(response.status) && attempt < attempts - 1) {
          lastError = new Error(`${method} ${path} returned ${response.status}`);
          await wait(retryDelay(response, attempt));
          continue;
        }
        if (!response.ok) {
          const detail = (await response.text()).replace(/\s+/g, " ").slice(0, 300);
          throw new Error(`${method} ${path} failed with ${response.status}${detail ? `: ${detail}` : ""}`);
        }
        const text = await response.text();
        if (responseType === "text") return text;
        if (responseType !== "json") throw new Error(`Unsupported response type: ${responseType}`);
        return text ? JSON.parse(text) : null;
      } catch (error) {
        lastError = error;
        if (attempt >= attempts - 1) break;
        await wait(250 * (2 ** attempt));
      }
    }
    throw new Error(`${method} ${path} failed after ${attempts} attempt(s): ${lastError?.message ?? "unknown error"}`);
  }

  async connect() {
    if (typeof WebSocket !== "function") {
      throw new Error("The dashboard tooling requires Node.js 22 or newer with global WebSocket support");
    }
    const wsUrl = `${this.baseUrl.replace(/^https:/, "wss:").replace(/^http:/, "ws:")}/api/websocket`;
    this.socket = new WebSocket(wsUrl);
    await new Promise((resolve, reject) => {
      let settled = false;
      const finish = (callback, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        callback(value);
      };
      const timer = setTimeout(
        () => finish(reject, new Error("Home Assistant WebSocket authentication timed out")),
        this.timeoutMs,
      );
      this.socket.addEventListener(
        "error",
        () => finish(reject, new Error("Home Assistant WebSocket connection failed")),
        { once: true },
      );
      this.socket.addEventListener("message", (event) => {
        const message = JSON.parse(String(event.data));
        if (message.type === "auth_required") {
          this.version = message.ha_version ?? null;
          this.socket.send(JSON.stringify({ type: "auth", access_token: this.token }));
        } else if (message.type === "auth_ok") {
          this.version = message.ha_version ?? this.version;
          finish(resolve);
        } else if (message.type === "auth_invalid") {
          finish(reject, new Error("Home Assistant rejected the administrator token"));
        }
      });
    });

    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(String(event.data));
      const pending = this.pending.get(message.id);
      if (message.type !== "result" || !pending) return;
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      if (message.success) pending.resolve(message.result);
      else pending.reject(new Error(
        `${message.error?.code ?? "unknown_error"}: ${message.error?.message ?? "WebSocket command failed"}`,
      ));
    });
    this.socket.addEventListener("close", (event) => {
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timer);
        pending.reject(new Error(`Home Assistant WebSocket closed (${event.code})`));
      }
      this.pending.clear();
    });
  }

  call(command) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("Home Assistant WebSocket is not connected"));
    }
    const id = this.nextId;
    this.nextId += 1;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Home Assistant WebSocket command timed out: ${command.type}`));
      }, this.timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.send(JSON.stringify({ id, ...command }));
    });
  }

  close() {
    this.socket?.close();
  }
}
