const KEEPALIVE_MS = 20 * 1000;
let cachedToken = null;
let cachedUntil = 0;

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "FLOW_PROVIDER_TOKEN_CACHE_GET") {
    const valid = cachedToken && Date.now() < cachedUntil;
    sendResponse({ token: valid ? cachedToken : null });
    return false;
  }
  if (msg?.type === "FLOW_PROVIDER_TOKEN_CACHE_SET") {
    cachedToken = msg.token || null;
    cachedUntil = cachedToken ? Date.now() + Math.max(1000, Number(msg.ttlMs || 120000)) : 0;
    sendResponse({ ok: true });
    return false;
  }
  if (msg?.type === "FLOW_PROVIDER_TOKEN_CACHE_CLEAR") {
    cachedToken = null;
    cachedUntil = 0;
    sendResponse({ ok: true });
    return false;
  }
  return false;
});

setInterval(() => {
  chrome.runtime.sendMessage({ type: "FLOW_PROVIDER_KEEPALIVE" }).catch(() => {});
}, KEEPALIVE_MS);
chrome.runtime.sendMessage({ type: "FLOW_PROVIDER_KEEPALIVE" }).catch(() => {});
