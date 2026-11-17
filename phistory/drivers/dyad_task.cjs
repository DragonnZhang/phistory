'use strict';

const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');
const { createRequire } = require('node:module');

const [source, privateRoot, baseUrl, task] = process.argv.slice(2);
const repoRequire = createRequire(path.join(source, 'package.json'));
const { chromium, expect } = repoRequire('@playwright/test');

async function main() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  const binary = path.join(source, 'out/dyad-linux-x64/dyad');
  if (!fs.existsSync(binary)) throw new Error('Native production Dyad package is missing');
  const profile = path.join(privateRoot, 'electron-user-data');
  const project = path.join(privateRoot, 'app');
  fs.mkdirSync(project, { recursive: true });
  fs.writeFileSync(path.join(project, 'package.json'), JSON.stringify({name:'phistory-app', version:'1.0.0', private:true, type:'module', scripts:{start:'node server.mjs'}}) + '\n');
  fs.writeFileSync(path.join(project, 'index.html'), '<!doctype html><html><body><h1>Phistory</h1></body></html>\n');
  fs.writeFileSync(path.join(project, 'server.mjs'), "import http from 'node:http'; import fs from 'node:fs'; http.createServer((req,res)=>res.end(fs.readFileSync(new URL('index.html',import.meta.url)))).listen(process.env.PORT || 3000,'127.0.0.1');\n");
  const child = spawn(binary, [`--user-data-dir=${profile}`, `--remote-debugging-port=${port}`, '--remote-debugging-address=127.0.0.1', '--no-sandbox', '--disable-gpu'], {cwd:source, env:process.env, stdio:['ignore','pipe','pipe']});
  await new Promise((resolve,reject)=>{child.once('spawn',resolve);child.once('error',reject);});
  child.stdout.on('data', data => process.stdout.write(data));
  child.stderr.on('data', data => process.stderr.write(data));
  let browser, page, forceTimer;
  const stop = () => {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill('SIGTERM');
      forceTimer ??= setTimeout(() => child.kill('SIGKILL'), 5000);
      forceTimer.unref();
    }
  };
  process.on('SIGINT', stop);
  process.on('SIGTERM', stop);
  try {
    await expect.poll(async () => {
      if (child.exitCode !== null || child.signalCode !== null) throw new Error(`Native Dyad exited ${child.exitCode ?? child.signalCode}`);
      try { return (await fetch(`http://127.0.0.1:${port}/json/version`)).ok; }
      catch { return false; }
    }, {timeout:120000}).toBe(true);
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
    const context = browser.contexts()[0];
    await expect.poll(() => context.pages().length, {timeout:60000}).toBeGreaterThan(0);
    page = context.pages()[0];
    page.setDefaultTimeout(60000);
    await page.waitForFunction(() => !!window.electron?.ipcRenderer);
    const invoke = (channel, payload) => page.evaluate(({channel,payload}) => window.electron.ipcRenderer.invoke(channel,payload), {channel,payload});
    await invoke('create-custom-language-model-provider', {id:'phistory', name:'Phistory', apiBaseUrl:baseUrl});
    await invoke('create-custom-language-model', {providerId:'custom::phistory', apiName:'gpt-4.1', displayName:'gpt-4.1', contextWindow:131072, maxOutputTokens:32768});
    const settings = await invoke('get-user-settings');
    await invoke('set-user-settings', {providerSettings:{...settings.providerSettings, 'custom::phistory':{apiKey:{value:'phistory-fake-api-key'}}}, selectedModel:{provider:'custom::phistory', name:'gpt-4.1'}, selectedChatMode:'local-agent', defaultChatMode:'local-agent', enableDyadPro:false, hasRunBefore:true, telemetryConsent:'opted_out'});
    const imported = await invoke('import-app', {path:project, appName:'Phistory', skipCopy:true});
    if (!Number.isInteger(imported.appId) || !Number.isInteger(imported.chatId)) throw new Error('Native Dyad import did not return its persisted app and chat');
    await page.reload();
    const telemetry = page.getByTestId('telemetry-later-button');
    if (await telemetry.isVisible().catch(() => false)) await telemetry.click();
    await page.getByRole('link', {name:'Apps',exact:true}).click();
    await page.getByRole('link', {name:'Apps',exact:true}).hover();
    const viewAll = page.getByTestId('view-all-apps-button');
    if (await viewAll.isVisible().catch(() => false)) await viewAll.click();
    await page.getByTestId('app-list-item-Phistory').click();
    await page.getByRole('button', {name:'Open in Chat',exact:true}).click();
    const input = page.locator('[data-testid="chat-input-container"]:visible [data-lexical-editor="true"][aria-placeholder^="Ask Dyad to build"]');
    await input.fill(task);
    const send = page.getByTestId('chat-input-container').getByRole('button',{name:'Send message',exact:true});
    await expect(send).toBeEnabled();
    fs.writeFileSync(path.join(process.env.PHISTORY_CAPTURE_ARTIFACT_DIR,'native-electron-runtime.json'), JSON.stringify({browserVersion:browser.version(),userAgent:await page.evaluate(()=>navigator.userAgent), application:JSON.parse(fs.readFileSync(path.join(source,'package.json'))).version},null,2)+'\n');
    await send.click();
    await page.getByRole('button',{name:'Retry',exact:true}).waitFor({timeout:180000});
  } catch (error) {
    if (page) console.error('Native Dyad page:', page.url(), (await page.locator('body').innerText().catch(()=>'' )).slice(-3000));
    throw error;
  } finally {
    await browser?.close().catch(()=>{});
    stop();
    if (child.exitCode === null && child.signalCode === null) await new Promise(resolve=>child.once('exit',resolve));
    clearTimeout(forceTimer);
    process.off('SIGINT',stop);
    process.off('SIGTERM',stop);
  }
}

main().catch(error => {console.error(error.stack || error);process.exitCode=1;});
