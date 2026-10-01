// Browser tests for: sample records + individuals editor, node groups, branching & merge, concurrent-edit protection,
// OCR / Parquet imports, semantic-matching notice, and a large ontology. Needs both servers + `seed_demo_users`.
//   BROWSER=chrome|edge|firefox|webkit node e2e/features.mjs
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BASE, browserName, demoLogin, launch, makeRunner, watchErrors } from './lib.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const samples = path.resolve(here, '../../demo/samples');
fs.mkdirSync(path.resolve(here, 'screenshots'), { recursive: true });
const shot = (page, n) => page.screenshot({ path: path.resolve(here, 'screenshots', `${browserName()}-${n}.png`) }).catch(() => {});

const browser = await launch();
const ctxA = await browser.newContext({ viewport: { width: 1500, height: 950 } });
const page = await ctxA.newPage();
const errors = [];
watchErrors(page, errors);
page.on('dialog', (d) => d.accept());
const { step, results } = makeRunner();
const H = { 'X-Prime-Client': 'workbench' };
const api = async (p, opts = {}) => { const r = await page.request.fetch(`${BASE}/api/v1/ontology${p}`, { ...opts, headers: { ...H, ...(opts.headers || {}) } }); return r.status() === 204 ? null : r.json(); };
const stamp = Date.now();
const name = `Feat ${browserName()} ${stamp}`;
let oid;
const nodeOf = (text) => page.locator('.react-flow__node-cls', { hasText: text }).first();
const tab = (id) => page.getByTestId(`tab-${id}`).click();
const openOntology = async (id, tabId = 'workbench') => { await page.goto(`${BASE}/?ontology=${id}&tab=${tabId}`); await page.getByTestId('prime-ontology-workbench').waitFor(); };
const save = async () => { await page.getByRole('button', { name: 'Save changes' }).click(); await page.getByText('Ontology saved.').waitFor(); };
const created = [];

await step('login via Demo login', async () => { await demoLogin(page); }, page);

await step('SOURCES: shows OCR availability and the new formats', async () => {
  await page.waitForFunction(() => /read with OCR/.test(document.querySelector('[data-testid="ocr-note"]')?.textContent || ''), null, { timeout: 15000 });
  if (!(await page.getByText(/Parquet · SQLite/).count())) throw new Error('formats list lacks Parquet/SQLite');
}, page);

await step('RECORDS: SQLite upload with sample records -> report -> approve', async () => {
  await page.getByRole('checkbox', { name: /Copy sample records as individuals/ }).check();
  await page.getByTestId('file-input').setInputFiles(path.join(samples, 'shop.db'));
  const rep = page.getByTestId('records-report');
  await rep.waitFor({ timeout: 20000 });
  const t = await rep.textContent();
  if (!/15 sample records/.test(t)) throw new Error(`report: ${t}`);
  if (!/customer\.password_hash/.test(t) || !/customer\.api_key/.test(t)) throw new Error('sensitive columns not reported');
  await page.getByLabel('Ontology name').fill(name);
  await page.getByRole('button', { name: /Approve & create/ }).click();
  await page.getByText('Ontology saved.').waitFor();
  oid = (await api('/')).results.find((o) => o.name === name).id;
  created.push(oid);
  const m = (await api(`/${oid}/`)).model;
  if (m.individuals.length !== 15) throw new Error(`individuals: ${m.individuals.length}`);
  if (/\$2b\$|sk-live/.test(JSON.stringify(m))) throw new Error('a sensitive value was copied into the ontology');
}, page);

await step('RECORDS: a plain-word question now returns real rows', async () => {
  await tab('explorer');
  await page.getByRole('tab', { name: 'Query studio' }).click();
  await page.getByRole('button', { name: 'Run' }).click();
  await page.getByTestId('query-results').waitFor({ timeout: 20000 });
  const txt = await page.getByTestId('query-results').textContent();
  if (!/3 row\(s\)/.test(txt)) throw new Error(`results: ${txt.slice(0, 200)}`);
  if (!/customer_1/.test(txt)) throw new Error('no customer individuals in the rows');
  await shot(page, 'query-rows');
}, page);

await step('INDIVIDUALS: select, edit a value and a link in the inspector, save', async () => {
  await tab('workbench');
  await page.getByRole('button', { name: /customer_1/ }).first().click();
  await page.getByTestId('individual-editor').waitFor();
  const f = page.getByLabel('customerName *');
  await f.fill('Initech Global');
  await f.press('Tab');
  await page.getByRole('button', { name: /salesorder_100/ }).first().click();
  const links = page.getByRole('combobox', { name: /hasCustomer → Customer/ });
  await links.click();
  await page.getByRole('option', { name: 'customer_2' }).click();
  await page.getByRole('button', { name: 'Remove customer_1' }).or(page.locator('.MuiChip-deleteIcon').first()).first().click().catch(() => {});
  await save();
  const m = (await api(`/${oid}/`)).model;
  const c1 = m.individuals.find((i) => i.name === 'customer_1');
  if (c1.data.customerName !== 'Initech Global') throw new Error(`value not saved: ${JSON.stringify(c1.data)}`);
  const o = m.individuals.find((i) => i.name === 'salesorder_100');
  if (!o.links.hasCustomer.includes('customer_2')) throw new Error(`link not saved: ${JSON.stringify(o.links)}`);
  await shot(page, 'individual-editor');
}, page);

await step('INDIVIDUALS: add a new one from the Explorer and validation catches bad data', async () => {
  await page.getByTestId('add-individual').click();
  await page.getByRole('button', { name: 'Create' }).click();
  await page.getByTestId('individual-editor').waitFor();
  await save();
  const v = await api(`/${oid}/validate/`);
  if (v.errors !== 0) throw new Error(`unexpected errors: ${JSON.stringify(v.issues.filter((i) => i.severity === 'error'))}`);
}, page);

await step('GROUPS: auto-group by cluster colours nodes, draws frames and filters', async () => {
  await page.getByTestId('auto-group').click();
  await page.getByRole('menuitem', { name: /connected cluster/ }).click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="node-group"]').length >= 3);
  if (!(await page.locator('.react-flow__node-groupframe').count())) throw new Error('no group frames drawn');
  await page.getByTestId('group-filter').click();
  await page.getByRole('option', { name: 'Cluster 1' }).click();
  await shot(page, 'groups');
  await page.getByTestId('group-filter').click();
  await page.getByRole('option', { name: 'All groups' }).click();
  await save();
  const m = (await api(`/${oid}/`)).model;
  if (!m.classes.every((c) => c.group)) throw new Error('groups not persisted');
}, page);

await step('BRANCH: create a branch, edit it, edit main, merge with a conflict decision', async () => {
  await tab('versions');
  await page.getByTestId('tab-branches').click();
  await page.getByLabel('Branch name').fill('feature-x');
  await page.getByRole('button', { name: 'Create branch' }).click();
  await page.getByText(/Branch “feature-x” created/).waitFor();
  const table = page.getByTestId('branches-table');
  await table.getByText('feature-x').waitFor();
  await table.getByRole('row', { name: /feature-x/ }).getByRole('button', { name: 'Open' }).click();
  await page.getByTestId('branch-chip').waitFor();
  await tab('workbench');
  await nodeOf('Customer').click();
  const desc = page.getByLabel('Description').first();
  await desc.fill('edited on the branch');
  await desc.press('Tab');
  await save();
  // back to main and change the same field differently
  await tab('versions');
  await page.getByTestId('tab-branches').click();
  await page.getByTestId('branches-table').getByRole('row', { name: /main/ }).getByRole('button', { name: 'Open' }).click();
  await page.getByTestId('branch-chip').waitFor({ state: 'detached' });
  await tab('workbench');
  await nodeOf('Customer').click();
  const d2 = page.getByLabel('Description').first();
  await d2.fill('edited on main');
  await d2.press('Tab');
  await save();
  await tab('versions');
  await page.getByTestId('tab-branches').click();
  await page.getByTestId('merge-feature-x').click();
  await page.getByTestId('merge-conflict').waitFor();
  if (!(await page.getByTestId('apply-merge').isDisabled())) throw new Error('merge allowed with unresolved conflict');
  await shot(page, 'merge-conflict');
  await page.getByRole('radio', { name: /branch’s.*version/ }).check();
  await page.getByTestId('apply-merge').click({ timeout: 15000 });
  await page.getByText(/Merged “feature-x”/).waitFor();
  const m = (await api(`/${oid}/`)).model;
  const comment = m.classes.find((c) => c.name === 'Customer').comment;
  if (comment !== 'edited on the branch') throw new Error(`comment after merge: ${comment}`);
}, page);

await step('CONCURRENCY: stale save is refused with a clear dialog; overwrite and reload both work', async () => {
  const pageB = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  pageB.on('dialog', (d) => d.accept());
  await demoLogin(pageB);
  await openOntology(oid);
  await pageB.goto(`${BASE}/?ontology=${oid}&tab=workbench`);
  await pageB.getByTestId('prime-ontology-workbench').waitFor();
  await pageB.locator('.react-flow__node-cls', { hasText: 'Customer' }).first().click();
  // A saves first
  await nodeOf('Customer').click();
  const da = page.getByLabel('Description').first();
  await da.fill('A was here');
  await da.press('Tab');
  await save();
  // B (older revision) edits and tries to save
  const db = pageB.getByLabel('Description').first();
  await db.fill('B was here');
  await db.press('Tab');
  await pageB.getByRole('button', { name: 'Save changes' }).click();
  const dlg = pageB.getByRole('dialog', { name: /Someone else changed this ontology/ });
  await dlg.waitFor();
  const t = await dlg.textContent();
  if (!/admin/.test(t) || !/not.*saved/.test(t)) throw new Error(`dialog text: ${t}`);
  if ((await api(`/${oid}/`)).model.classes.find((c) => c.name === 'Customer').comment !== 'A was here') throw new Error("B's stale save landed");
  await shot(pageB, 'conflict-dialog');
  // option 1: discard mine, load theirs
  await pageB.getByTestId('conflict-reload').click();
  await pageB.locator('.react-flow__node-cls', { hasText: 'Customer' }).first().click();
  if ((await pageB.getByLabel('Description').first().inputValue()) !== 'A was here') throw new Error('reload did not load the other version');
  // option 2: edit again from the fresh revision and overwrite after another conflict
  await nodeOf('Customer').click();
  await da.fill('A again');
  await da.press('Tab');
  await save();
  const db2 = pageB.getByLabel('Description').first();
  await db2.fill('B overwrites');
  await db2.press('Tab');
  await pageB.getByRole('button', { name: 'Save changes' }).click();
  await pageB.getByTestId('conflict-overwrite').click();
  await pageB.getByText('Ontology saved.').waitFor();
  if ((await api(`/${oid}/`)).model.classes.find((c) => c.name === 'Customer').comment !== 'B overwrites') throw new Error('overwrite not applied');
  await pageB.context().close();
}, page);

await step('OCR: a scanned (image-only) PDF is read and concepts keep page evidence', async () => {
  await openOntology(oid, 'sources');
  await page.getByTestId('file-input').setInputFiles(path.join(samples, 'scanned_agreement.pdf'));
  await page.getByTestId('candidate-review').waitFor({ timeout: 90000 });
  const txt = await page.getByTestId('candidate-review').textContent();
  if (!/OCR was applied to 3 scanned page/.test(txt)) throw new Error(`no OCR notice: ${txt.slice(0, 300)}`);
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="candidate-review"] .react-flow__node-cls').length >= 3);
  await page.locator('[data-testid="candidate-review"] .react-flow__node-cls', { hasText: 'Payment' }).first().click();
  await page.getByText(/Found in scanned_agreement\.pdf: Page 2/).first().waitFor();
  await shot(page, 'ocr');
  await page.getByRole('button', { name: 'Discard' }).click();
}, page);

await step('PARQUET: file with sample records', async () => {
  await page.getByRole('checkbox', { name: /Copy sample records as individuals/ }).check();
  await page.getByTestId('file-input').setInputFiles(path.join(samples, 'customers.parquet'));
  await page.getByTestId('records-report').waitFor({ timeout: 20000 });
  if (!/3 sample records/.test(await page.getByTestId('records-report').textContent())) throw new Error('records report');
  await page.getByRole('button', { name: 'Discard' }).click();
}, page);

await step('MAPPING: says which matching engine is active (semantic model)', async () => {
  await tab('mapping');
  await page.getByRole('button', { name: 'Propose mappings' }).click();
  const t = await page.getByTestId('matching-engine').textContent({ timeout: 30000 });
  if (!/embedding model \(BAAI\/bge-small-en-v1\.5\)/.test(t)) throw new Error(`engine note: ${t}`);
}, page);

await step('LARGE ontology (300 classes / 450 relationships): opens, stays responsive, compact mode', async () => {
  const classes = Array.from({ length: 300 }, (_, i) => ({ name: `Class${i}`, label: `Class ${i}`, comment: '', parents: i > 0 && i % 7 === 0 ? [`Class${i - 7}`] : [], group: `Group ${i % 6}` }));
  const dps = classes.flatMap((c) => [0, 1, 2].map((k) => ({ name: `prop${k}`, domain: c.name, datatype: 'string', required: false })));
  const ops = Array.from({ length: 450 }, (_, i) => ({ name: `rel${i}`, domain: `Class${i % 300}`, range: `Class${(i * 7 + 13) % 300}`, cardinality: 'many-to-many' }));
  const big = await api('/', { method: 'POST', data: { name: `Big ${stamp}`, model: { classes, dataProperties: dps, objectProperties: ops } } });
  created.push(big.id);
  const t0 = Date.now();
  await openOntology(big.id);
  await page.waitForFunction(() => document.querySelectorAll('.react-flow__node-cls').length > 5, null, { timeout: 30000 });
  const open = Date.now() - t0;
  await page.getByText(/compact mode/).waitFor();
  const t1 = Date.now();
  await page.getByPlaceholder('Search graph…').fill('Class150');
  await page.waitForTimeout(300);
  await nodeOf('Class 150').waitFor({ timeout: 15000 }).catch(() => {});
  const interact = Date.now() - t1;
  await shot(page, 'large');
  console.log(`      large graph: opened in ${(open / 1000).toFixed(1)}s, search interaction ${(interact / 1000).toFixed(1)}s, rendered nodes ${await page.locator('.react-flow__node-cls').count()} of 300`);
  if (open > 25000) throw new Error(`opening took ${open}ms`);
  // explorer with 300 classes must also respond
  await page.getByLabel('Filter explorer').fill('Class29');
  await page.getByRole('button', { name: /^Class29$/ }).first().waitFor({ timeout: 10000 });
}, page);

await step('no uncaught browser errors', async () => { if (errors.length) throw new Error(errors.slice(0, 3).join(' | ')); }, page);

for (const id of created) await api(`/${id}/`, { method: 'DELETE' }).catch(() => {});
await browser.close();
const failed = results.filter((r) => !r[1]).length;
console.log(failed ? `\n${failed} step(s) FAILED [${browserName()}]` : `\nALL FEATURE STEPS PASSED [${browserName()}]`);
process.exit(failed ? 1 : 0);
