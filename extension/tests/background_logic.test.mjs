import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../background.js', import.meta.url), 'utf8');
const configContext = { self: {} };
vm.runInNewContext(fs.readFileSync(new URL('../config.js', import.meta.url), 'utf8'), configContext);
const productionConfig = JSON.parse(JSON.stringify(configContext.self.FLOW_PROVIDER_EXTENSION_CONFIG));

function buildHarness(initialStorage = {}, { fetchImpl = null, extensionConfig = null } = {}) {
  const sockets = [];
  const fetchSignals = [];
  const consoleEvents = [];
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
      remove: async (keys) => {
        for (const key of Array.isArray(keys) ? keys : [keys]) delete storage[key];
      },
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
    windows: {
      getAll: async () => [{ id: 1, focused: true }],
      create: async () => ({ id: 1, tabs: [{ id: 1 }] }),
    },
    scripting: { executeScript: async () => [{ result: { ok: true, data: 'token' } }] },
    declarativeNetRequest: { updateDynamicRules: async () => {} },
  };

  const context = {
    self: extensionConfig ? { FLOW_PROVIDER_EXTENSION_CONFIG: extensionConfig } : {}, chrome, WebSocket: MockWebSocket,
    URL, AbortController, Uint8Array, TextEncoder,
    btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
    setTimeout, clearTimeout, setInterval: () => 1, clearInterval: () => {},
    console: {
      info: (...args) => consoleEvents.push({ level: 'info', args }),
      warn: (...args) => consoleEvents.push({ level: 'warn', args }),
      error: (...args) => consoleEvents.push({ level: 'error', args }),
      log: (...args) => consoleEvents.push({ level: 'log', args }),
    },
    Date, Math,
    crypto: globalThis.crypto,
    navigator: {},
    fetch: fetchImpl || ((_url, options = {}) => new Promise((_resolve, reject) => {
      const signal = options.signal;
      fetchSignals.push(signal);
      if (signal?.aborted) return reject(new Error('AbortError'));
      signal?.addEventListener('abort', () => reject(new Error('AbortError')), { once: true });
    })),
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'background.js' });
  return { context, sockets, fetchSignals, storage, consoleEvents };
}

async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
}

test('connect uses only the versioned websocket protocol and removes legacy gateway storage', async () => {
  const h = buildHarness({
    'flow-provider-server-url-v1': 'https://provider.example.com',
    'flow-provider-gateway-token-v1': 'secret-value',
  });
  await flush();
  assert.equal(h.sockets.length, 1);
  const ws = h.sockets[0];
  assert.equal(ws.url, 'wss://provider.example.com/api/extensions/ws');
  assert.deepEqual(Array.from(ws.protocols), ['flow-provider-v7']);
  assert.equal(ws.url.includes('secret-value'), false);
  assert.equal(h.storage['flow-provider-gateway-token-v1'], undefined);
});

test('legacy /ext/token server setting is sanitized without migrating its token', async () => {
  const h = buildHarness({ 'flow-provider-server-url-v1': 'https://provider.example.com/ext/old-secret' });
  await flush();
  assert.equal(h.storage['flow-provider-server-url-v1'], 'https://provider.example.com');
  assert.equal(h.storage['flow-provider-gateway-token-v1'], undefined);
  assert.equal(h.sockets[0].url, 'wss://provider.example.com/api/extensions/ws');
});

test('extension source and popup expose no gateway-token configuration', () => {
  const popupHtml = fs.readFileSync(new URL('../popup.html', import.meta.url), 'utf8');
  const popupJs = fs.readFileSync(new URL('../popup.js', import.meta.url), 'utf8');
  assert.equal(source.includes('gatewayToken'), false);
  assert.equal(source.includes('flow-token.'), false);
  assert.equal(popupHtml.toLowerCase().includes('gateway token'), false);
  assert.equal(popupJs.includes('gatewayToken'), false);
});

test('browser transport preserves the full plain-text body when JSON parsing fails', () => {
  assert.match(source, /const text = await resp\.text\(\)\.catch\(\(\) => ""\);/);
  assert.match(source, /out\.text = text;/);
});

test('popup hides provider endpoint configuration', () => {
  const popupHtml = fs.readFileSync(new URL('../popup.html', import.meta.url), 'utf8');
  const popupJs = fs.readFileSync(new URL('../popup.js', import.meta.url), 'utf8');
  assert.equal(popupHtml.includes('Provider server'), false);
  assert.equal(popupHtml.includes('id="server"'), false);
  assert.equal(popupHtml.includes('id="save"'), false);
  assert.equal(popupJs.includes('FLOW_PROVIDER_SET_SERVER'), false);
  assert.equal(popupJs.includes('serverUrl'), false);
});

test('configured production default replaces the previous public default once', async () => {
  const h = buildHarness({
    'flow-provider-server-url-v1': 'https://ext.shopcongngheso5.io.vn',
    'flow-provider-server-default-version-v1': 1,
  }, {
    extensionConfig: productionConfig,
  });
  await flush();
  assert.equal(h.storage['flow-provider-server-url-v1'], 'https://api.shopcongngheso5.io.vn');
  assert.equal(h.storage['flow-provider-server-default-version-v1'], 2);
  assert.equal(h.sockets[0].url, 'wss://api.shopcongngheso5.io.vn/api/extensions/ws');
});

test('configured production migration preserves an explicit custom server', async () => {
  const h = buildHarness({ 'flow-provider-server-url-v1': 'https://custom.example.com' }, {
    extensionConfig: productionConfig,
  });
  await flush();
  assert.equal(h.storage['flow-provider-server-url-v1'], 'https://custom.example.com');
  assert.equal(h.storage['flow-provider-server-default-version-v1'], 2);
  assert.equal(h.sockets[0].url, 'wss://custom.example.com/api/extensions/ws');
});

test('simulation mode is persisted and announced to the connected provider', async () => {
  const h = buildHarness();
  await flush();
  const ws = h.sockets[0];
  ws.readyState = h.context.WebSocket.OPEN;

  const enabled = await vm.runInContext('setSimulationMode(true)', h.context);
  const state = await vm.runInContext('connectionState()', h.context);

  assert.equal(enabled, true);
  assert.equal(h.storage['flow-provider-simulation-mode-v1'], true);
  assert.equal(state.simulationMode, true);
  assert.ok(ws.sent.some((frame) => frame.type === 'simulation_mode_changed' && frame.simulationMode === true));
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

test('openFlowHome reuses an existing project tab instead of opening another tab', async () => {
  const h = buildHarness();
  await flush();
  let createCalls = 0;
  h.context.chrome.tabs.query = async () => [{
    id: 42,
    url: 'https://labs.google/fx/vi/tools/flow/project/example',
    status: 'complete',
    active: true,
  }];
  h.context.chrome.tabs.get = async (tabId) => ({
    id: tabId,
    url: 'https://labs.google/fx/vi/tools/flow/project/example',
    status: 'complete',
  });
  h.context.chrome.tabs.create = async () => {
    createCalls += 1;
    return { id: 99 };
  };

  const result = await vm.runInContext('openFlowHome()', h.context);
  assert.equal(result.tabId, 42);
  assert.equal(result.isNew, false);
  assert.equal(createCalls, 0);
});

test('a successful backend connection opens one inactive Flow tab when none exists', async () => {
  const h = buildHarness();
  await flush();
  let createCalls = 0;
  h.context.chrome.tabs.query = async () => [];
  h.context.chrome.tabs.create = async (options) => {
    createCalls += 1;
    assert.equal(options.url, 'https://labs.google/fx/vi/tools/flow');
    assert.equal(options.active, false);
    return { id: 88 };
  };
  h.context.chrome.tabs.get = async (tabId) => ({
    id: tabId,
    url: 'https://labs.google/fx/vi/tools/flow',
    status: 'complete',
  });

  const ws = h.sockets[0];
  ws.readyState = h.context.WebSocket.OPEN;
  void ws.onopen();
  await flush();

  assert.equal(createCalls, 1);
});

test('concurrent openFlowHome calls create at most one Flow tab', async () => {
  const h = buildHarness();
  await flush();
  let createCalls = 0;
  h.context.chrome.tabs.query = async () => [];
  h.context.chrome.tabs.create = async () => {
    createCalls += 1;
    return { id: 77 };
  };
  h.context.chrome.tabs.get = async (tabId) => ({
    id: tabId,
    url: 'https://labs.google/fx/vi/tools/flow',
    status: 'complete',
  });

  const results = await Promise.all([
    vm.runInContext('openFlowHome()', h.context),
    vm.runInContext('openFlowHome()', h.context),
    vm.runInContext('openFlowHome()', h.context),
  ]);
  assert.equal(createCalls, 1);
  assert.deepEqual(results.map((result) => result.tabId), [77, 77, 77]);
});

test('openFlowHome creates a normal window when Chrome has no current window', async () => {
  const h = buildHarness();
  await flush();
  let tabCreateCalls = 0;
  let windowCreateCalls = 0;
  h.context.chrome.tabs.query = async () => [];
  h.context.chrome.tabs.create = async () => {
    tabCreateCalls += 1;
    throw new Error('No current window');
  };
  h.context.chrome.tabs.get = async (tabId) => ({
    id: tabId,
    url: 'https://labs.google/fx/vi/tools/flow',
    status: 'complete',
  });
  h.context.chrome.windows.getAll = async () => [];
  h.context.chrome.windows.create = async (options) => {
    windowCreateCalls += 1;
    assert.equal(options.url, 'https://labs.google/fx/vi/tools/flow');
    assert.equal(options.focused, false);
    assert.equal(options.type, 'normal');
    return { id: 9, tabs: [{ id: 91 }] };
  };

  const result = await vm.runInContext('openFlowHome()', h.context);
  assert.equal(result.tabId, 91);
  assert.equal(result.isNew, true);
  assert.equal(tabCreateCalls, 0);
  assert.equal(windowCreateCalls, 1);
});

test('concurrent auth synchronization shares one labs session request', async () => {
  let sessionFetches = 0;
  let finishSessionFetch;
  const sessionPending = new Promise((resolve) => { finishSessionFetch = resolve; });
  const h = buildHarness({}, {
    fetchImpl: async () => {
      sessionFetches += 1;
      await sessionPending;
      return {
        ok: true,
        json: async () => ({ access_token: 'labs-token', user: { email: 'user@example.com' } }),
      };
    },
  });
  await flush();
  const ws = h.sockets[0];
  ws.readyState = h.context.WebSocket.OPEN;

  const first = vm.runInContext('syncAuth()', h.context);
  const second = vm.runInContext('syncAuth()', h.context);
  await flush();
  assert.equal(sessionFetches, 1);

  finishSessionFetch();
  await Promise.all([first, second]);
  assert.equal(ws.sent.filter((frame) => frame.type === 'token_captured').length, 1);
});

test('local offscreen keepalive does not duplicate the backend websocket heartbeat', async () => {
  const h = buildHarness();
  await flush();
  const ws = h.sockets[0];
  ws.readyState = h.context.WebSocket.OPEN;

  await vm.runInContext('lastAuthSyncAt = Date.now(); keepAlive()', h.context);
  assert.equal(ws.sent.some((frame) => frame.type === 'pong'), false);
});

test('popup state exposes a bounded activity log for provider RPC progress', async () => {
  const h = buildHarness();
  await flush();
  vm.runInContext('activityState.logs = []', h.context);
  for (let index = 0; index < 80; index += 1) {
    vm.runInContext(`appendActivity("event ${index}", "done")`, h.context);
  }
  const state = await vm.runInContext('connectionState()', h.context);
  assert.equal(state.activity.logs.length, 50);
  assert.equal(state.activity.logs[0].label, 'event 79');
  assert.equal(state.activity.activeCount, 0);
});

test('extension logs redact credentials and URL query values', async () => {
  const h = buildHarness();
  await flush();
  vm.runInContext(
    'appendActivity("Fetch failed", "error", "https://example.test/path?key=secret-value Bearer private-token")',
    h.context,
  );
  vm.runInContext(
    'extensionLog("error", "Auth failed", { authorization: "Bearer private-token" })',
    h.context,
  );
  vm.runInContext(
    'extensionLog("info", "Request", { url: "https://api.shopcongngheso5.io.vn/v1/media?key=secret-value" })',
    h.context,
  );
  const state = await vm.runInContext('connectionState()', h.context);
  const serialized = JSON.stringify({ logs: state.activity.logs, console: h.consoleEvents });
  assert.equal(serialized.includes('secret-value'), false);
  assert.equal(serialized.includes('private-token'), false);
  assert.equal(serialized.includes('example.test'), false);
  assert.equal(serialized.includes('shopcongngheso5.io.vn'), false);
  assert.equal(serialized.includes('127.0.0.1'), false);
  assert.match(serialized, /\/v1\/media/);
  assert.match(serialized, /redacted/);
});
