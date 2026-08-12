const FLOW_AUTH_MODE = "flow";

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
    if (email) {
      socket.send(JSON.stringify({ type: "user_info", userInfo: { email: String(email), name: "", picture: "", verified_email: true } }));
    }
  }
  return true;
};
