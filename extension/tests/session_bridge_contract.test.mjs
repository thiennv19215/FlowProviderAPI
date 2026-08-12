import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const loader = fs.readFileSync(new URL('../background-loader.js', import.meta.url), 'utf8');
const background = fs.readFileSync(new URL('../background.js', import.meta.url), 'utf8');
const bridge = fs.readFileSync(new URL('../session-bridge.js', import.meta.url), 'utf8');
const offscreen = fs.readFileSync(new URL('../offscreen.js', import.meta.url), 'utf8');
const configContext = { self: {} };
vm.runInNewContext(fs.readFileSync(new URL('../config.js', import.meta.url), 'utf8'), configContext);
const manifest = JSON.parse(fs.readFileSync(new URL('../manifest.json', import.meta.url), 'utf8'));

test('session bridge loads after background so it can reuse connector state', () => {
  assert.match(loader, /background\.js", "session-bridge\.js/);
});

test('production extension defaults to the public provider hostname', () => {
  assert.equal(configContext.self.FLOW_PROVIDER_EXTENSION_CONFIG.defaultServerUrl, 'https://api.shopcongngheso5.io.vn');
  assert.ok(manifest.host_permissions.includes('https://api.shopcongngheso5.io.vn/*'));
});

test('frame session is accepted only from the signed-in labs.google content frame', () => {
  assert.match(bridge, /sender\.id !== chrome\.runtime\.id/);
  assert.match(bridge, /url\.hostname === "labs\.google"/);
  assert.match(bridge, /FLOW_PROVIDER_FRAME_SESSION/);
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
});
