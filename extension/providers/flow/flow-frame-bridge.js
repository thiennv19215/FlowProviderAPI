(() => {
  if (window.top === window) return;
  if (location.hostname !== "labs.google") return;

  async function publishSession() {
    try {
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
