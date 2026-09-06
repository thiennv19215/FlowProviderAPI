import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const popupHtml = fs.readFileSync(new URL('../popup/popup.html', import.meta.url), 'utf8');
const popupJs = fs.readFileSync(new URL('../popup/popup.js', import.meta.url), 'utf8');
const background = fs.readFileSync(new URL('../background/background.js', import.meta.url), 'utf8');
const transport = fs.readFileSync(new URL('../providers/flow/browser-transport.js', import.meta.url), 'utf8');

test('popup is an operator console and does not expose local generation paths', () => {
  assert.equal(popupHtml.includes('id="btn-generate"'), false);
  assert.equal(popupHtml.includes('id="media-type-group"'), false);
  assert.equal(popupHtml.includes('data-provider='), false);
  assert.equal(popupHtml.includes('Batch Prompts'), false);
  assert.equal(popupHtml.includes('Video (Veo)'), false);
  assert.equal(popupHtml.includes('Gemini Live'), false);
  assert.equal(popupJs.includes('FLOW_PROVIDER_CREATE_GENERATION'), false);
  assert.match(popupHtml, /Executor only · jobs are owned by FlowProviderAPI/);
});

test('browser runtime rejects the legacy direct-generation shortcut', () => {
  assert.match(transport, /executeDirectFlowImageGeneration = async function disabledDirectFlowGeneration/);
  assert.match(transport, /direct_generation_disabled_use_provider_api/);
  assert.match(background, /FLOW_PROVIDER_CREATE_GENERATION/);
});

test('extension_ready metadata advertises explicit executor capabilities', () => {
  assert.match(background, /\.\.\.meta/);
  assert.match(transport, /baseGetProfileMetaForCapabilities/);
  for (const capability of [
    'flow.session',
    'flow.browser_fetch',
    'flow.recaptcha',
    'flow.project_tab',
    'rpc.cancel',
    'connector.keepalive',
  ]) {
    assert.ok(transport.includes(`"${capability}"`), capability);
  }
});

test('stored Flow tab ids are validated before reuse', () => {
  assert.match(transport, /trackedUrl = tracked\?\.url \|\| tracked\?\.pendingUrl/);
  assert.match(transport, /!isFlowUrl\(trackedUrl\)/);
  assert.match(transport, /chrome\.storage\.local\.remove\(FLOW_TAB_ID_KEY\)/);
});

test('connector reconnect uses bounded positive jitter', () => {
  assert.match(transport, /jitteredScheduleReconnect/);
  assert.match(transport, /Math\.random\(\) \* jitterWindow/);
  assert.match(transport, /Math\.min\(30000, baseDelay/);
});
