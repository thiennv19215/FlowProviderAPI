import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../background.js', import.meta.url), 'utf8');

function buildHarness(initialStorage = {}) {
  const sockets = [];
  const fetchSignals = [];
  const storage = { ...initialStorage };

  class MockWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 3;
    constructor(url, protocols = []) {
      this.url = url;
      this.protocols = protocols;
      this.readyState = MockWebSocket.CONNECTING;
      this.sent = [];
      sockets.push(this);
    }
    send(payload) { this.sent.push(JSON.parse(payload)); }
    close() { this.readyState = MockWebSocket.CLOSED; this.onclose?.(); }
  }

  const chrome = {
    storage: { local: {
      get: async (keys) => {
        if (typeof keys === 'string') return { [keys]: storage[keys] };
        if (Array.isArray(keys)) return Object.fromEntries(keys.map((key) => [key, storage[key]]));
        return { ...storage };
      },
      set: async (values) => Object.assign(storage, values),
    } },
    permissions: { contains: async () => true, request: async () => true },
    identity: { getProfileUserInfo: async () => ({ email: '' }) },
    runtime: {
      getManifest: () => ({ version: '1.0.0' }),
      onMessage: { addListener: () => {} },
      onInstalled: { addListener: () => {} },
      onStartup: { addListener: () => {} },
    },
    tabs: {
      query: async () => [],
      get: async () => null,
      create: async () => ({ id: 1 }),
    },
    scripting: { executeScript: async () => [{ result: { ok: true, data: 'token' } }] },
    declarativeNetRequest: { updateDynamicRules: async () => {} },
  };

  const context = {
    self: {}, chrome, WebSocket: MockWebSocket,
    URL, AbortController, Uint8Array, TextEncoder,
    btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
    setTimeout, clearTimeout, setInterval: () => 1, clearInterval: () => {}, console, Date, Math,
    crypto: globalThis.crypto,
    navigator: {},
    fetch: (_url, options = {}) => new Promise((_resolve, reject) => {
      const signal = options.signal;
      fetchSignals.push(signal);
      if (signal?.aborted) return reject(new Error('AbortError'));
      signal?.addEventListener('abort', () => reject(new Error('AbortError')), { once: true });
    }),
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'background.js' });
  return { context, sockets, fetchSignals, storage };
}

async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
}

test('connect sends gateway auth as websocket subprotocol without putting the token in the URL', async () => {
  const h = buildHarness({
    'flow-provider-server-url-v1': 'https://provider.example.com',
    'flow-provider-gateway-token-v1': 'secret-value',
  });
  await flush();
  assert.equal(h.sockets.length, 1);
  const ws = h.sockets[0];
  assert.equal(ws.url, 'wss://provider.example.com/api/extensions/ws');
  assert.ok(ws.protocols.includes('flow-provider-v7'));
  assert.ok(ws.protocols.some((value) => value.startsWith('flow-token.')));
  assert.equal(ws.url.includes('secret-value'), false);
});

test('legacy /ext/token server setting is migrated to sanitized server plus token storage', async () => {
  const h = buildHarness({ 'flow-provider-server-url-v1': 'https://provider.example.com/ext/old-secret' });
  await flush();
  assert.equal(h.storage['flow-provider-server-url-v1'], 'https://provider.example.com');
  assert.equal(h.storage['flow-provider-gateway-token-v1'], 'old-secret');
  assert.equal(h.sockets[0].url, 'wss://provider.example.com/api/extensions/ws');
});

test('CANCEL_RPC aborts an in-flight SW_FETCH', async () => {
  const h = buildHarness();
  await flush();
  const ws = h.sockets[0];
  ws.readyState = h.context.WebSocket.OPEN;

  const rpcPromise = ws.onmessage({ data: JSON.stringify({
    id: 'rpc-1',
    type: 'SW_FETCH',
    spec: { url: 'https://aisandbox-pa.googleapis.com/v1/test', method: 'GET', timeoutMs: 60000 },
  }) });
  await flush();
  assert.equal(h.fetchSignals.length, 1);
  assert.equal(h.fetchSignals[0].aborted, false);

  await ws.onmessage({ data: JSON.stringify({ type: 'CANCEL_RPC', targetRequestId: 'rpc-1' }) });
  await rpcPromise;
  assert.equal(h.fetchSignals[0].aborted, true);
  assert.ok(ws.sent.some((frame) => frame.id === 'rpc-1' && typeof frame.error === 'string'));
});

test('legacy unused RPC handlers are removed', () => {
  assert.equal(source.includes('ENSURE_TAB'), false);
  assert.equal(source.includes('RELOAD_TAB'), false);
  assert.equal(source.includes('DOWNLOAD_FILE'), false);
  assert.equal(source.includes('ensureOffscreen'), false);
});
