import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../background.js', import.meta.url), 'utf8');
const browserTransportSource = fs.readFileSync(new URL('../browser-transport.js', import.meta.url), 'utf8');
const chatgptSource = fs.readFileSync(new URL('../chatgpt-provider.js', import.meta.url), 'utf8');
const configContext = { self: {} };
vm.runInNewContext(fs.readFileSync(new URL('../config.js', import.meta.url), 'utf8'), configContext);
const productionConfig = JSON.parse(JSON.stringify(configContext.self.FLOW_PROVIDER_EXTENSION_CONFIG));

function buildHarness(initialStorage = {}, { fetchImpl = null, extensionConfig = null, loadBrowserTransport = false } = {}) {
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
    storage: {
      local: {
        get: async (keys) => {
          if (typeof keys === 'string') return { [keys]: storage[keys] };
          if (Array.isArray(keys)) return Object.fromEntries(keys.map((key) => [key, storage[key]]));
          return { ...storage };
        },
        set: async (values) => Object.assign(storage, values),
        remove: async (keys) => {
          for (const key of Array.isArray(keys) ? keys : [keys]) delete storage[key];
        },
      }
    },
    permissions: { contains: async () => true, request: async () => true },
    identity: { getProfileUserInfo: async () => ({ email: '' }) },
    runtime: {
      getManifest: () => ({ version: '1.0.0' }),
      onMessage: { addListener: () => { } },
      onInstalled: { addListener: () => { } },
      onStartup: { addListener: () => { } },
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
    declarativeNetRequest: { updateDynamicRules: async () => { } },
    webRequest: { onBeforeRequest: { addListener: () => { } } },
  };

  const context = {
    self: extensionConfig ? { FLOW_PROVIDER_EXTENSION_CONFIG: extensionConfig } : {}, chrome, WebSocket: MockWebSocket,
    URL, AbortController, Uint8Array, TextEncoder,
    btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
    setTimeout, clearTimeout, setInterval: () => 1, clearInterval: () => { },
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
  vm.runInContext(chatgptSource, context, { filename: 'chatgpt-provider.js' });
  if (loadBrowserTransport) vm.runInContext(browserTransportSource, context, { filename: 'browser-transport.js' });
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

test('connector key is sent only inside the TLS websocket hello frame', async () => {
  const h = buildHarness({}, {
    extensionConfig: {
      ...productionConfig,
      defaultServerUrl: 'https://provider.example.com',
      connectorApiKey: 'connector-secret',
    },
  });
  await flush();
  const ws = h.sockets[0];
  assert.equal(ws.url.includes('connector-secret'), false);
  ws.readyState = h.context.WebSocket.OPEN;
  await ws.onopen();
  await flush();
  const hello = ws.sent.find((frame) => frame.type === 'extension_ready');
  assert.equal(hello.connectorApiKey, 'connector-secret');
});

test('connector key fails closed when the provider endpoint is not TLS', async () => {
  const h = buildHarness({}, {
    extensionConfig: {
      ...productionConfig,
      defaultServerUrl: 'http://provider.example.com',
      connectorApiKey: 'connector-secret',
    },
  });
  await flush();
  assert.equal(h.sockets.length, 0);
  assert.equal(
    h.consoleEvents.some((event) => JSON.stringify(event).includes('connector-secret')),
    false,
  );
  vm.runInContext('disconnect()', h.context);
});

test('concurrent connect calls create only one websocket', async () => {
  const h = buildHarness();
  await flush();
  vm.runInContext('disconnect()', h.context);

  await Promise.all([
    vm.runInContext('connect()', h.context),
    vm.runInContext('connect()', h.context),
    vm.runInContext('connect()', h.context),
  ]);

  assert.equal(h.sockets.length, 2);
});

test('changing the endpoint invalidates a connection waiting for profile metadata', async () => {
  const h = buildHarness({ 'flow-provider-server-url-v1': 'https://old.example.com' });
  await flush();
  vm.runInContext('disconnect()', h.context);
  let release;
  const barrier = new Promise((resolve) => { release = resolve; });
  let entered;
  const metadataStarted = new Promise((resolve) => { entered = resolve; });
  h.context.chrome.identity.getProfileUserInfo = async () => {
    entered();
    await barrier;
    return { email: '' };
  };
  const staleAttempt = vm.runInContext('connect()', h.context);
  await metadataStarted;
  h.context.chrome.identity.getProfileUserInfo = async () => ({ email: '' });
  await vm.runInContext('setConnectionConfig("https://new.example.com")', h.context);
  await vm.runInContext('connect()', h.context);
  release();
  await staleAttempt;

  assert.equal(h.sockets.length, 2);
  assert.equal(h.sockets[1].url, 'wss://new.example.com/api/extensions/ws');
  assert.equal(vm.runInContext('socket', h.context), h.sockets[1]);
  vm.runInContext('disconnect()', h.context);
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

test('job completion and error statistics are tracked and persisted', async () => {
  const h = buildHarness();
  await flush();

  h.context.activity1 = vm.runInContext('beginActivity({ type: "SW_FETCH" })', h.context);
  assert.equal(vm.runInContext('activityState.activeCount', h.context), 1);

  vm.runInContext('finishActivity(activity1)', h.context);
  assert.equal(vm.runInContext('activityState.activeCount', h.context), 0);
  assert.equal(vm.runInContext('activityState.completedCount', h.context), 1);
  assert.equal(vm.runInContext('activityState.errorCount', h.context), 0);
  assert.equal(h.storage['flow_provider_job_stats']?.completedCount, 1);

  h.context.activity2 = vm.runInContext('beginActivity({ type: "INJECT_PAGE_FETCH" })', h.context);
  vm.runInContext('finishActivity(activity2, new Error("Failed upstream"))', h.context);
  assert.equal(vm.runInContext('activityState.completedCount', h.context), 1);
  assert.equal(vm.runInContext('activityState.errorCount', h.context), 1);
  assert.equal(h.storage['flow_provider_job_stats']?.errorCount, 1);

  const state = await vm.runInContext('connectionState()', h.context);
  assert.equal(state.activity.completedCount, 1);
  assert.equal(state.activity.errorCount, 1);
});

test('CANCEL_RPC aborts an in-flight SW_FETCH', async () => {
  const h = buildHarness();
  await flush();
  const ws = h.sockets[0];
  ws.readyState = h.context.WebSocket.OPEN;

  const rpcPromise = ws.onmessage({
    data: JSON.stringify({
      id: 'rpc-1',
      type: 'SW_FETCH',
      spec: { url: 'https://aisandbox-pa.googleapis.com/v1/test', method: 'GET', timeoutMs: 60000 },
    })
  });
  await flush();
  assert.equal(h.fetchSignals.length, 1);
  assert.equal(h.fetchSignals[0].aborted, false);

  await ws.onmessage({ data: JSON.stringify({ type: 'CANCEL_RPC', targetRequestId: 'rpc-1' }) });
  await rpcPromise;
  assert.equal(h.fetchSignals[0].aborted, true);
  assert.ok(ws.sent.some((frame) => frame.id === 'rpc-1' && typeof frame.error === 'string'));
});

test('unexpected websocket close aborts every in-flight RPC before reconnecting', async () => {
  const h = buildHarness();
  await flush();
  const ws = h.sockets[0];
  ws.readyState = h.context.WebSocket.OPEN;

  const rpcPromise = ws.onmessage({
    data: JSON.stringify({
      id: 'rpc-close',
      type: 'SW_FETCH',
      spec: { url: 'https://aisandbox-pa.googleapis.com/v1/test', method: 'GET', timeoutMs: 60000 },
    })
  });
  await flush();
  assert.equal(h.fetchSignals.length, 1);
  assert.equal(h.fetchSignals[0].aborted, false);

  ws.close();
  await rpcPromise;
  assert.equal(h.fetchSignals[0].aborted, true);
  assert.equal(vm.runInContext('inflightRpcControllers.size', h.context), 0);
  vm.runInContext('disconnect()', h.context);
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

test('backend handshake is sent before a Flow tab finishes loading', async () => {
  const h = buildHarness({}, {
    fetchImpl: async () => { throw new Error('labs unavailable'); },
  });
  await flush();
  h.context.chrome.tabs.query = async () => [];
  h.context.chrome.tabs.create = async () => ({ id: 88 });
  h.context.chrome.tabs.get = async () => await new Promise(() => { });

  const ws = h.sockets[0];
  ws.readyState = h.context.WebSocket.OPEN;
  void ws.onopen();
  await flush();

  assert.ok(ws.sent.some((frame) => frame.type === 'extension_ready'));
});

test('reconnect cooldown prevents repeatedly creating Flow tabs after open failure', async () => {
  const h = buildHarness({}, {
    fetchImpl: async () => { throw new Error('labs unavailable'); },
  });
  await flush();
  let createCalls = 0;
  h.context.chrome.tabs.query = async () => [];
  h.context.chrome.tabs.create = async () => {
    createCalls += 1;
    return { id: 88 };
  };
  h.context.chrome.tabs.get = async () => null;

  const first = h.sockets[0];
  first.readyState = h.context.WebSocket.OPEN;
  await first.onopen();
  await flush();

  await vm.runInContext('socket = null; reconnectTimer = null; connect()', h.context);
  await flush();
  const second = h.sockets[1];
  second.readyState = h.context.WebSocket.OPEN;
  await second.onopen();
  await flush();

  assert.equal(createCalls, 1);
  assert.ok(first.sent.some((frame) => frame.type === 'extension_ready'));
  assert.ok(second.sent.some((frame) => frame.type === 'extension_ready'));
});

test('a tracked Flow tab is reused after it redirects to sign-in', async () => {
  const h = buildHarness({ 'flow-provider-flow-tab-id-v1': 42 });
  await flush();
  let createCalls = 0;
  h.context.chrome.tabs.query = async () => [];
  h.context.chrome.tabs.get = async () => ({
    id: 42,
    url: 'https://accounts.google.com/signin',
    status: 'complete',
  });
  h.context.chrome.tabs.create = async () => {
    createCalls += 1;
    return { id: 99 };
  };
  vm.runInContext('waitForTab = async () => { throw new Error("flow_tab_redirected"); }', h.context);

  await assert.rejects(
    vm.runInContext('openFlowHome()', h.context),
    /flow_tab_redirected/,
  );
  assert.equal(createCalls, 0);
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

test('account switch invalidates a delayed session and authorizes later RPCs with token B', async () => {
  let releaseSessionA;
  const sessionA = new Promise((resolve) => { releaseSessionA = resolve; });
  const requests = [];
  const h = buildHarness({}, {
    loadBrowserTransport: true,
    fetchImpl: async (url, options = {}) => {
      requests.push({ url, options });
      if (url === 'https://labs.google/fx/api/auth/session') return sessionA;
      return {
        ok: true,
        status: 200,
        url,
        headers: new Map([['content-type', 'text/plain']]),
        text: async () => 'ok',
      };
    },
  });
  await flush();
  const ws = h.sockets[0];
  ws.readyState = h.context.WebSocket.OPEN;

  const syncA = vm.runInContext('syncAuth()', h.context);
  await flush();
  assert.equal(requests.filter((request) => request.url === 'https://labs.google/fx/api/auth/session').length, 1);

  vm.runInContext('publishCapturedSession("token-B", "b@example.com")', h.context);
  const rpcResult = await vm.runInContext('handleRpc({ type: "SW_FETCH", spec: { authMode: "flow", url: "https://aisandbox-pa.googleapis.com/v1/test", method: "GET" } }, new AbortController().signal)', h.context);
  const providerRequest = requests.find((request) => request.url === 'https://aisandbox-pa.googleapis.com/v1/test');
  assert.equal(providerRequest.options.headers.authorization, 'Bearer token-B');
  assert.equal(await vm.runInContext('getBearer()', h.context), 'token-B');

  releaseSessionA({
    ok: true,
    json: async () => ({ access_token: 'token-A', user: { email: 'a@example.com' } }),
  });
  await syncA;
  assert.equal(ws.sent.some((frame) => frame.type === 'user_info' && frame.userInfo?.email === 'a@example.com'), false);
  assert.equal(rpcResult.ok, true);
});

test('out-of-order session fetches keep the newest bearer cache', async () => {
  const pending = [];
  const h = buildHarness({}, {
    fetchImpl: async (url) => {
      if (url !== 'https://labs.google/fx/api/auth/session') {
        return { ok: true, status: 200, url, headers: new Map(), text: async () => 'ok' };
      }
      return new Promise((resolve) => pending.push(resolve));
    },
  });
  await flush();

  const first = vm.runInContext('getBearer({ force: true })', h.context);
  const second = vm.runInContext('getBearer({ force: true })', h.context);
  await flush();
  assert.equal(pending.length, 2);

  pending[1]({ ok: true, json: async () => ({ access_token: 'token-B', user: { email: 'b@example.com' } }) });
  assert.equal(await second, 'token-B');
  pending[0]({ ok: true, json: async () => ({ access_token: 'token-A', user: { email: 'a@example.com' } }) });
  await assert.rejects(first, /auth_changed/);
  assert.equal(await vm.runInContext('getBearer()', h.context), 'token-B');
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

test('isFlowUrl accepts Flow URLs and rejects other labs tools', async () => {
  const h = buildHarness();
  await flush();
  const isFlow = (url) => vm.runInContext(`isFlowUrl(${JSON.stringify(url)})`, h.context);

  assert.equal(isFlow('https://labs.google/fx/vi/tools/flow'), true);
  assert.equal(isFlow('https://labs.google/fx/vi/tools/flow/project/abc'), true);
  assert.equal(isFlow('https://labs.google/fx/tools/flow'), true);
  assert.equal(isFlow('https://labs.google/fx/en-us/tools/flow/'), true);
  assert.equal(isFlow('https://flow.google/'), true);
  assert.equal(isFlow('https://flow.google.com/tools/flow'), true);

  // Rejects other non-flow tools
  assert.equal(isFlow('https://labs.google/fx/vi/tools/image-fx'), false);
  assert.equal(isFlow('https://labs.google/fx/vi/tools/music-fx'), false);
  assert.equal(isFlow('https://labs.google/fx/'), false);
  assert.equal(isFlow('https://labs.google/'), false);
});

test('handleRpc executes INJECT_RECAPTCHA successfully', async () => {
  const h = buildHarness();
  await flush();
  h.context.chrome.tabs.get = async () => ({ id: 42, url: 'https://labs.google/fx/vi/tools/flow', status: 'complete' });
  h.context.chrome.scripting.executeScript = async () => [{ result: { ok: true, data: 'test-captcha-token' } }];

  const res = await vm.runInContext('handleRpc({ type: "INJECT_RECAPTCHA", tabId: 42, fallbackKey: "key123", action: "IMAGE_GENERATION" })', h.context);
  assert.equal(res, 'test-captcha-token');
});

test('handleRpc INJECT_RECAPTCHA wakes tab and retries on initial failure', async () => {
  const h = buildHarness();
  await flush();
  let updatedTabId = null;
  let updateOptions = null;
  h.context.chrome.tabs.update = async (tabId, opts) => {
    updatedTabId = tabId;
    updateOptions = opts;
    return { id: tabId };
  };
  h.context.chrome.tabs.get = async () => ({ id: 42, url: 'https://labs.google/fx/vi/tools/flow', status: 'complete' });

  let callCount = 0;
  h.context.chrome.scripting.executeScript = async () => {
    callCount += 1;
    if (callCount === 1) {
      return [{ result: { ok: false, error: 'recaptcha_timeout' } }];
    }
    return [{ result: { ok: true, data: 'recovered-captcha-token' } }];
  };

  const res = await vm.runInContext('handleRpc({ type: "INJECT_RECAPTCHA", tabId: 42, fallbackKey: "key123", action: "IMAGE_GENERATION" })', h.context);
  assert.equal(res, 'recovered-captcha-token');
  assert.equal(updatedTabId, 42);
  assert.equal(updateOptions?.active, true);
});

test('openFlowHome opens direct project URL when project ID is provided', async () => {
  const h = buildHarness();
  await flush();
  let createdUrl = null;
  h.context.chrome.tabs.query = async () => [];
  h.context.chrome.tabs.create = async (options) => {
    createdUrl = options.url;
    return { id: 77 };
  };
  h.context.chrome.tabs.get = async (tabId) => ({
    id: tabId,
    url: createdUrl,
    status: 'complete',
  });

  const res = await vm.runInContext('openFlowHome({ projectId: "be5e00b8-deb9-4738-b38f-cc5161febbe1" })', h.context);
  assert.equal(res.tabId, 77);
  assert.equal(createdUrl, 'https://flow.google.com/project/be5e00b8-deb9-4738-b38f-cc5161febbe1');
});

test('openFlowHome navigates existing generic tab to project URL when project ID is known', async () => {
  const h = buildHarness({ 'flow-provider-last-project-id-v1': 'be5e00b8-deb9-4738-b38f-cc5161febbe1' });
  await flush();
  let updatedUrl = null;
  h.context.chrome.tabs.query = async () => [{
    id: 55,
    url: 'https://labs.google/fx/vi/tools/flow',
    status: 'complete',
    active: true,
  }];
  h.context.chrome.tabs.update = async (tabId, options) => {
    if (options.url) updatedUrl = options.url;
    return { id: tabId, url: updatedUrl };
  };
  h.context.chrome.tabs.get = async (tabId) => ({
    id: tabId,
    url: updatedUrl || 'https://labs.google/fx/vi/tools/flow',
    status: 'complete',
  });

  const res = await vm.runInContext('openFlowHome()', h.context);
  assert.equal(res.tabId, 55);
  assert.equal(updatedUrl, 'https://flow.google.com/project/be5e00b8-deb9-4738-b38f-cc5161febbe1');
});

test('isChatGPTUrl matches ChatGPT domains and rejects other sites', async () => {
  const h = buildHarness();
  await flush();
  const isCgt = (url) => vm.runInContext(`isChatGPTUrl(${JSON.stringify(url)})`, h.context);

  assert.equal(isCgt('https://chatgpt.com/'), true);
  assert.equal(isCgt('https://chatgpt.com/c/12345'), true);
  assert.equal(isCgt('https://chat.openai.com/'), true);
  assert.equal(isCgt('https://chat.openai.com/chat'), true);

  assert.equal(isCgt('https://google.com'), false);
  assert.equal(isCgt('https://labs.google/fx/vi/tools/flow'), false);
});

test('handleRpc CHATGPT_OPEN_TAB finds and opens ChatGPT tab', async () => {
  const h = buildHarness();
  await flush();
  let createdUrl = null;
  h.context.chrome.tabs.query = async () => [];
  h.context.chrome.tabs.create = async (options) => {
    createdUrl = options.url;
    return { id: 101 };
  };
  h.context.chrome.tabs.get = async (tabId) => ({
    id: tabId,
    url: createdUrl,
    status: 'complete',
  });

  const res = await vm.runInContext('handleRpc({ type: "CHATGPT_OPEN_TAB" })', h.context);
  assert.equal(res.tabId, 101);
  assert.equal(createdUrl, 'https://chatgpt.com/');
});

test('handleRpc CHATGPT_GET_SESSION returns accessToken and user from session', async () => {
  const h = buildHarness();
  await flush();
  h.context.chrome.tabs.get = async () => ({ id: 101, url: 'https://chatgpt.com/', status: 'complete' });
  h.context.chrome.scripting.executeScript = async () => [{
    result: {
      ok: true,
      accessToken: 'test-chatgpt-jwt-token',
      user: { email: 'user@example.com', name: 'Test User' },
    },
  }];

  const res = await vm.runInContext('handleRpc({ type: "CHATGPT_GET_SESSION", tabId: 101 })', h.context);
  assert.equal(res.ok, true);
  assert.equal(res.accessToken, 'test-chatgpt-jwt-token');
  assert.equal(res.user?.email, 'user@example.com');
});

test('handleRpc CHATGPT_FETCH executes in-tab fetch and returns data', async () => {
  const h = buildHarness();
  await flush();
  h.context.chrome.tabs.get = async () => ({ id: 101, url: 'https://chatgpt.com/', status: 'complete' });
  h.context.chrome.scripting.executeScript = async () => [{
    result: {
      ok: true,
      data: {
        ok: true,
        status: 200,
        data: { conversation_id: 'conv_123', message: { id: 'msg_1' } },
      },
    },
  }];

  const spec = {
    url: 'https://chatgpt.com/backend-api/conversation',
    method: 'POST',
    body: JSON.stringify({ action: 'next' }),
  };
  const res = await vm.runInContext(`handleRpc({ type: "CHATGPT_FETCH", tabId: 101, spec: ${JSON.stringify(spec)} })`, h.context);
  assert.equal(res.ok, true);
  assert.equal(res.status, 200);
  assert.equal(res.data.conversation_id, 'conv_123');
});




