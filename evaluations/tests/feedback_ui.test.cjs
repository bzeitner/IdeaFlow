const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/evaluations/feedback.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness({hidden = false, enabled = true, observer = true, fetchImpl} = {}) {
  let observation;
  const calls = [], listeners = {};
  const buttons = [{disabled: false}];
  const message = {textContent: ''};
  const form = {
    // Named form controls shadow DOM properties; this reproduces the browser bug.
    action: {toString: () => '[object RadioNodeList]'},
    getAttribute: name => name === 'action' ? '/evaluations/interaction/' : null,
    querySelectorAll: () => buttons,
    addEventListener: (name, callback) => { listeners[name] = callback; },
  };
  const panel = {
    dataset: {observe: 'output-research-1'},
    querySelector: selector => selector === '.feedback-form' ? enabled && form : message,
    querySelectorAll: () => [form],
  };
  const document = {
    visibilityState: hidden ? 'hidden' : 'visible',
    querySelectorAll: () => [panel],
    getElementById: () => ({}),
    addEventListener: (name, callback) => { listeners[name] = callback; },
  };
  class IntersectionObserver {
    constructor(callback) { observation = callback; }
    observe() {}
  }
  class FormData {
    constructor() { this.fields = new Map([['operation', 'feedback']]); }
    set(key, value) { this.fields.set(key, value); }
  }
  vm.runInNewContext(source, {document, window: observer ? {IntersectionObserver} : {}, IntersectionObserver,
    FormData, fetch: (url, options) => {
      calls.push({url, operation: options.body.fields.get('operation'), options});
      return fetchImpl ? fetchImpl() : Promise.resolve({ok: true, json: async () => ({id: 1})});
    }});
  return {calls, buttons, message, document, listeners,
    intersect: (visible = true, width = 200) => observation([{isIntersecting: visible, intersectionRect: {height: visible ? 100 : 0, width}}]),
    submit: () => listeners.submit({preventDefault() {}, submitter: {value: 'useful'}}),
  };
}

test('initial render, collapsed content and hidden tab do not record exposure', async () => {
  const h = harness({hidden: true});
  assert.equal(h.calls.length, 0);
  h.intersect(false);
  h.intersect(true);
  await flush();
  assert.equal(h.calls.length, 0);
  h.document.visibilityState = 'visible';
  h.listeners.visibilitychange();
  await flush();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].operation, 'exposure');
});

test('successful exposure deduplicates repeated visibility in the same page session', async () => {
  const h = harness();
  h.intersect(true);
  await flush();
  h.intersect(false);
  h.intersect(true);
  h.listeners.visibilitychange();
  await flush();
  assert.equal(h.calls.length, 1);
});

test('zero-area intersection never claims exposure', async () => {
  const h = harness();
  h.intersect(true, 0);
  await flush();
  assert.equal(h.calls.length, 0);
});

test('failed exposure can retry on later visibility', async () => {
  let attempt = 0;
  const h = harness({fetchImpl: async () => ({ok: ++attempt > 1, json: async () => ({})})});
  h.intersect(true);
  await flush();
  h.listeners.visibilitychange();
  await flush();
  assert.equal(h.calls.length, 2);
});

test('submission uses action attribute despite action-named buttons and does not invent exposure', async () => {
  const h = harness();
  await h.submit();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].url, '/evaluations/interaction/');
  assert.equal(h.calls[0].operation, 'feedback');
  assert.equal(h.calls[0].options.credentials, 'same-origin');
  assert.equal(h.buttons[0].disabled, true);
  assert.match(h.message.textContent, /Saved/);
});

test('disabled feedback creates no handlers and unavailable observer creates no exposure', async () => {
  const disabled = harness({enabled: false});
  assert.equal(disabled.listeners.submit, undefined);
  assert.equal(disabled.calls.length, 0);
  const fallback = harness({observer: false});
  await fallback.submit();
  assert.deepEqual(fallback.calls.map(call => call.operation), ['feedback']);
});
