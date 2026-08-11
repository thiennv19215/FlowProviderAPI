import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../background.js', import.meta.url), 'utf8');

function buildHarness() {
  let alarmListener = null;
  const sockets = [];
  const fetchSignals = [];

  class MockWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSED = 3;
    constructor(url) {
      this.url = url;
      this.readyState = MockWebSocket.CONNECTING;
      this.sent = [];
      sockets.push(this);
    }
    send(payload) { this.sent.push(JSON.parse(payload)); }
    close() { this.readyState = MockWebSocket.CLOSED; this.onclose?.(); }
  }

  const chrome = {
    storage: { local: { get: async () => ({}), set: async () => {} } },
    permissions: { contains: async () => true, request: async () => true },
    identity: { getProfileUserInfo: async () => ({ email: '' }) },
    runtime: {
      getURL: (p) => `chrome-extension://test/${p}`,
      getManifest: () => ({ version: '1.0.0' }),
      getContexts: async () => [{ contextType: 'OFFSCREEN_DOCUMENT' }],
      onMessage: { addListener: () => {} },
      onInstalled: { addListener: () => {} },
      onStartup: { addListener: () => {} },
    },
    offscreen: { createDocument: async () => {} },
    tabs: {
      query: async () => [],
      get: async () => null,
      create: async () => ({ id: 1 }),
      reload: async () => {},
    },
    scripting: { executeScript: async () => [{ result: { ok: true, data: 'token' } }] },
    downloads: { download: async () => 1 },
    declarativeNetRequest: { updateDynamicRules: async () => {} },
    alarms: {
      create: () => {},
      clear: async () => {},
      onAlarm: { addListener: (fn) => { alarmListener = fn; } },
    },
  };

  const context = {
    self: {}, chrome, WebSocket: MockWebSocket,
    URL, AbortController, Uint8Array, btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
    setTimeout, clearTimeout, console, Date, Math,
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
  return { context, sockets, fetchSignals, getAlarmListener: () => alarmListener };
}

async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
}

test('keepalive alarm sends a websocket heartbeat instead of being a no-op', async () => {
  const h = buildHarness();
  await flush();
  assert.equal(h.sockets.length, 1);
  const ws = h.sockets[0];
  ws.readyState = h.context.WebSocket.OPEN;
  const alarm = h.getAlarmListener();
  assert.equal(typeof alarm, 'function');
  alarm({ name: 'flow-provider-keepalive' });
  await flush();
  assert.ok(ws.sent.some((frame) => frame.type === 'pong'), 'expected pong heartbeat frame');
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
