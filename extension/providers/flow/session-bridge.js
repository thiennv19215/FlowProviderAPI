const FLOW_PROVIDER_FRAME_SESSION_TYPE = "FLOW_PROVIDER_FRAME_SESSION";
const FLOW_PROVIDER_KEEPALIVE_TYPE = "FLOW_PROVIDER_KEEPALIVE";

function isTrustedLabsFrameSender(sender) {
  if (!sender || sender.id !== chrome.runtime.id) return false;
  try {
    const url = new URL(sender.url || sender.tab?.url || "");
    return url.protocol === "https:" && url.hostname === "labs.google";
  } catch (_) {
    return false;
  }
}

function isTrustedOffscreenSender(sender) {
  if (!sender || sender.id !== chrome.runtime.id) return false;
  return sender.url === chrome.runtime.getURL("background/offscreen.html");
}

function publishCapturedSession(token, email = "") {
  const normalizedToken = typeof token === "string" ? token.trim() : "";
  if (!normalizedToken) return false;

  cachedBearer = normalizedToken;
  cachedBearerAt = Date.now();
  if (email) accountState.email = String(email);

  if (socket?.readyState === WebSocket.OPEN) {
    lastAuthSyncAt = Date.now();
    socket.send(JSON.stringify({ type: "token_captured", flowKey: normalizedToken }));
    if (email) {
      socket.send(JSON.stringify({
        type: "user_info",
        userInfo: {
          email: String(email),
          name: "",
          picture: "",
          verified_email: true,
        },
      }));
    }
  }
  return true;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg?.type === FLOW_PROVIDER_FRAME_SESSION_TYPE) {
    if (!isTrustedLabsFrameSender(sender)) {
      sendResponse({ ok: false, error: "untrusted_frame_sender" });
      return false;
    }
    const ok = publishCapturedSession(msg.token, msg.email);
    sendResponse({ ok, error: ok ? undefined : "missing_session_token" });
    return false;
  }

  if (msg?.type === FLOW_PROVIDER_KEEPALIVE_TYPE) {
    if (!isTrustedOffscreenSender(sender)) return false;
    keepAlive().catch(() => {});
    sendResponse({ ok: true });
    return false;
  }

  return false;
});
