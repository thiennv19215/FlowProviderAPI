importScripts("config.js");
try { importScripts("config.local.js"); } catch (_) {}
importScripts(
  "core/offscreen-init.js",
  "core/background.js",
  "providers/flow/session-bridge.js",
  "providers/flow/browser-transport.js",
  "providers/chatgpt/chatgpt-provider.js"
);
