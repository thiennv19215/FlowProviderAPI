const KEEPALIVE_MS = 20 * 1000;

function pingServiceWorker() {
  chrome.runtime.sendMessage({ type: "FLOW_PROVIDER_KEEPALIVE" }).catch(() => {});
}

setInterval(pingServiceWorker, KEEPALIVE_MS);
pingServiceWorker();
