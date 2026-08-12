const CONFIG = self.FLOW_PROVIDER_EXTENSION_CONFIG || {};
const PROTOCOL_VERSION = Number(CONFIG.protocolVersion || 7);
const SERVER_KEY = "flow-provider-server-url-v1";
const SERVER_DEFAULT_VERSION_KEY = "flow-provider-server-default-version-v1";
const INSTALLATION_KEY = "flow-provider-installation-id-v1";
const PROFILE_KEY = "flow-provider-profile-id-v1";
const LABS_SESSION_URL = "https://labs.google/fx/api/auth/session";
const FLOW_HOME_URL = "https://labs.google/fx/vi/tools/flow";
const ALLOWED_FETCH_HOSTS = ["labs.google", "aisandbox-pa.googleapis.com", "flow-content.google", "storage.googleapis.com"];
const AUTH_REFRESH_MS = 5 * 60 * 1000;

let socket = null;
let reconnectTimer = null;
let reconnectAttempt = 0;
let cachedBearer = null;
let cachedBearerAt = 0;
let lastAuthSyncAt = 0;
let accountState = { email: null, credits: null, ready: false };
let authSyncInFlight = null;
const inflightRpcControllers = new Map();

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

async function openFlowHome() {
  const tabs = await chrome.tabs.query({ url: ["https://labs.google/fx/*tools/flow*", "https://flow.google/*"] });
  const existing = tabs.find((t) => t.id && !String(t.url || "").includes("/project/"));
  if (existing?.id) { await waitForTab(existing.id); return { tabId: existing.id, isNew: false }; }
  const tab = await chrome.tabs.create({ url: FLOW_HOME_URL, active: false });
  await waitForTab(tab.id);
  return { tabId: tab.id, isNew: true };
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
          const out = { ok: resp.ok, status: resp.status, finalUrl: resp.url };
          if (type === "json") out.data = await resp.json().catch(() => null);
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
  try {
    const resp = await fetch(spec.url, { method: spec.method || "GET", headers: spec.headers || {}, body: spec.body, credentials: "include", signal: controller.signal });
    const type = spec.responseType || ((resp.headers.get("content-type") || "").includes("json") ? "json" : "text");
    const out = { ok: resp.ok, status: resp.status, finalUrl: resp.url };
    if (type === "json") out.data = await resp.json().catch(() => null);
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
  return { serverUrl: config.serverUrl, connected: socket?.readyState === WebSocket.OPEN, account: accountState, version: chrome.runtime.getManifest().version };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  const delay = Math.min(1000 * 2 ** reconnectAttempt, 15000);
  reconnectAttempt += 1;
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
    const server = new URL(config.serverUrl);
    server.protocol = server.protocol === "https:" ? "wss:" : "ws:";
    server.pathname = `${server.pathname.replace(/\/$/, "")}/api/extensions/ws`;
    const ws = new WebSocket(server.toString(), ["flow-provider-v7"]);
    socket = ws;
    ws.onopen = async () => {
      reconnectAttempt = 0;
      const meta = await getProfileMeta();
      if (ws !== socket || ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({ type: "extension_ready", installationId: await getInstallationId(), protocolVersion: PROTOCOL_VERSION, connectionId: id("conn"), ...meta }));
      await syncAuth(ws);
    };
    ws.onmessage = async (event) => {
      if (ws !== socket) return;
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "auth_sync_ack") {
          accountState = { email: msg.email || accountState.email, credits: Number.isFinite(msg.credits) ? msg.credits : accountState.credits, ready: msg.status === "synced" };
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
          inflightRpcControllers.set(String(msg.id), controller);
          try { ws.send(JSON.stringify({ id: msg.id, data: await handleRpc(msg, controller.signal) })); }
          catch (error) { if (ws === socket && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ id: msg.id, error: error?.message || String(error) })); }
          finally { inflightRpcControllers.delete(String(msg.id)); }
        }
      } catch (error) { console.error("Flow Provider message error", error); }
    };
    ws.onclose = () => { if (ws === socket) { socket = null; accountState.ready = false; scheduleReconnect(); } };
    ws.onerror = () => {};
  } catch (error) {
    console.warn("Flow Provider connect failed", error?.message || error);
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
  } catch (error) { console.warn("DNR setup failed", error); }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "FLOW_PROVIDER_GET_STATE") { connectionState().then(sendResponse); return true; }
  if (msg?.type === "FLOW_PROVIDER_SET_SERVER") { setConnectionConfig(msg.serverUrl).then((serverUrl) => sendResponse({ ok: true, serverUrl })).catch((e) => sendResponse({ ok: false, error: e.message })); return true; }
  if (msg?.type === "FLOW_PROVIDER_OPEN_FLOW") { openFlowHome().then((v) => sendResponse({ ok: true, ...v })).catch((e) => sendResponse({ ok: false, error: e.message })); return true; }
  return false;
});

chrome.runtime.onInstalled.addListener(() => { setupDnr(); connect(); });
chrome.runtime.onStartup.addListener(() => { setupDnr(); connect(); });
setupDnr();
connect();
