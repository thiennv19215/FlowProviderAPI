const CONFIG = self.FLOW_PROVIDER_EXTENSION_CONFIG || {};
const PROTOCOL_VERSION = Number(CONFIG.protocolVersion || 7);
const SERVER_KEY = "flow-provider-server-url-v1";
const SERVER_DEFAULT_VERSION_KEY = "flow-provider-server-default-version-v1";
const INSTALLATION_KEY = "flow-provider-installation-id-v1";
const PROFILE_KEY = "flow-provider-profile-id-v1";
const SIMULATION_MODE_KEY = "flow-provider-simulation-mode-v1";
const FLOW_TAB_ID_KEY = "flow-provider-flow-tab-id-v1";
const LABS_SESSION_URL = "https://labs.google/fx/api/auth/session";
const FLOW_HOME_URL = "https://labs.google/fx/vi/tools/flow";
const ALLOWED_FETCH_HOSTS = ["labs.google", "aisandbox-pa.googleapis.com", "flow-content.google", "storage.googleapis.com"];
const AUTH_REFRESH_MS = 5 * 60 * 1000;
const FLOW_TAB_OPEN_COOLDOWN_MS = 60 * 1000;
const MAX_ACTIVITY_LOGS = 50;
const MAX_LOG_DETAIL_CHARS = 240;
const SENSITIVE_LOG_KEY = /authorization|cookie|token|secret|api.?key|body|base64|image/i;

let socket = null;
let reconnectTimer = null;
let reconnectAttempt = 0;
let cachedBearer = null;
let cachedBearerAt = 0;
let lastAuthSyncAt = 0;
let accountState = { email: null, credits: null, ready: false };
let authSyncInFlight = null;
let lastFlowTabOpenAttemptAt = 0;
const inflightRpcControllers = new Map();
let activityState = { activeCount: 0, current: null, logs: [] };

function sanitizeLogValue(value, key = "", depth = 0) {
  if (SENSITIVE_LOG_KEY.test(key)) return "[redacted]";
  if (value == null || typeof value === "number" || typeof value === "boolean") return value;
  if (typeof value === "string") {
    let text = value
      .replace(/Bearer\s+[A-Za-z0-9._~-]+/gi, "Bearer [redacted]")
      .replace(/([?&](?:key|token|secret|api_key)=)[^&\s]+/gi, "$1[redacted]");
    try {
      const url = new URL(text);
      if (["http:", "https:", "ws:", "wss:"].includes(url.protocol)) {
        text = url.pathname || "/";
      }
    } catch (_) {
      text = text.replace(/\b(?:https?|wss?):\/\/[^\s"'<>]+/gi, (candidate) => {
        try {
          const url = new URL(candidate);
          return url.pathname || "/";
        } catch (_) {
          return "[url]";
        }
      });
    }
    text = text.replace(/\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b/gi, "[domain]");
    return text.slice(0, MAX_LOG_DETAIL_CHARS);
  }
  if (depth >= 2) return "[object]";
  if (Array.isArray(value)) {
    return value.slice(0, 10).map((item) => sanitizeLogValue(item, key, depth + 1));
  }
  if (typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .slice(0, 20)
        .map(([childKey, childValue]) => [
          childKey,
          sanitizeLogValue(childValue, childKey, depth + 1),
        ]),
    );
  }
  return String(value).slice(0, MAX_LOG_DETAIL_CHARS);
}

function extensionLog(level, event, details = null) {
  const prefix = `[Flow Provider ${chrome.runtime.getManifest().version}] ${event}`;
  const safeDetails = details == null ? null : sanitizeLogValue(details);
  const writer = level === "error" ? console.error : level === "warn" ? console.warn : console.info;
  if (safeDetails == null || safeDetails === "") writer.call(console, prefix);
  else writer.call(console, prefix, safeDetails);
}

function activityLabel(message) {
  if (message.type === "OPEN_FLOW_TAB") return "Preparing Flow";
  if (message.type === "INJECT_RECAPTCHA") return "Solving captcha";
  if (message.type === "INJECT_PAGE_FETCH") return "Calling Flow";
  if (message.type === "GET_BEARER") return "Refreshing session";
  if (message.type === "SW_FETCH" && String(message.spec?.url || "").includes("/credits")) return "Refreshing credits";
  if (message.type === "SW_FETCH") return "Calling provider";
  return String(message.type || "Provider request").replaceAll("_", " ").toLowerCase();
}

function appendActivity(label, status, detail = null) {
  const safeLabel = sanitizeLogValue(label);
  const safeDetail = detail == null ? null : sanitizeLogValue(detail);
  activityState.logs = [{ at: Date.now(), label: safeLabel, status, detail: safeDetail }, ...activityState.logs].slice(0, MAX_ACTIVITY_LOGS);
  extensionLog(status === "error" ? "error" : status === "running" ? "info" : "info", safeLabel, safeDetail);
}

function beginActivity(message) {
  const activity = { label: activityLabel(message), startedAt: Date.now() };
  activityState.activeCount += 1;
  activityState.current = activity;
  appendActivity(activity.label, "running");
  return activity;
}

function finishActivity(activity, error = null) {
  activityState.activeCount = Math.max(0, activityState.activeCount - 1);
  if (!activityState.activeCount) activityState.current = null;
  const durationMs = Math.max(0, Date.now() - activity.startedAt);
  appendActivity(activity.label, error ? "error" : "done", error ? String(error).slice(0, 160) : `${durationMs} ms`);
}

function id(prefix = "id") {
  return `${prefix}_${globalThis.crypto?.randomUUID?.() || `${Date.now()}_${Math.random().toString(36).slice(2)}`}`;
}

function normalizeServerUrl(value) {
  const url = new URL(String(value || "").trim());
  const local = ["localhost", "127.0.0.1", "::1"].includes(url.hostname);
  if (url.protocol !== "https:" && !(local && url.protocol === "http:")) {
    throw new Error("Provider server must use HTTPS (HTTP is allowed only for localhost).")
  }
  const legacy = url.pathname.match(/^\/ext\/([^/]+)(\/.*)?$/);
  if (legacy) {
    url.pathname = legacy[2] || "/";
  }
  url.hash = "";
  url.pathname = url.pathname.replace(/\/+$/, "");
  url.search = "";
  return { serverUrl: url.toString().replace(/\/$/, "") };
}

async function getConnectionConfig() {
  // Remove the shared credential left by versions <= 1.0.2. The connector is
  // intentionally credential-free from 1.0.3 onward.
  await chrome.storage.local.remove("flow-provider-gateway-token-v1");
  const data = await chrome.storage.local.get([SERVER_KEY, SERVER_DEFAULT_VERSION_KEY]);
  const defaultServerUrl = CONFIG.defaultServerUrl || "http://127.0.0.1:8000";
  const defaultServerVersion = Number(CONFIG.defaultServerVersion || 0);
  const storedServerValue = typeof data?.[SERVER_KEY] === "string" ? data[SERVER_KEY].trim() : "";
  const storedServer = storedServerValue ? normalizeServerUrl(storedServerValue) : null;
  const legacyDefaultServers = Array.isArray(CONFIG.legacyDefaultServerUrls)
    ? CONFIG.legacyDefaultServerUrls.map((value) => normalizeServerUrl(value).serverUrl)
    : [];
  const migrateStoredDefault = Number(data?.[SERVER_DEFAULT_VERSION_KEY] || 0) < defaultServerVersion
    && storedServer
    && legacyDefaultServers.includes(storedServer.serverUrl);
  const parsed = normalizeServerUrl(migrateStoredDefault || !storedServer ? defaultServerUrl : storedServer.serverUrl);
  const updates = {};
  if (!storedServer || migrateStoredDefault || storedServerValue !== parsed.serverUrl) updates[SERVER_KEY] = parsed.serverUrl;
  if (Number(data?.[SERVER_DEFAULT_VERSION_KEY] || 0) < defaultServerVersion) updates[SERVER_DEFAULT_VERSION_KEY] = defaultServerVersion;
  if (Object.keys(updates).length) await chrome.storage.local.set(updates);
  return { serverUrl: parsed.serverUrl };
}

async function setConnectionConfig(serverValue) {
  const parsed = normalizeServerUrl(serverValue);
  const origin = `${new URL(parsed.serverUrl).origin}/*`;
  const granted = await chrome.permissions.contains({ origins: [origin] }) || await chrome.permissions.request({ origins: [origin] });
  if (!granted) throw new Error(`Permission was not granted for ${origin}`);
  const updates = { [SERVER_KEY]: parsed.serverUrl, [SERVER_DEFAULT_VERSION_KEY]: Number(CONFIG.defaultServerVersion || 0) };
  await chrome.storage.local.set(updates);
  disconnect();
  connect();
  return parsed.serverUrl;
}

async function getInstallationId() {
  const data = await chrome.storage.local.get(INSTALLATION_KEY);
  if (typeof data?.[INSTALLATION_KEY] === "string") return data[INSTALLATION_KEY];
  const value = id("install");
  await chrome.storage.local.set({ [INSTALLATION_KEY]: value });
  return value;
}

async function getProfileMeta() {
  const data = await chrome.storage.local.get(PROFILE_KEY);
  let profileId = data?.[PROFILE_KEY];
  if (typeof profileId !== "string") {
    profileId = id("profile");
    await chrome.storage.local.set({ [PROFILE_KEY]: profileId });
  }
  const runtimeId = globalThis.navigator?.brave ? "brave" : "chrome";
  let email = "";
  try { email = (await chrome.identity.getProfileUserInfo({ accountStatus: "ANY" }))?.email || ""; } catch (_) {}
  return { runtimeId, profileId, profileName: email || "Browser extension" };
}

async function getSimulationMode() {
  const data = await chrome.storage.local.get(SIMULATION_MODE_KEY);
  return data?.[SIMULATION_MODE_KEY] === true;
}

async function setSimulationMode(enabled) {
  const simulationMode = enabled === true;
  await chrome.storage.local.set({ [SIMULATION_MODE_KEY]: simulationMode });
  if (socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "simulation_mode_changed", simulationMode }));
  }
  appendActivity(simulationMode ? "Simulation mode enabled" : "Simulation mode disabled", "done");
  return simulationMode;
}

function allowedFetchUrl(value) {
  try {
    const u = new URL(value);
    return u.protocol === "https:" && ALLOWED_FETCH_HOSTS.some((host) => u.hostname === host || u.hostname.endsWith(`.${host}`));
  } catch (_) { return false; }
}

async function fetchLabsSession() {
  const resp = await fetch(LABS_SESSION_URL, { credentials: "include" });
  if (!resp.ok) throw new Error(`labs_session_http_${resp.status}`);
  const session = await resp.json();
  if (!session?.access_token) throw new Error("labs_access_token_missing");
  let profileEmail = "";
  try { profileEmail = (await chrome.identity.getProfileUserInfo({ accountStatus: "ANY" }))?.email || ""; } catch (_) {}
  const sessionEmail = session?.user?.email || "";
  if (profileEmail && sessionEmail && profileEmail.toLowerCase() !== sessionEmail.toLowerCase()) throw new Error("browser_profile_email_mismatch");
  cachedBearer = session.access_token;
  cachedBearerAt = Date.now();
  return session;
}

async function getBearer({ force = false } = {}) {
  if (!force && cachedBearer && Date.now() - cachedBearerAt < 120000) return cachedBearer;
  return (await fetchLabsSession()).access_token;
}

async function syncAuth(targetSocket = socket) {
  if (!targetSocket || targetSocket.readyState !== WebSocket.OPEN || targetSocket !== socket) return;
  if (authSyncInFlight?.socket === targetSocket) return authSyncInFlight.promise;

  const entry = { socket: targetSocket, promise: null };
  entry.promise = (async () => {
    try {
      const session = await fetchLabsSession();
      if (targetSocket !== socket || targetSocket.readyState !== WebSocket.OPEN) return;
      lastAuthSyncAt = Date.now();
      targetSocket.send(JSON.stringify({ type: "token_captured", flowKey: session.access_token }));
      if (session.user) {
        accountState.email = session.user.email || null;
        targetSocket.send(JSON.stringify({ type: "user_info", userInfo: { email: session.user.email || "", name: session.user.name || "", picture: session.user.image || "", verified_email: true } }));
      }
    } catch (error) {
      if (targetSocket === socket && targetSocket.readyState === WebSocket.OPEN) {
        targetSocket.send(JSON.stringify({ type: "auth_sync_status", status: "needs_labs_sign_in", reason: error?.message || String(error) }));
      }
    }
  })();
  authSyncInFlight = entry;
  try {
    return await entry.promise;
  } finally {
    if (authSyncInFlight === entry) authSyncInFlight = null;
  }
}

async function waitForTab(tabId, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const tab = await chrome.tabs.get(tabId).catch(() => null);
    if (!tab) throw new Error("flow_tab_closed");
    if (tab.status === "complete" && /^https:\/\/(labs|flow)\.google\//.test(tab.url || "")) return tab;
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error("flow_tab_timeout");
}

let openFlowHomeInFlight = null;

async function findOrOpenFlowHome() {
  const tabs = await chrome.tabs.query({ url: ["https://labs.google/fx/*tools/flow*", "https://flow.google/*"] });
  const existing = tabs
    .filter((tab) => tab.id)
    .sort((left, right) => Number(right.status === "complete") - Number(left.status === "complete")
      || Number(right.active) - Number(left.active)
      || Number(right.lastAccessed || 0) - Number(left.lastAccessed || 0))[0];
  if (existing?.id) {
    await chrome.storage.local.set({ [FLOW_TAB_ID_KEY]: existing.id });
    appendActivity("Flow tab reused", "done", `tab ${existing.id}`);
    await waitForTab(existing.id);
    return { tabId: existing.id, isNew: false };
  }

  const stored = await chrome.storage.local.get(FLOW_TAB_ID_KEY);
  const trackedTabId = Number(stored?.[FLOW_TAB_ID_KEY]);
  if (Number.isInteger(trackedTabId) && trackedTabId > 0) {
    const tracked = await chrome.tabs.get(trackedTabId).catch(() => null);
    if (tracked) {
      appendActivity("Flow tab reused", "done", `tab ${trackedTabId}`);
      await waitForTab(trackedTabId);
      return { tabId: trackedTabId, isNew: false };
    }
    await chrome.storage.local.remove(FLOW_TAB_ID_KEY);
  }

  const normalWindows = await chrome.windows.getAll({ windowTypes: ["normal"] }).catch(() => []);
  const targetWindow = normalWindows
    .filter((window) => window.id)
    .sort((left, right) => Number(right.focused) - Number(left.focused))[0];
  let tab;
  if (targetWindow?.id) {
    tab = await chrome.tabs.create({
      windowId: targetWindow.id,
      url: FLOW_HOME_URL,
      active: false,
    });
  } else {
    const createdWindow = await chrome.windows.create({
      url: FLOW_HOME_URL,
      focused: false,
      type: "normal",
    });
    tab = createdWindow.tabs?.[0];
  }
  if (!tab?.id) throw new Error("flow_tab_create_failed");
  await chrome.storage.local.set({ [FLOW_TAB_ID_KEY]: tab.id });
  appendActivity("Flow tab opened", "done", `tab ${tab.id}`);
  await waitForTab(tab.id);
  return { tabId: tab.id, isNew: true };
}

async function openFlowHome({ respectCooldown = false } = {}) {
  if (openFlowHomeInFlight) return await openFlowHomeInFlight;
  const now = Date.now();
  if (respectCooldown && now - lastFlowTabOpenAttemptAt < FLOW_TAB_OPEN_COOLDOWN_MS) {
    throw new Error("flow_tab_open_cooldown");
  }
  lastFlowTabOpenAttemptAt = now;
  const pending = findOrOpenFlowHome();
  openFlowHomeInFlight = pending;
  try {
    return await pending;
  } finally {
    if (openFlowHomeInFlight === pending) openFlowHomeInFlight = null;
  }
}

async function inject(tabId, operation, payload = {}) {
  await waitForTab(tabId);
  const results = await chrome.scripting.executeScript({
    target: { tabId }, world: "MAIN", args: [{ operation, payload }],
    func: async ({ operation, payload }) => {
      if (operation === "recaptcha") {
        let siteKey = payload.fallbackKey;
        for (const script of document.querySelectorAll('script[src*="recaptcha"]')) {
          const match = script.src.match(/[?&]render=([^&]+)/);
          if (match && match[1] !== "explicit") { siteKey = match[1]; break; }
        }
        if (!siteKey) throw new Error("recaptcha_site_key_missing");
        const token = await new Promise((resolve, reject) => {
          const deadline = Date.now() + 30000;
          const check = () => {
            if (Date.now() > deadline) return reject(new Error("recaptcha_timeout"));
            if (globalThis.grecaptcha?.enterprise) grecaptcha.enterprise.ready(() => grecaptcha.enterprise.execute(siteKey, { action: payload.action || "IMAGE_GENERATION" }).then(resolve).catch(reject));
            else setTimeout(check, 400);
          };
          check();
        });
        return { ok: true, data: token };
      }
      if (operation === "pageFetch") {
        const spec = payload.spec || {};
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), spec.timeoutMs || 45000);
        try {
          const resp = await fetch(spec.url, { method: spec.method || "GET", headers: spec.headers || {}, body: spec.body, credentials: "include", signal: controller.signal });
          const type = spec.responseType || ((resp.headers.get("content-type") || "").includes("json") ? "json" : "text");
          const out = { ok: resp.ok, status: resp.status, finalUrl: resp.url, headers: Object.fromEntries(resp.headers.entries()) };
          if (type === "json") {
            const text = await resp.text().catch(() => "");
            try { out.data = text ? JSON.parse(text) : null; }
            catch (_) { out.text = text; }
          }
          else if (type !== "none") out.text = await resp.text().catch(() => "");
          return { ok: true, data: out };
        } finally { clearTimeout(timer); }
      }
      throw new Error(`unsupported_injection:${operation}`);
    },
  });
  const result = results?.[0]?.result;
  if (!result?.ok) throw new Error(result?.error || "injection_failed");
  return result.data;
}

async function swFetch(spec, signal) {
  if (!spec || !allowedFetchUrl(spec.url)) throw new Error("fetch_host_not_allowed");
  const controller = new AbortController();
  const abortFromCaller = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", abortFromCaller, { once: true });
  }
  const timer = setTimeout(() => controller.abort(), spec.timeoutMs || 45000);
  const startedAt = Date.now();
  const requestLabel = `${spec.method || "GET"} ${sanitizeLogValue(spec.url)}`;
  try {
    const resp = await fetch(spec.url, { method: spec.method || "GET", headers: spec.headers || {}, body: spec.body, credentials: "include", signal: controller.signal });
    extensionLog(resp.ok ? "info" : "warn", "Fetch completed", {
      request: requestLabel,
      status: resp.status,
      durationMs: Date.now() - startedAt,
    });
    const type = spec.responseType || ((resp.headers.get("content-type") || "").includes("json") ? "json" : "text");
    const out = { ok: resp.ok, status: resp.status, finalUrl: resp.url, headers: Object.fromEntries(resp.headers.entries()) };
    if (type === "json") {
      const text = await resp.text().catch(() => "");
      try { out.data = text ? JSON.parse(text) : null; }
      catch (_) { out.text = text; }
    }
    else if (type === "base64") {
      const bytes = new Uint8Array(await resp.arrayBuffer());
      let binary = "";
      for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      out.base64 = btoa(binary);
    } else if (type !== "none") out.text = await resp.text().catch(() => "");
    return out;
  } finally {
    clearTimeout(timer);
    if (signal) signal.removeEventListener("abort", abortFromCaller);
  }
}

async function handleRpc(msg, signal) {
  switch (msg.type) {
    case "PING": return { version: chrome.runtime.getManifest().version };
    case "GET_BEARER": return await getBearer({ force: Boolean(msg.force) });
    case "OPEN_FLOW_TAB": return await openFlowHome();
    case "INJECT_RECAPTCHA": return await inject(msg.tabId, "recaptcha", { fallbackKey: msg.fallbackKey, action: msg.action });
    case "INJECT_PAGE_FETCH": return await inject(msg.tabId, "pageFetch", { spec: msg.spec });
    case "SW_FETCH": return await swFetch(msg.spec, signal);
    default: throw new Error(`unknown_rpc_type:${msg.type}`);
  }
}

async function connectionState() {
  const config = await getConnectionConfig();
  return { serverUrl: config.serverUrl, connected: socket?.readyState === WebSocket.OPEN, account: accountState, activity: activityState, simulationMode: await getSimulationMode(), version: chrome.runtime.getManifest().version };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  const delay = Math.min(1000 * 2 ** reconnectAttempt, 15000);
  reconnectAttempt += 1;
  appendActivity("Backend reconnect scheduled", "running", `${delay} ms`);
  reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, delay);
}

function disconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = null;
  const oldSocket = socket;
  socket = null;
  if (oldSocket) { try { oldSocket.close(); } catch (_) {} }
  cachedBearer = null;
  cachedBearerAt = 0;
  lastAuthSyncAt = 0;
  for (const controller of inflightRpcControllers.values()) controller.abort();
  inflightRpcControllers.clear();
}

async function connect() {
  if (socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(socket.readyState)) return;
  try {
    const config = await getConnectionConfig();
    const [installationId, simulationMode, meta] = await Promise.all([
      getInstallationId(), getSimulationMode(), getProfileMeta(),
    ]);
    const connectorApiKey = String(CONFIG.connectorApiKey || "").trim();
    const server = new URL(config.serverUrl);
    server.protocol = server.protocol === "https:" ? "wss:" : "ws:";
    server.pathname = `${server.pathname.replace(/\/$/, "")}/api/extensions/ws`;
    appendActivity("Backend connecting", "running", `WebSocket · protocol v${PROTOCOL_VERSION}`);
    const ws = new WebSocket(server.toString(), ["flow-provider-v7"]);
    socket = ws;
    ws.onopen = () => {
      reconnectAttempt = 0;
      appendActivity("Backend connected", "done", `protocol v${PROTOCOL_VERSION}`);
      if (ws !== socket || ws.readyState !== WebSocket.OPEN) return;
      // Complete the backend handshake before waiting for Google Flow. A Flow
      // page can take longer than the backend hello timeout or redirect to a
      // sign-in page; neither case should force a reconnect/open-tab loop.
      ws.send(JSON.stringify({
        type: "extension_ready",
        installationId,
        protocolVersion: PROTOCOL_VERSION,
        connectionId: id("conn"),
        simulationMode,
        ...(connectorApiKey ? { connectorApiKey } : {}),
        ...meta,
      }));
      void openFlowHome({ respectCooldown: true }).catch((error) => {
        appendActivity("Flow tab unavailable", "error", error?.message || error);
      });
      void syncAuth(ws);
    };
    ws.onmessage = async (event) => {
      if (ws !== socket) return;
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "auth_sync_ack") {
          accountState = { email: msg.email || accountState.email, credits: Number.isFinite(msg.credits) ? msg.credits : accountState.credits, ready: msg.status === "synced" };
          appendActivity(
            "Account synchronized",
            accountState.ready ? "done" : "error",
            Number.isFinite(accountState.credits) ? `${accountState.credits} credits` : "account ready",
          );
          return;
        }
        if (msg.type === "please_resend_userinfo") { await syncAuth(ws); return; }
        if (msg.type === "CANCEL_RPC") {
          const controller = inflightRpcControllers.get(msg.targetRequestId);
          if (controller) controller.abort();
          return;
        }
        if (msg.id != null) {
          const controller = new AbortController();
          const activity = beginActivity(msg);
          inflightRpcControllers.set(String(msg.id), controller);
          try {
            ws.send(JSON.stringify({ id: msg.id, data: await handleRpc(msg, controller.signal) }));
            finishActivity(activity);
          }
          catch (error) {
            const message = error?.message || String(error);
            finishActivity(activity, message);
            if (ws === socket && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ id: msg.id, error: message }));
          }
          finally { inflightRpcControllers.delete(String(msg.id)); }
        }
      } catch (error) { appendActivity("Backend message failed", "error", error?.message || error); }
    };
    ws.onclose = (event) => { if (ws === socket) { socket = null; accountState.ready = false; appendActivity("Backend disconnected", "error", `code ${event?.code || 0}`); scheduleReconnect(); } };
    ws.onerror = () => { extensionLog("warn", "Backend socket error"); };
  } catch (error) {
    appendActivity("Backend connection failed", "error", error?.message || error);
    scheduleReconnect();
  }
}

async function keepAlive() {
  if (socket?.readyState === WebSocket.OPEN) {
    if (Date.now() - lastAuthSyncAt >= AUTH_REFRESH_MS) await syncAuth(socket);
    return;
  }
  await connect();
}

async function setupDnr() {
  try {
    await chrome.declarativeNetRequest.updateDynamicRules({ removeRuleIds: [1201, 1202], addRules: [
      { id: 1201, priority: 1, action: { type: "modifyHeaders", requestHeaders: [{ header: "origin", operation: "set", value: "https://labs.google" }, { header: "referer", operation: "set", value: "https://labs.google/" }] }, condition: { urlFilter: "aisandbox-pa.googleapis.com", excludedInitiatorDomains: ["labs.google"], resourceTypes: ["xmlhttprequest"] } },
      { id: 1202, priority: 1, action: { type: "modifyHeaders", requestHeaders: [{ header: "origin", operation: "set", value: "https://labs.google" }, { header: "referer", operation: "set", value: "https://labs.google/fx/tools/flow" }] }, condition: { urlFilter: "labs.google/fx/api/trpc/", excludedInitiatorDomains: ["labs.google"], resourceTypes: ["xmlhttprequest"] } }
    ] });
  } catch (error) { appendActivity("Network rules failed", "error", error?.message || error); }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "FLOW_PROVIDER_GET_STATE") { connectionState().then(sendResponse); return true; }
  if (msg?.type === "FLOW_PROVIDER_CLEAR_LOGS") { activityState.logs = []; sendResponse({ ok: true }); return true; }
  if (msg?.type === "FLOW_PROVIDER_SET_SIMULATION_MODE") { setSimulationMode(msg.enabled).then((simulationMode) => sendResponse({ ok: true, simulationMode })).catch((e) => sendResponse({ ok: false, error: e.message })); return true; }
  if (msg?.type === "FLOW_PROVIDER_SET_SERVER") { setConnectionConfig(msg.serverUrl).then((serverUrl) => sendResponse({ ok: true, serverUrl })).catch((e) => sendResponse({ ok: false, error: e.message })); return true; }
  if (msg?.type === "FLOW_PROVIDER_OPEN_FLOW") { openFlowHome().then((v) => sendResponse({ ok: true, ...v })).catch((e) => sendResponse({ ok: false, error: e.message })); return true; }
  return false;
});

chrome.runtime.onInstalled.addListener(() => { setupDnr(); connect(); });
chrome.runtime.onStartup.addListener(() => { setupDnr(); connect(); });
setupDnr();
connect();
