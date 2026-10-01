// CPU-profile opening a large ontology in the workbench (Chromium only). Usage: node e2e/profile.mjs [1000]
import { BASE, demoLogin, launch } from './lib.mjs';

const n = Number(process.argv[2]) || 1000;
const browser = await launch();
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
const page = await ctx.newPage();
page.on('dialog', (d) => d.accept());
await demoLogin(page);
const H = { 'X-Prime-Client': 'workbench' };
const classes = Array.from({ length: n }, (_, i) => ({ name: `Class${i}`, label: `Class ${i}`, comment: '', parents: i > 0 && i % 7 === 0 ? [`Class${i - 7}`] : [] }));
const dps = classes.flatMap((c) => [0, 1, 2].map((k) => ({ name: `prop${k}`, domain: c.name, datatype: 'string', required: false })));
const ops = Array.from({ length: Math.round(n * 1.5) }, (_, i) => ({ name: `rel${i}`, domain: `Class${i % n}`, range: `Class${(i * 7 + 13) % n}`, cardinality: 'many-to-many' }));
const r = await page.request.fetch(`${BASE}/api/v1/ontology/`, { method: 'POST', headers: H, data: { name: `Prof ${n}`, model: { classes, dataProperties: dps, objectProperties: ops } } });
const o = await r.json();

const cdp = await ctx.newCDPSession(page);
await cdp.send('Profiler.enable');
await cdp.send('Profiler.setSamplingInterval', { interval: 500 });
await cdp.send('Profiler.start');
const t0 = Date.now();
await page.goto(`${BASE}/?ontology=${o.id}&tab=workbench`);
await page.waitForFunction(() => document.querySelectorAll('.react-flow__node-cls').length > 3, null, { timeout: 180000 });
const open = Date.now() - t0;
const { profile } = await cdp.send('Profiler.stop');

const byId = new Map(profile.nodes.map((nd) => [nd.id, nd]));
const self = new Map();
const dt = profile.timeDeltas;
profile.samples.forEach((id, i) => {
  const nd = byId.get(id);
  const cf = nd.callFrame;
  const key = `${cf.functionName || '(anonymous)'} ${cf.url.split('/').slice(-2).join('/').split('?')[0]}:${cf.lineNumber}`;
  self.set(key, (self.get(key) || 0) + (dt[i] || 0));
});
const total = [...self.values()].reduce((a, b) => a + b, 0);
console.log(`open ${n} classes: ${(open / 1000).toFixed(1)}s wall, ${(total / 1e6).toFixed(1)}s sampled CPU`);
console.log([...self.entries()].sort((a, b) => b[1] - a[1]).slice(0, 14).map(([k, v]) => `${(v / 1000).toFixed(0).padStart(6)} ms  ${k}`).join('\n'));
await page.request.fetch(`${BASE}/api/v1/ontology/${o.id}/`, { method: 'DELETE', headers: H });
await browser.close();
