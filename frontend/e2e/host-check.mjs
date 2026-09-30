// Verifies a built Next.js host renders the embedded workbench. usage: node e2e/host-check.mjs <url> <expectedContext> <expectedTitle>
import { chromium } from 'playwright-core';
import fs from 'node:fs';
const [url, ctx, title] = process.argv.slice(2);
const chrome = ['C:/Program Files/Google/Chrome/Application/chrome.exe'].find(fs.existsSync);
const b = await chromium.launch({ executablePath: chrome, headless: true });
const p = await b.newPage({ viewport: { width: 1400, height: 850 } });
const errs = [];
p.on('pageerror', (e) => errs.push(e.message));
await p.goto(url);
await p.getByTestId('prime-ontology-workbench').waitFor({ timeout: 20000 });
const got = await p.getByTestId('prime-ontology-workbench').getAttribute('data-context');
await p.getByText(title).first().waitFor({ timeout: 10000 });
await p.getByTestId('tab-workbench').click();
await p.waitForFunction(() => document.querySelectorAll('.react-flow__node').length >= 5, null, { timeout: 15000 });
const agentsTab = await p.getByTestId('tab-agentic').count();
console.log(JSON.stringify({ context: got, ok: got === ctx, agentsTab, pageErrors: errs.length }));
await b.close();
process.exit(got === ctx && !errs.length ? 0 : 1);
