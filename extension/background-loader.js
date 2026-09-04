importScripts("config.js");
try { importScripts("config.local.js"); } catch (_) {}
importScripts("offscreen-init.js", "background.js", "session-bridge.js", "browser-transport.js", "chatgpt-provider.js");
