// Runs n8n Code-node JavaScript outside n8n, for tests/test_js_parity.py.
// stdin:  JSON array of jobs
//   {kind: "code", code, items, refs, now}   one Code node, $input = items, $('Name') = refs[Name]
//   {kind: "expr", expr, json, now}          one {{ expression }} with $json
// stdout: JSON array of {ok, out} or {ok: false, error}
// `now` freezes `new Date()` inside the job, like a fixed clock.
'use strict';

const RealDate = Date;

function frozenDate(nowIso) {
  const nowMs = RealDate.parse(nowIso);
  return class FrozenDate extends RealDate {
    constructor(...args) {
      if (args.length === 0) super(nowMs);
      else super(...args);
    }
    static now() { return nowMs; }
  };
}

function run(job) {
  const D = frozenDate(job.now);
  if (job.kind === 'expr') {
    return new Function('$json', 'Date', `return (${job.expr});`)(job.json, D);
  }
  const items = job.items.map((json) => ({ json }));
  const $input = { first: () => items[0], all: () => items };
  const $ = (name) => ({ first: () => ({ json: job.refs[name] }) });
  return new Function('$input', '$', 'Date', job.code)($input, $, D).map((item) => item.json);
}

let raw = '';
process.stdin.on('data', (chunk) => { raw += chunk; });
process.stdin.on('end', () => {
  const out = JSON.parse(raw).map((job) => {
    try { return { ok: true, out: run(job) }; } catch (e) { return { ok: false, error: String(e) }; }
  });
  process.stdout.write(JSON.stringify(out));
});
