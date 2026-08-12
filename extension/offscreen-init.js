async function ensureFlowProviderOffscreen() {
  if (!chrome.offscreen?.createDocument) return;
  try {
    if (chrome.runtime.getContexts) {
      const contexts = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"], documentUrls: [chrome.runtime.getURL("offscreen.html")] });
      if (contexts.length) return;
    }
    await chrome.offscreen.createDocument({
      url: "offscreen.html",
      reasons: ["WORKERS"],
      justification: "Keep the Flow Provider browser bridge and in-memory auth cache available without opening a visible Flow tab."
    });
  } catch (error) {
    if (!String(error?.message || error).toLowerCase().includes("single offscreen")) {
      console.warn("Flow Provider offscreen setup failed", error);
    }
  }
}

ensureFlowProviderOffscreen().catch(() => {});
chrome.runtime.onInstalled.addListener(() => ensureFlowProviderOffscreen().catch(() => {}));
chrome.runtime.onStartup.addListener(() => ensureFlowProviderOffscreen().catch(() => {}));
