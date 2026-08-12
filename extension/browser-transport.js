const FLOW_AUTH_MODE = "flow";
const FLOW_API_HOST = "aisandbox-pa.googleapis.com";
let capturedFlowApiKey = null;

function normalizeFlowApiKey(value) {
  const key = typeof value === "string" ? value.trim() : "";
  return /^[A-Za-z0-9_-]{20,100}$/.test(key) ? key : null;
}

function apiKeyFromUrl(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.hostname !== FLOW_API_HOST) return null;
    return normalizeFlowApiKey(url.searchParams.get("key"));
  } catch (_) {
    return null;
  }
}

function publishFlowApiKey(value, targetSocket = socket, force = false) {
  const key = normalizeFlowApiKey(value);
  if (!key) return false;
  const changed = key !== capturedFlowApiKey;
  capturedFlowApiKey = key;
  if ((changed || force) && targetSocket?.readyState === WebSocket.OPEN && targetSocket === socket) {
    targetSocket.send(JSON.stringify({ type: "flow_api_key", apiKey: key }));
  }
  return true;
}

async function discoverFlowApiKeyFromOpenTabs() {
  const tabs = await chrome.tabs.query({ url: ["https://labs.google/*", "https://flow.google/*"] });
  for (const tab of tabs) {
    if (!tab.id) continue;
    try {
      const results = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        world: "MAIN",
        func: (host) => {
          const urls = performance.getEntriesByType("resource").map((entry) => entry.name);
          for (const element of document.querySelectorAll("script[src],link[href]")) urls.push(element.src || element.href || "");
          for (const value of urls) {
            try {
              const url = new URL(value, location.href);
              const key = url.hostname === host ? url.searchParams.get("key") : null;
              if (key) return key;
            } catch (_) {}
          }
          return null;
        },
        args: [FLOW_API_HOST],
      });
      const key = normalizeFlowApiKey(results?.[0]?.result);
      if (key) return key;
    } catch (_) {}
  }
  return null;
}

chrome.webRequest.onBeforeRequest.addListener(
  (details) => { publishFlowApiKey(apiKeyFromUrl(details.url)); },
  { urls: [`https://${FLOW_API_HOST}/*`] },
);

async function withBrowserOwnedFlowAuth(spec) {
  const next = { ...(spec || {}) };
  if (next.authMode !== FLOW_AUTH_MODE) return next;
  delete next.authMode;
  const token = await getBearer();
  next.headers = { ...(next.headers || {}), authorization: `Bearer ${token}` };
  return next;
}

const baseHandleRpc = handleRpc;
handleRpc = async function browserOwnedHandleRpc(msg, signal) {
  if ((msg?.type === "SW_FETCH" || msg?.type === "INJECT_PAGE_FETCH") && msg?.spec?.authMode === FLOW_AUTH_MODE) {
    return baseHandleRpc({ ...msg, spec: await withBrowserOwnedFlowAuth(msg.spec) }, signal);
  }
  return baseHandleRpc(msg, signal);
};

syncAuth = async function browserOwnedSyncAuth(targetSocket = socket) {
  if (!targetSocket || targetSocket.readyState !== WebSocket.OPEN || targetSocket !== socket) return;
  try {
    const session = await fetchLabsSession();
    if (targetSocket !== socket || targetSocket.readyState !== WebSocket.OPEN) return;
    lastAuthSyncAt = Date.now();
    targetSocket.send(JSON.stringify({ type: "auth_available" }));
    const apiKey = capturedFlowApiKey || await discoverFlowApiKeyFromOpenTabs();
    if (apiKey) publishFlowApiKey(apiKey, targetSocket, true);
    if (session.user) {
      accountState.email = session.user.email || null;
      targetSocket.send(JSON.stringify({
        type: "user_info",
        userInfo: {
          email: session.user.email || "",
          name: session.user.name || "",
          picture: session.user.image || "",
          verified_email: true,
        },
      }));
    }
  } catch (error) {
    if (targetSocket === socket && targetSocket.readyState === WebSocket.OPEN) {
      targetSocket.send(JSON.stringify({ type: "auth_sync_status", status: "needs_labs_sign_in", reason: error?.message || String(error) }));
    }
  }
};

publishCapturedSession = function browserOwnedPublishCapturedSession(token, email = "") {
  const normalizedToken = typeof token === "string" ? token.trim() : "";
  if (!normalizedToken) return false;
  cachedBearer = normalizedToken;
  cachedBearerAt = Date.now();
  if (email) accountState.email = String(email);
  if (socket?.readyState === WebSocket.OPEN) {
    lastAuthSyncAt = Date.now();
    socket.send(JSON.stringify({ type: "auth_available" }));
    if (capturedFlowApiKey) publishFlowApiKey(capturedFlowApiKey, socket, true);
    if (email) {
      socket.send(JSON.stringify({ type: "user_info", userInfo: { email: String(email), name: "", picture: "", verified_email: true } }));
    }
  }
  return true;
};
