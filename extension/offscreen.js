setInterval(() => chrome.runtime.sendMessage({ type: "FLOW_PROVIDER_KEEPALIVE" }).catch(() => {}), 20000);
