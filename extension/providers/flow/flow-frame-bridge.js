(() => {
  if (location.protocol !== "https:" || !["labs.google", "flow.google", "flow.google.com"].includes(location.hostname)) return;

  async function publishSession() {
    try {
      // The Labs session endpoint remains browser-owned. Ask the service
      // worker to fetch it from Flow pages without a cross-origin page fetch.
      if (location.hostname !== "labs.google") {
        await chrome.runtime.sendMessage({ type: "FLOW_PROVIDER_REFRESH_SESSION" });
        return;
      }
      const resp = await fetch("https://labs.google/fx/api/auth/session", { credentials: "include" });
      if (!resp.ok) return;
      const session = await resp.json();
      if (!session?.access_token) return;
      await chrome.runtime.sendMessage({
        type: "FLOW_PROVIDER_FRAME_SESSION",
        token: session.access_token,
        email: session?.user?.email || "",
      });
    } catch (_) {}
  }

  publishSession();
  setInterval(publishSession, 4 * 60 * 1000);
})();
