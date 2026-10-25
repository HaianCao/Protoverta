// Unit-test the actual inline runtime controls with a minimal DOM fixture.
// This is not a real-browser layout, accessibility or end-to-end QA claim.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

async function check(path) {
  const html = fs.readFileSync(path, 'utf8');
  const start = html.indexOf('const RUNTIME_FIELDS=');
  const end = html.indexOf('function gatewayLimits()', start);
  assert(start >= 0 && end > start, 'runtime controls must be present');
  const nodes = new Map();
  function element() {
    return {style: {}, children: [], _value: '',
      get value() {return this._value;}, set value(value) {this._value = String(value);},
      add(option) {this.children.push(option);},
      addEventListener() {},
      append(...items) {for (const item of items) {this.children.push(item);if(item.id) nodes.set(item.id, item);}}};
  }
  const body = element();
  nodes.set('gatewayRuntimeFields', element());
  let selected = {id: 'synthetic'};
  let report = {enabled: false, records: []};
  const context = vm.createContext({
    document: {createElement: element, body, querySelector: () => body},
    Option: function(text, value) {this.text = text;this.value = value;},
    $: id => {assert(nodes.has(id), 'unknown DOM id: ' + id);return nodes.get(id);},
    syncGatewayChoice() {}, protocolChanged() {},
    selectedProvider: () => selected, selected: () => selected,
    api: async path => {assert.equal(path, '/api/diagnostics?provider=synthetic');return report;}
  });
  vm.runInContext(html.slice(start, end), context);
  const values = {diagnostics_dir: 'tmp/diagnostics', token_count_mode: 'native', prompt_caching: 'ephemeral', require_current_evidence: true, evidence_max_age_days: 14, require_verified_routes: false, openrouter_provider: 'fixture/endpoint'};
  context.values = values;
  vm.runInContext('setGatewayRuntime(values)', context);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(gatewayRuntime())', context)), values);
  vm.runInContext('setGatewayRuntime({})', context);
  assert.equal(vm.runInContext('JSON.stringify(gatewayRuntime())', context), '{}');
  vm.runInContext("setGatewayRuntime({context_preflight:'off',token_count_mode:'off',prompt_caching:'off'})", context);
  for (const type of ['context', 'count', 'cache']) {
    assert.equal(vm.runInContext(`RUNTIME_CHOICES.${type}.some(option=>option[0]==='off')`, context), true);
  }
  assert.equal(vm.runInContext('gatewayRuntime().token_count_mode', context), 'off');
  await vm.runInContext('diagnosticButton.onclick()', context);
  assert.match(vm.runInContext('diagnosticOutput.textContent', context), /Enable a diagnostics directory/);
  report = {enabled: true, records: [{request_id: 'a'.repeat(32), protocol: 'responses', upstream_protocol: 'chat', status: 200, outcome: 'invalid_stream', duration_ms: 12}]};
  await vm.runInContext('diagnosticButton.onclick()', context);
  assert.match(vm.runInContext('diagnosticOutput.textContent', context), /HTTP 200.*invalid_stream/);
  assert.equal(vm.runInContext('diagnosticButton.disabled', context), false);
  report.limited = true;
  report.dropped_records = 3;
  await vm.runInContext('diagnosticButton.onclick()', context);
  assert.match(vm.runInContext('diagnosticOutput.textContent', context), /Limited report/);
  assert.match(vm.runInContext('diagnosticOutput.textContent', context), /Dropped records: 3/);
  const pending = vm.runInContext('diagnosticButton.onclick()', context);
  selected = {id: 'changed'};
  await pending;
  assert.match(vm.runInContext('diagnosticOutput.textContent', context), /Provider selection changed/);
  selected = null;
  await vm.runInContext('diagnosticButton.onclick()', context);
  assert.match(vm.runInContext('diagnosticOutput.textContent', context), /Choose a provider/);
  console.log(path + ': control round-trip and diagnostic rendering PASS (DOM fixture only)');
}

(async () => {
  assert(process.argv.length >= 3, 'pass one or more web_template.html paths');
  for (const path of process.argv.slice(2)) await check(path);
})().catch(error => {console.error(error.message);process.exitCode = 1;});
