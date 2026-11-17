'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');

const source = path.resolve(process.argv[2] || '');
const privateRoot = path.resolve(process.argv[3] || '');
const baseUrl = process.argv[4]; // e.g. http://127.0.0.1:PORT/v1
const text = process.argv[5] || 'Reply with one short sentence.';
const model = process.env.PHISTORY_ORKAS_MODEL || 'gpt-4.1';
const fakeKey = process.env.PHISTORY_ORKAS_FAKE_KEY || 'phistory-fake-api-key';
if (!fs.existsSync(path.join(source, 'bootstrap.cjs')) || !baseUrl || !process.argv[3]) {
  throw new Error('Usage: node DRIVER SOURCE PRIVATE_ROOT TAP_BASE_URL TASK');
}
const repoRequire = createRequire(path.join(source, 'package.json'));
const { _electron: electron, expect } = repoRequire('@playwright/test');

async function main() {
  const childEnv = { ...process.env };
  for (const key of Object.keys(childEnv)) {
    if (key.startsWith('ORKAS_E2E_') || key.startsWith('ORKAS_TEST_')) delete childEnv[key];
  }
  for (const key of ['ELECTRON_RUN_AS_NODE', 'CORE_AGENT_AUTH_DIR', 'ORKAS_OPENCODE_PATH',
    'ORKAS_HERMES_PATH', 'ORKAS_CODEX_PATH', 'ORKAS_CLAUDE_PATH']) delete childEnv[key];
  const profile = path.join(privateRoot, 'electron-user-data');
  for (const dir of ['home', 'xdg-config', 'xdg-cache', 'xdg-data', 'workspace', 'electron-user-data']) {
    fs.mkdirSync(path.join(privateRoot, dir), { recursive: true, mode: 0o700 });
  }
  Object.assign(childEnv, {
    HOME: path.join(privateRoot, 'home'),
    XDG_CONFIG_HOME: path.join(privateRoot, 'xdg-config'),
    XDG_CACHE_HOME: path.join(privateRoot, 'xdg-cache'),
    XDG_DATA_HOME: path.join(privateRoot, 'xdg-data'),
    ORKAS_WORKSPACE_ROOT: path.join(privateRoot, 'workspace'),
    TZ: 'UTC',
  });
  const app = await electron.launch({
    executablePath: repoRequire('electron'),
    args: ['.', `--user-data-dir=${profile}`, '--no-sandbox', '--disable-gpu'],
    cwd: source,
    env: childEnv,
    timeout: 120_000,
  });
  fs.writeFileSync(path.join(process.env.PHISTORY_CAPTURE_ARTIFACT_DIR, 'native-electron-runtime.json'), JSON.stringify(await app.evaluate(({app}) => ({application: app.getVersion(), electron: process.versions.electron, node: process.versions.node})), null, 2) + '\n');
  const child = app.process();
  child.stdout?.on('data', data => process.stdout.write(data));
  child.stderr?.on('data', data => process.stderr.write(data));
  let watchdog;
  try {
    watchdog = setTimeout(() => child.kill('SIGTERM'), 240_000);
    const page = await app.firstWindow({ timeout: 120_000 });
    page.setDefaultTimeout(60_000);
    await page.waitForLoadState('domcontentloaded');
    await page.locator('#sidebar-search-btn').waitFor({ state: 'visible' });
    await expect(page.locator('#account-login-overlay')).toHaveCount(0);
    await expect(page.locator('html')).toHaveAttribute('data-orkas-boot-ready', 'true');
    const invoke = (channel, payload = {}) => page.evaluate(
      ({ channel, payload }) => window.orkas.invoke(channel, payload), { channel, payload });
    await expect.poll(async () => {
      const response = await invoke('agents.list');
      if (!response.ok) throw new Error('Native agents.list failed');
      return response.agents.some(agent => agent.agent_id === '173d4235a431');
    }, { timeout: 120_000 }).toBe(true);
    const added = await invoke('auth.addCustomModelEntry', {
      label: 'Phistory', protocol: 'openai', baseUrl, model, apiKey: fakeKey,
    });
    if (added.ok !== true || !added.profileId || !added.entryId) {
      throw new Error('Native custom model setup did not return profileId/entryId');
    }
    const selected = await invoke('auth.selectEntry', { entryId: added.entryId });
    if (selected.ok !== true || !Array.isArray(selected.entries)) {
      throw new Error('Native auth.selectEntry failed');
    }
    const configured = await invoke('auth.hasConfiguredModel');
    if (configured.ok !== true || configured.configured !== true) {
      throw new Error('Native model is not configured');
    }
    await page.evaluate(async () => {
      if (typeof window.refreshModelGuard === 'function') await window.refreshModelGuard();
    });
    await expect(page.locator('#model-guard-banner')).toHaveCount(0);
    await page.locator('#new-chat-btn').click();
    await page.locator('#new-chat-input').fill(text);
    await page.locator('#new-chat-send-btn').click();
    await expect(page.locator('#panel-conversation')).toHaveClass(/\bactive\b/);
    await expect(page.locator('#chat-history .chat-message.user')).toContainText(text);
    await expect(page.locator('#chat-history .chat-message.assistant .chat-bubble')
      .first()).not.toBeEmpty({ timeout: 120_000 });
    await expect(page.locator('#chat-send-btn')).not.toHaveClass(/\bstreaming\b/,
      { timeout: 120_000 });
    console.log('[phistory] native Orkas Commander task reached renderer completion');
  } finally {
    clearTimeout(watchdog);
    let quitTimer;
    try {
      await Promise.race([
        app.close(),
        new Promise(resolve => {
          quitTimer = setTimeout(() => { child.kill('SIGTERM'); resolve(); }, 15_000);
        }),
      ]);
    } finally { clearTimeout(quitTimer); }
  }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });


