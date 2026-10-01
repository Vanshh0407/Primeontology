// Shared helpers for the browser tests. BROWSER = chrome (default) | edge | firefox | webkit
import fs from 'node:fs';
import { chromium, firefox, webkit } from 'playwright-core';

export const BASE = process.env.BASE_URL || 'http://localhost:3008';
const CHROME = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe'].find(fs.existsSync);
const EDGE = ['C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(fs.existsSync);

export const browserName = () => (process.env.BROWSER || 'chrome').toLowerCase();

export async function launch() {
  const b = browserName();
  if (b === 'firefox') return firefox.launch({ headless: true });
  if (b === 'webkit') return webkit.launch({ headless: true });
  if (b === 'edge') return chromium.launch({ executablePath: EDGE, headless: true });
  return chromium.launch({ executablePath: CHROME || EDGE, headless: true });
}

/** Sign in through the real login page using the Demo login button. */
export async function demoLogin(page, { user, pass } = {}) {
  await page.goto(`${BASE}/`);
  await page.getByTestId('login-page').waitFor();
  if (user) {
    await page.getByLabel('Username', { exact: true }).fill(user);
    await page.getByLabel('Password', { exact: true }).fill(pass);
  } else {
    await page.getByRole('button', { name: 'Demo login' }).click();
  }
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByTestId('prime-ontology-workbench').waitFor();
}

export function makeRunner() {
  let failed = 0;
  const results = [];
  const step = async (name, fn, page) => {
    const t = Date.now();
    try {
      await fn();
      console.log(`PASS ${name} (${((Date.now() - t) / 1000).toFixed(1)}s)`);
      results.push([name, true]);
    } catch (e) {
      failed += 1;
      results.push([name, false]);
      console.log('FAIL', name, '\n   ', e.message.split('\n').slice(0, 5).join('\n    '));
      if (page) await page.screenshot({ path: `e2e/screenshots/FAIL-${browserName()}-${failed}.png` }).catch(() => {});
    }
  };
  return { step, get failed() { return failed; }, results };
}

/** Collects uncaught page errors / console errors, ignoring noise unrelated to the app. */
export function watchErrors(page, errors) {
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => {
    if (m.type() === 'error' && !/favicon|Failed to load resource|fonts\.g|net::ERR/.test(m.text())) errors.push(m.text());
  });
}
