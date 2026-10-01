// End-to-end smoke test: drives the real UI in Chrome against the live backend + MySQL.
// Usage: MYSQL_TEST_PASSWORD=... node e2e/smoke.mjs   (env: BASE_URL, MYSQL_TEST_USER, MYSQL_TEST_DB, MYSQL_TEST_HOST)
import { launch } from './lib.mjs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import fs from 'node:fs';

const BASE = process.env.BASE_URL || 'http://localhost:3008';
const here = path.dirname(fileURLToPath(import.meta.url));
const samples = path.resolve(here, '../../demo/samples');
const shots = path.resolve(here, 'screenshots');
fs.mkdirSync(shots, { recursive: true });
const chrome = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(fs.existsSync);

let failed = 0;
const step = async (name, fn) => {
  try { await fn(); console.log('PASS', name); } catch (e) {
    failed++;
    console.log('FAIL', name, '\n   ', e.message.split('\n').slice(0, 5).join('\n    '));
    await page.screenshot({ path: `${shots}/FAIL-${failed}.png` }).catch(() => {});
  }
};

const browser = await launch();
const page = await (await browser.newContext({ viewport: { width: 1500, height: 900 } })).newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
page.on('console', (m) => m.type() === 'error' && !/favicon|Failed to load resource/.test(m.text()) && errors.push(m.text()));
page.on('dialog', (d) => d.accept());
const api = async (p, opts) => (await page.request.fetch(`${BASE}/api/v1/ontology${p}`, opts)).json();
const nodes = () => page.locator('.react-flow__node').count();
const tab = (id) => page.getByTestId(`tab-${id}`).click();
const ontologyName = `E2E ${Date.now()}`;
let ontologyId;

const apiLogin = (u, p) => page.request.post(`${BASE}/api/v1/ontology/auth/login/`, { data: { username: u, password: p }, headers: { 'X-Prime-Client': 'workbench' } });

await page.goto(BASE + '/');

await step('LOGIN: unauthenticated user sees the login page; API is closed', async () => {
  await page.getByTestId('login-page').waitFor();
  if ((await page.request.get(`${BASE}/api/v1/ontology/`)).status() !== 403) throw new Error('API open without login');
  if (await page.getByTestId('prime-ontology-workbench').count()) throw new Error('workbench visible before login');
});

await step('LOGIN: wrong password is rejected with an error', async () => {
  await page.getByLabel('Username', { exact: true }).fill('admin');
  await page.getByLabel('Password', { exact: true }).fill('nope-nope');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByTestId('login-error').getByText('Invalid username or password.').waitFor();
});

await step('LOGIN: "Demo login" fills the credentials, Sign in opens the workbench', async () => {
  await page.reload();
  await page.getByRole('button', { name: 'Demo login' }).click();
  if ((await page.getByLabel('Username', { exact: true }).inputValue()) !== 'admin') throw new Error('username not filled');
  if ((await page.getByLabel('Password', { exact: true }).inputValue()) !== 'admin123') throw new Error('password not filled');
  await page.screenshot({ path: `${shots}/0-login-filled.png` });
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByTestId('prime-ontology-workbench').waitFor();
  await page.getByTestId('current-user').getByText('admin · admin').waitFor();
});

await step('R1 MySQL: connect, inspect schema, generate candidate, approve', async () => {
  await page.getByLabel('User', { exact: true }).fill(process.env.MYSQL_TEST_USER || 'root');
  await page.getByLabel('Password', { exact: true }).fill(process.env.MYSQL_TEST_PASSWORD || '');
  await page.getByLabel('Database / schema').fill(process.env.MYSQL_TEST_DB || 'primeontology_demo');
  if (process.env.MYSQL_TEST_HOST) await page.getByLabel('Host').fill(process.env.MYSQL_TEST_HOST);
  await page.getByRole('button', { name: 'Inspect schema' }).click();
  await page.getByText('Schema preview — 5 tables').waitFor({ timeout: 15000 });
  await page.getByRole('button', { name: 'Generate ontology' }).click();
  await page.getByTestId('candidate-review').waitFor({ timeout: 15000 });
  await page.getByText('5 classes').first().waitFor();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="candidate-review"] .react-flow__node').length === 5);
  await page.screenshot({ path: `${shots}/1-candidate.png` });
  await page.getByLabel('Ontology name').fill(ontologyName);
  await page.getByRole('button', { name: /Approve & create/ }).click();
  await page.getByText('Ontology saved.').waitFor();
  ontologyId = (await api('/')).results.find((o) => o.name === ontologyName).id;
  if (!ontologyId) throw new Error('not persisted');
});

await step('R2 workbench: graph renders, select class, edit relationship live, save', async () => {
  await tab('workbench');
  await page.waitForFunction(() => document.querySelectorAll('.react-flow__node').length === 5);
  await page.locator('.react-flow__node', { hasText: 'Sales Order' }).first().click();
  await page.getByTestId('class-editor').waitFor();
  await page.getByText('hasCustomer → Customer').click();
  await page.getByTestId('relationship-editor').waitFor();
  const name = page.getByLabel('Name', { exact: true });
  await name.fill('createsOrder');
  await name.press('Enter');
  await page.locator('.react-flow__edge-textwrapper', { hasText: 'createsOrder' }).first().waitFor({ timeout: 5000 });
  await page.screenshot({ path: `${shots}/2-workbench.png` });
  await page.getByRole('button', { name: 'Save changes' }).click();
  await page.getByText('Ontology saved.').waitFor();
  const o = await api(`/${ontologyId}/`);
  if (!o.model.objectProperties.some((p) => p.name === 'createsOrder')) throw new Error('edit not persisted');
  if (Object.keys(o.model.layout || {}).length !== 5) throw new Error('layout not persisted');
});

await step('R2 workbench: add class + inheritance via UI, undo', async () => {
  await page.getByRole('button', { name: 'Class', exact: true }).click();
  await page.getByLabel('Class name').fill('PremiumCustomer');
  await page.getByRole('combobox', { name: /Parent class/ }).fill('Customer');
  await page.getByRole('option', { name: 'Customer' }).click();
  await page.getByRole('button', { name: 'Create' }).click();
  await page.waitForFunction(() => document.querySelectorAll('.react-flow__node').length === 6);
  await page.getByRole('button', { name: 'Undo last edit' }).or(page.locator('button:has(svg[data-testid="UndoIcon"])')).first().click();
  await page.waitForFunction(() => document.querySelectorAll('.react-flow__node').length === 5);
  // invalid name rejected with message
  await page.getByRole('button', { name: 'Class', exact: true }).click();
  await page.getByLabel('Class name').fill('bad name');
  await page.getByRole('button', { name: 'Create' }).click();
  await page.getByText(/Use letters, digits/).waitFor();
  await page.keyboard.press('Escape');
});

await step('R3 universal import: CSV upload -> review -> merge into ontology', async () => {
  await tab('sources');
  await page.getByTestId('file-input').setInputFiles(path.join(samples, 'customers.csv'));
  await page.getByTestId('candidate-review').waitFor({ timeout: 15000 });
  await page.getByRole('tab', { name: 'Source → ontology mapping' }).click();
  await page.getByText('Customer.customerName').first().waitFor();
  await page.getByRole('button', { name: /Merge into/ }).click();
  await page.getByText(/Merged into/).waitFor();
});

await step('R4 documents: contract text -> concepts with page/section provenance', async () => {
  await tab('sources');
  await page.getByTestId('file-input').setInputFiles(path.join(samples, 'services_agreement.txt'));
  await page.getByTestId('candidate-review').waitFor({ timeout: 15000 });
  await page.locator('.react-flow__node', { hasText: 'Obligation' }).first().click();
  await page.getByText(/Found in services_agreement.txt/).first().waitFor();
  await page.screenshot({ path: `${shots}/4-document.png` });
  await page.getByRole('button', { name: 'Discard' }).click();
});

await step('R5 mapping: heterogeneous sources -> Customer.customerName, accept, save, commit', async () => {
  await tab('mapping');
  await page.getByRole('button', { name: 'Propose mappings' }).click();
  await page.getByTestId('mapping-table').waitFor();
  const row = page.locator('tr', { hasText: 'cust_nm' });
  await row.getByText('Customer.customerName').first().waitFor();
  await page.getByRole('button', { name: 'Accept all HIGH' }).click();
  await page.screenshot({ path: `${shots}/5-mapping.png` });
  await page.getByRole('button', { name: /Save mapping set/ }).click();
  await page.getByText(/Saved “Mapping set”/).waitFor();
  await page.getByRole('button', { name: 'Commit' }).first().click();
  await page.getByText(/Recorded \d+ mapping lineage/).waitFor();
});

await step('R6 validation + reasoning + SHACL', async () => {
  await tab('validation');
  await page.getByTestId('health').waitFor();
  await page.screenshot({ path: `${shots}/6-validation.png` });
  await page.getByRole('tab', { name: 'Reasoning (RDFS / OWL)' }).click();
  await page.getByRole('button', { name: 'Run reasoner' }).click();
  await page.getByTestId('reasoning').waitFor();
  await page.getByRole('tab', { name: 'SHACL' }).click();
  await page.getByRole('button', { name: /Generate SHACL/ }).click();
  await page.locator('textarea').first().waitFor();
  await page.waitForFunction(() => [...document.querySelectorAll('textarea')].some((t) => t.value.includes('NodeShape')));
});

await step('R7 explorer + query: semantic search, concept card, natural + SPARQL query', async () => {
  await tab('explorer');
  await page.getByTestId('semantic-search').locator('input').fill('customer');
  await page.getByRole('button', { name: /^Customer/ }).first().click().catch(async () => page.getByText('Customer', { exact: true }).first().click());
  await page.getByTestId('concept-card').waitFor();
  await page.getByRole('tab', { name: 'Query studio' }).click();
  await page.getByRole('button', { name: 'Run' }).click();
  await page.getByText(/Interpretation:/).waitFor();
  await page.getByRole('button', { name: 'SPARQL' }).click();
  await page.getByRole('button', { name: 'All classes' }).click();
  await page.getByRole('button', { name: 'Run' }).click();
  await page.getByTestId('query-results').waitFor();
  await page.screenshot({ path: `${shots}/7-query.png` });
  await page.getByTestId('query-input').locator('textarea').first().fill('DROP ALL');
  await page.getByRole('button', { name: 'Run' }).click();
  await page.getByText(/read-only/).waitFor();
});

await step('R8 governance: commit -> submit -> (four-eyes) -> diff -> audit', async () => {
  await tab('versions');
  await page.getByLabel('Commit message').fill('first release');
  await page.getByRole('button', { name: 'Commit version' }).click();
  await page.getByText('Version committed.').waitFor();
  await page.getByTestId('versions-table').getByText('v1.0').waitFor();
  await page.getByRole('button', { name: 'Submit for review' }).click();
  await page.getByText(/Submit for review: done/).waitFor();
  // Same user authored the version: the UI explains and disables Approve (four-eyes), and the server still enforces it.
  await page.getByTestId('four-eyes-note').waitFor();
  if (!(await page.getByRole('button', { name: 'Approve' }).isDisabled())) throw new Error('Approve must be disabled for the version author');
  const forced = await page.request.post(`${BASE}/api/v1/ontology/${ontologyId}/versions/1.0/approve/`, { headers: { 'X-Prime-Client': 'workbench' } });
  if (forced.status() === 200 || !/Four-eyes/.test(await forced.text())) throw new Error('server did not enforce four-eyes');
  await page.getByRole('tab', { name: /Audit trail/ }).click();
  await page.getByText('version.submit').first().waitFor();
  await page.screenshot({ path: `${shots}/8-versions.png` });
});

await step('R5 assistant: proposal -> diff -> approve (never silent)', async () => {
  await tab('assistant');
  await page.getByTestId('assistant-input').locator('input').fill('Add class Warranty');
  await page.getByRole('button', { name: 'Propose' }).click();
  await page.getByTestId('proposal').waitFor();
  await page.getByText('+ Class: Warranty').waitFor();
  if ((await api(`/${ontologyId}/`)).model.classes.some((c) => c.name === 'Warranty')) throw new Error('changed before approval');
  await page.getByRole('button', { name: 'Approve & apply' }).click();
  await page.getByText(/Proposal applied/).waitFor();
  if (!(await api(`/${ontologyId}/`)).model.classes.some((c) => c.name === 'Warranty')) throw new Error('not applied');
});

await step('R10 agentic: register tool + policy, plan respects policy', async () => {
  await page.goto(`${BASE}/?context=unicontractai&ontology=${ontologyId}&role=admin&user=e2e.admin`);
  await page.getByTestId('prime-ontology-workbench').waitFor();
  // the host's capability list arrives asynchronously; the tab must be gone once it has
  await page.waitForFunction(() => !document.querySelector('[data-testid="tab-agentic"]'), null, { timeout: 8000 }).catch(() => { throw new Error('Agents tab must stay hidden for UniContractAI (contract-focused host)'); });
  await page.goto(`${BASE}/?context=primeagenticos&ontology=${ontologyId}&role=admin&user=e2e.admin`);
  await tab('agentic');
  await page.getByRole('button', { name: '+ Tool' }).waitFor();
  await page.waitForFunction(() => { const b = [...document.querySelectorAll('button')].find((x) => x.textContent.trim().toLowerCase() === '+ tool'); return b && !b.disabled; });
  await page.getByRole('button', { name: '+ Tool' }).click();
  await page.getByRole('button', { name: '+ Policy' }).click();
  const conceptBoxes = page.getByRole('combobox', { name: /concepts/ });
  await conceptBoxes.nth(0).fill('Customer'); await page.getByRole('option', { name: 'Customer', exact: true }).click();
  await conceptBoxes.nth(1).fill('Customer'); await page.getByRole('option', { name: 'Customer', exact: true }).click();
  await page.getByRole('button', { name: 'Save registry' }).click();
  await page.getByText(/Agent registry saved/).waitFor();
  await page.getByTestId('plan-input').locator('input').fill('Delete the customer');
  await page.getByRole('button', { name: 'Plan', exact: true }).click();
  await page.getByTestId('plan').getByText('needs approval').waitFor();
  await page.screenshot({ path: `${shots}/10-agentic.png` });
});

await step('R9 embedding: same component in 3 host contexts + viewer role is read-only', async () => {
  // sign out (via UI) and sign in as the read-only viewer user
  await page.goto(`${BASE}/?ontology=${ontologyId}`);
  await page.getByTestId('logout').click();
  await page.getByTestId('login-page').waitFor();
  await page.getByLabel('Username', { exact: true }).fill('viewer');
  await page.getByLabel('Password', { exact: true }).fill('viewer123');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByTestId('current-user').getByText('viewer · viewer').waitFor();
  for (const host of ['unicontractai', 'primesemonto', 'primeagenticos']) {
    await page.goto(`${BASE}/?context=${host}&ontology=${ontologyId}&tab=workbench`);
    await page.getByTestId('prime-ontology-workbench').waitFor();
    const attr = await page.getByTestId('prime-ontology-workbench').getAttribute('data-context');
    if (attr !== host) throw new Error('context not applied');
    await page.waitForFunction(() => document.querySelectorAll('.react-flow__node').length >= 5);
    if (await page.getByRole('button', { name: 'Save changes' }).isEnabled().catch(() => false)) throw new Error('viewer can save');
    if (await page.getByRole('button', { name: 'Class', exact: true }).count()) throw new Error('viewer sees edit controls');
  }
  await page.screenshot({ path: `${shots}/9-embedded-agentic.png` });
  const t = await page.getByText(/PrimeAgentic OS/).first().textContent();
  if (!t) throw new Error('host title missing');
});

await step('no uncaught browser errors', async () => { if (errors.length) throw new Error(errors.slice(0, 3).join(' | ')); });

await apiLogin('admin', 'admin123');
await api(`/${ontologyId}/`, { method: 'DELETE', headers: { 'X-Prime-Client': 'workbench' } }).catch(() => {});
await browser.close();
console.log(failed ? `\n${failed} step(s) FAILED` : '\nALL E2E STEPS PASSED');
process.exit(failed ? 1 : 0);
