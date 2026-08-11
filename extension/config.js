self.FLOW_PROVIDER_EXTENSION_CONFIG = {
  defaultServerUrl: "http://127.0.0.1:8000",
  protocolVersion: 7,
};

(() => {
  const NativeWebSocket = self.WebSocket;
  if (!NativeWebSocket) return;

  function encodeToken(value) {
    const bytes = new TextEncoder().encode(value);
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  }

  self.WebSocket = class FlowProviderWebSocket extends NativeWebSocket {
    constructor(url, protocols) {
      const parsed = new URL(String(url));
      const match = parsed.pathname.match(/^\/ext\/([^/]+)(\/.*)?$/);
      if (match) {
        const token = decodeURIComponent(match[1]);
        parsed.pathname = match[2] || "/";
        const authProtocol = `flow-token.${encodeToken(token)}`;
        const supplied = protocols == null ? [] : (Array.isArray(protocols) ? protocols : [protocols]);
        super(parsed.toString(), ["flow-provider-v7", authProtocol, ...supplied]);
        return;
      }
      super(url, protocols);
    }
  };
})();
