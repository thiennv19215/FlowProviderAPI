import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const background = fs.readFileSync(new URL('../background/background.js', import.meta.url), 'utf8');
const bridge = fs.readFileSync(new URL('../providers/flow/session-bridge.js', import.meta.url), 'utf8');
const browserTransport = fs.readFileSync(new URL('../providers/flow/browser-transport.js', import.meta.url), 'utf8');
const offscreen = fs.readFileSync(new URL('../background/offscreen.js', import.meta.url), 'utf8');
const configContext = { self: {} };
vm.runInNewContext(fs.readFileSync(new URL('../config.js', import.meta.url), 'utf8'), configContext);
const manifest = JSON.parse(fs.readFileSync(new URL('../manifest.json', import.meta.url), 'utf8'));

test('service worker loads providers and reusable connector state', () => {
  assert.equal(manifest.background.service_worker, 'background/background.js');
  assert.match(background, /providers\/flow\/session-bridge\.js/);
});

test('private connector credentials load from an optional local config', () => {
  assert.match(background, /importScripts\("\.\.\/config\.local\.js"\)/);
  assert.match(background, /try \{/);
});

test('production extension defaults to the public provider hostname', () => {
  assert.equal(configContext.self.FLOW_PROVIDER_EXTENSION_CONFIG.defaultServerUrl, 'https://api.shopcongngheso5.io.vn');
  assert.ok(manifest.host_permissions.includes('https://api.shopcongngheso5.io.vn/*'));
});

test('session refresh accepts only exact HTTPS Flow origins from this extension', async () => {
  let listener;
  let refreshes = 0;
  const context = { URL, chrome: { runtime: {
    id: 'connector', onMessage: { addListener: (fn) => { listener = fn; } },
  } }, syncAuth: async () => { refreshes += 1; } };
  vm.runInNewContext(bridge, context);
  for (const host of ['labs.google', 'flow.google', 'flow.google.com']) {
    assert.ok(manifest.content_scripts[0].matches.includes(`https://${host}/*`));
    const response = await new Promise((resolve) => {
      assert.equal(listener({ type: 'FLOW_PROVIDER_REFRESH_SESSION' },
        { id: 'connector', url: `https://${host}/project/test` }, resolve), true);
    });
    assert.equal(response.ok, true);
    assert.equal(response.token, undefined);
  }
  for (const sender of [
    { id: 'other', url: 'https://flow.google.com/' },
    { id: 'connector', url: 'http://flow.google.com/' },
    { id: 'connector', url: 'https://flow.google.com.evil.test/' },
    { id: 'connector', url: 'https://evil.test/', tab: { url: 'https://flow.google.com/' } },
  ]) {
    for (const type of ['FLOW_PROVIDER_REFRESH_SESSION', 'FLOW_PROVIDER_FRAME_SESSION']) {
      let response;
      assert.equal(listener({ type, token: 'untrusted' }, sender, (r) => { response = r; }), false);
      assert.equal(response.error, 'untrusted_frame_sender');
    }
  }
  assert.equal(refreshes, 3);
});

test('Flow top-level pages and frames request browser-owned session refresh', async () => {
  const source = fs.readFileSync(new URL('../providers/flow/flow-frame-bridge.js', import.meta.url), 'utf8');
  for (const host of ['flow.google', 'flow.google.com']) {
    for (const topLevel of [true, false]) {
      const messages = [];
      let tick;
      const window = {};
      window.top = topLevel ? window : {};
      vm.runInNewContext(source, {
        window, location: { protocol: 'https:', hostname: host },
        chrome: { runtime: { sendMessage: async (m) => messages.push(m) } },
        fetch: () => { throw new Error('Must not fetch session across origins from a Flow page'); },
        setInterval: (fn) => { tick = fn; },
      });
      assert.equal(messages[0].type, 'FLOW_PROVIDER_REFRESH_SESSION');
      await tick();
      assert.equal(messages.length, 2);
    }
  }
});

test('captured frame session updates the bearer cache and pushes auth to the provider socket', () => {
  assert.match(bridge, /cachedBearer = normalizedToken/);
  assert.match(bridge, /cachedBearerAt = Date\.now\(\)/);
  assert.match(bridge, /type: "token_captured"/);
});

test('offscreen keepalive wakes the service worker without maintaining a second token cache', () => {
  assert.match(offscreen, /FLOW_PROVIDER_KEEPALIVE/);
  assert.equal(offscreen.includes('FLOW_PROVIDER_TOKEN_CACHE_'), false);
  assert.match(bridge, /keepAlive\(\)\.catch/);
});

test('offscreen owns the extension keepalive timer', () => {
  assert.match(offscreen, /setInterval\(pingServiceWorker, KEEPALIVE_MS\)/);
  assert.equal(background.includes('const KEEPALIVE_MS'), false);
  assert.equal(background.includes('setInterval(() => { keepAlive()'), false);
  assert.equal(background.includes('type: "pong"'), false);
});

test('auth synchronization is single-flight for each active socket', () => {
  assert.match(background, /authSyncInFlight\?\.socket === targetSocket/);
  assert.match(background, /if \(authSyncInFlight === entry\) authSyncInFlight = null/);
  assert.match(browserTransport, /authSyncInFlight\?\.socket === targetSocket/);
  assert.match(browserTransport, /expectedGeneration: generation/);
  assert.match(browserTransport, /cachedBearer = session\.access_token/);
  assert.match(browserTransport, /cachedBearer = null/);
  assert.match(browserTransport, /authGeneration \+= 1/);
  assert.match(background, /authFetchSequence/);
  assert.match(browserTransport, /fetchSequence === authFetchSequence/);
});
