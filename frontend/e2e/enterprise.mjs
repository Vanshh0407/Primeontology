// Browser tests for the R11-R15 screens. Needs both servers, `seed_demo_users` and `seed_enterprise_demo`.
//   BROWSER=chrome|edge node e2e/enterprise.mjs
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BASE, browserName, demoLogin, launch, makeRunner, watchErrors } from './lib.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
fs.mkdirSync(path.resolve(here, 'screenshots'), { recursive: true });
const shot = (page, n) => page.screenshot({ path: path.resolve(here, 'screenshots', `${browserName()}-ent-${n}.png`) }).catch(() => {});

const browser = await launch();
const page = await (await browser.newContext({ viewport: { width: 1500, height: 950 } })).newPage();
const errors = [];
watchErrors(page, errors);
page.on('dialog', (d) => d.accept());
const { step, results } = makeRunner();
const H = { 'X-Prime-Client': 'workbench' };
let oid;
const open = async (tab) => { await page.goto(`${BASE}/?ontology=${oid}&tab=${tab}`); await page.getByTestId('prime-ontology-workbench').waitFor(); await page.getByTestId(`tab-${tab}`).click(); };

await step('login and find the Enterprise Demo ontology', async () => {
  await demoLogin(page);
  const list = await (await page.request.fetch(`${BASE}/api/v1/ontology/`, { headers: H })).json();
  oid = (list.results || list).find((o) => o.name === 'Enterprise Demo')?.id;
  if (!oid) throw new Error('run: python manage.py seed_enterprise_demo');
}, page);

await step('SIDEBAR: every feature is reachable from the left navigation', async () => {
  await open('workbench');
  for (const id of ['sources', 'workbench', 'mapping', 'validation', 'explorer', 'versions', 'assistant', 'rag', 'twin', 'fabric', 'agentic', 'autonomy', 'os']) {
    if (!(await page.getByTestId(`tab-${id}`).count())) throw new Error(`missing sidebar item ${id}`);
  }
}, page);

await step('FABRIC: sources fresh, entities resolved across systems, drill-in shows provenance', async () => {
  await open('fabric');
  await page.getByText('freshness: FRESH').waitFor();
  await page.getByText(/\d+ linked across systems/).waitFor();
  await page.getByRole('row', { name: /Ann Lee/ }).first().click();
  await page.getByText('Golden record').waitFor();
  await page.getByText(/SAP|Salesforce/).first().waitFor();
  await shot(page, 'fabric');
}, page);

await step('RAG: the document\'s structured question, grounded answer with citations', async () => {
  await open('rag');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await page.getByTestId('rag-answer').waitFor();
  const txt = await page.getByTestId('rag-answer').innerText();
  if (!/Cobalt Services|Steel Supply/.test(txt)) throw new Error(`answer lacks the expected contracts: ${txt.slice(0, 200)}`);
  if (!/grounded/i.test(txt)) throw new Error('no grounding label');
  await shot(page, 'rag');
}, page);

await step('RAG: unrelated question abstains instead of guessing', async () => {
  await page.getByRole('textbox').first().fill('What is the airspeed velocity of an unladen swallow?');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await page.getByText(/could not find enough evidence/).waitFor();
}, page);

await step('TWIN: what-if is non-destructive and shows the alert it would cause; graph view renders', async () => {
  await open('twin');
  const inputs = page.locator('input[type="text"], input:not([type])');
  await page.getByLabel('Entity id').fill('1');
  await page.getByLabel('Attribute', { exact: true }).fill('creditLimit');
  await page.getByLabel('New value').fill('100');
  await page.getByRole('button', { name: 'Simulate' }).click();
  await page.getByTestId('twin-sim').waitFor();
  const t = await page.getByTestId('twin-sim').innerText();
  if (!/not persisted/.test(t) || !/availableCredit/.test(t)) throw new Error(`unexpected simulation text: ${t}`);
  await page.getByRole('button', { name: /Customers \(\d+\)/ }).click();
  await page.locator('svg[aria-label="Graph view"] circle').first().waitFor();
  await shot(page, 'twin');
  void inputs;
}, page);

await step('AUTONOMY: a change needs approval; the requester cannot approve their own request', async () => {
  await open('autonomy');
  await page.getByRole('textbox').first().fill('Set Ann Lee credit limit to 8000');
  await page.getByRole('button', { name: 'Run', exact: true }).click();
  await page.getByTestId('agent-run').waitFor();
  await page.getByText(/awaiting approval/i).first().waitFor();
  const approve = page.getByRole('button', { name: 'Approve' }).first();
  await approve.waitFor();
  if (!(await approve.isDisabled())) throw new Error('four-eyes: the requester could approve their own request');
  await shot(page, 'autonomy');
}, page);

await step('AUTONOMY: export the plan as n8n JSON and BPMN', async () => {
  for (const [label, ext] of [['Export n8n', 'json'], ['Export BPMN', 'bpmn']]) {
    const [dl] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: label }).click()]);
    if (!dl.suggestedFilename().endsWith(ext)) throw new Error(`unexpected file ${dl.suggestedFilename()}`);
  }
}, page);

await step('AUTONOMY: administrators can register an agent from the UI', async () => {
  await page.getByRole('button', { name: 'Register agent' }).click();
  await page.getByRole('dialog').getByLabel('Name', { exact: true }).fill(`E2E Agent ${Date.now() % 100000}`);
  await page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByText('Agent saved').waitFor();
}, page);

await step('OS: health, policies, policy evaluation and routing', async () => {
  await open('os');
  await page.getByText(/^fabric:/).waitFor();
  await page.getByRole('button', { name: 'Evaluate' }).click();
  await page.getByText(/by risk|require approval|allow|deny/).first().waitFor();
  await page.getByRole('textbox').first().fill('What if Ann Lee credit limit goes to 200?');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await page.getByText('routed to twin').waitFor();
  await shot(page, 'os');
}, page);

await step('no uncaught page errors on the new screens', async () => {
  if (errors.length) throw new Error(errors.slice(0, 3).join(' | '));
}, page);

await browser.close();
const failed = results.filter(([, ok]) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
