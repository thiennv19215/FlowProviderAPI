let offscreenCreating = null;

async function ensureFlowProviderOffscreen() {
  if (!chrome.offscreen?.createDocument) return;
  if (offscreenCreating) return offscreenCreating;
  offscreenCreating = (async () => {
    try {
      if (chrome.runtime.getContexts) {
        const contexts = await chrome.runtime.getContexts({
          contextTypes: ["OFFSCREEN_DOCUMENT"],
          documentUrls: [chrome.runtime.getURL("core/offscreen.html")],
        });
        if (contexts.length) return;
      }
      await chrome.offscreen.createDocument({
        url: "core/offscreen.html",
        reasons: ["IFRAME_SCRIPTING"],
        justification: "Host a hidden Google Flow frame so the connector can warm and observe the signed-in Flow session without opening a visible tab.",
      });
    } catch (error) {
      const message = String(error?.message || error).toLowerCase();
      if (!message.includes("single offscreen") && !message.includes("already exists")) {
        console.warn("Flow Provider offscreen setup failed", error);
      }
    } finally {
      offscreenCreating = null;
    }
  })();
  return offscreenCreating;
}

ensureFlowProviderOffscreen().catch(() => {});
chrome.runtime.onInstalled.addListener(() => ensureFlowProviderOffscreen().catch(() => {}));
chrome.runtime.onStartup.addListener(() => ensureFlowProviderOffscreen().catch(() => {}));
