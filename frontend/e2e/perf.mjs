// Large-ontology responsiveness probe. Usage: node e2e/perf.mjs [300 1000 2000]
import { BASE, browserName, demoLogin, launch, watchErrors } from './lib.mjs';

const sizes = process.argv.slice(2).map(Number).filter(Boolean);
if (!sizes.length) sizes.push(300, 1000);
const browser = await launch();
const page = await (await browser.newContext({ viewport: { width: 1500, height: 950 } })).newPage();
const errors = [];
watchErrors(page, errors);
page.on('dialog', (d) => d.accept());
await demoLogin(page);
const H = { 'X-Prime-Client': 'workbench' };
const api = async (p, o = {}) => { const r = await page.request.fetch(`${BASE}/api/v1/ontology${p}`, { ...o, headers: { ...H, ...(o.headers || {}) } }); return r.status() === 204 ? null : r.json(); };

const rows = [];
for (const n of sizes) {
  const classes = Array.from({ length: n }, (_, i) => ({ name: `Class${i}`, label: `Class ${i}`, comment: '', parents: i > 0 && i % 7 === 0 ? [`Class${i - 7}`] : [], group: `Group ${i % 8}` }));
  const dps = classes.flatMap((c) => [0, 1, 2].map((k) => ({ name: `prop${k}`, domain: c.name, datatype: 'string', required: false })));
  const ops = Array.from({ length: Math.round(n * 1.5) }, (_, i) => ({ name: `rel${i}`, domain: `Class${i % n}`, range: `Class${(i * 7 + 13) % n}`, cardinality: 'many-to-many' }));
  const t = Date.now();
  const o = await api('/', { method: 'POST', data: { name: `Perf ${n} ${t}`, model: { classes, dataProperties: dps, objectProperties: ops } } });
  const created = Date.now() - t;
  const t0 = Date.now();
  await page.goto(`${BASE}/?ontology=${o.id}&tab=workbench`);
  await page.waitForFunction(() => document.querySelectorAll('.react-flow__node-cls').length > 3, null, { timeout: 120000 });
  const open = Date.now() - t0;
  const rendered = await page.locator('.react-flow__node-cls').count();
  const t1 = Date.now();
  await page.getByPlaceholder('Search graph…').fill('Class7');
  await page.waitForTimeout(100);
  const search = Date.now() - t1;
  const t2 = Date.now();
  await page.getByLabel('Filter explorer').fill(`Class${n - 1}`);
  await page.getByRole('button', { name: new RegExp(`^Class${n - 1}$`) }).first().waitFor({ timeout: 30000 });
  const explorer = Date.now() - t2;
  const t3 = Date.now();
  await page.mouse.move(700, 500);
  for (let i = 0; i < 10; i += 1) await page.mouse.wheel(0, -200);
  await page.waitForTimeout(100);
  const zoom = Date.now() - t3;
  const t4 = Date.now();
  await page.getByTestId('tab-validation').click();
  await page.getByTestId('health').waitFor({ timeout: 120000 });
  const validate = Date.now() - t4;
  rows.push({ classes: n, relationships: Math.round(n * 1.5), apiCreate: created, open, rendered, search, explorer, zoom10: zoom, validateTab: validate });
  await page.screenshot({ path: `e2e/screenshots/${browserName()}-perf-${n}.png` }).catch(() => {});
  await api(`/${o.id}/`, { method: 'DELETE' });
}
console.table(rows.map((r) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, ['classes', 'relationships', 'rendered'].includes(k) ? v : `${(v / 1000).toFixed(2)}s`]))));
console.log(errors.length ? `page errors: ${errors.slice(0, 3).join(' | ')}` : 'no page errors');
await browser.close();
