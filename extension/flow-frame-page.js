(() => {
  if (window.top === window) return;
  if (location.hostname !== "labs.google") return;

  async function executeRecaptcha(payload = {}) {
    let siteKey = payload.fallbackKey || "";
    for (const script of document.querySelectorAll('script[src*="recaptcha"]')) {
      const match = script.src.match(/[?&]render=([^&]+)/);
      if (match && match[1] !== "explicit") { siteKey = decodeURIComponent(match[1]); break; }
    }
    if (!siteKey) throw new Error("recaptcha_site_key_missing");
    const deadline = Date.now() + 30000;
    return await new Promise((resolve, reject) => {
      const check = () => {
        if (Date.now() > deadline) return reject(new Error("recaptcha_timeout"));
        if (globalThis.grecaptcha?.enterprise) {
          grecaptcha.enterprise.ready(() => {
            grecaptcha.enterprise.execute(siteKey, { action: payload.action || "IMAGE_GENERATION" }).then(resolve).catch(reject);
          });
          return;
        }
        setTimeout(check, 300);
      };
      check();
    });
  }

  window.addEventListener("message", async (event) => {
    const msg = event.data;
    if (event.source !== window || msg?.source !== "flow-provider-frame-bridge" || msg?.type !== "FLOW_PROVIDER_RECAPTCHA_EXECUTE") return;
    try {
      const token = await executeRecaptcha(msg.payload || {});
      window.postMessage({ source: "flow-provider-page", type: "FLOW_PROVIDER_RECAPTCHA_RESULT", requestId: msg.requestId, ok: true, token }, "*");
    } catch (error) {
      window.postMessage({ source: "flow-provider-page", type: "FLOW_PROVIDER_RECAPTCHA_RESULT", requestId: msg.requestId, ok: false, error: error?.message || String(error) }, "*");
    }
  });
})();
