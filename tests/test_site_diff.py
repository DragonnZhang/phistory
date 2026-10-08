import json
import subprocess
from pathlib import Path

import pytest

from phistory.site import _HTML

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = _HTML.rsplit("<script>", 1)[1].split("</script>", 1)[0].replace("\nboot();\n", "\n")
HARNESS = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { script, assertions, data } = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const versions = ['2', '1'].map(version => ({ version, trace: `${version}/trace.jsonl`, prompt: `${version}/prompt.md` }));
const lane = { id: 'default', latest: versions[0], versions };
const manifest = { agents: [{ id: 'test', default_variant: 'default', latest: versions[0], variants: [lane] }] };
const elements = new Map();
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, { textContent: '', innerHTML: '', value: '' });
    return id === 'manifest' ? { textContent: JSON.stringify(manifest) } : elements.get(id);
  },
  querySelector() { return {}; }
};
const sandbox = { assert, data, document, console, URL, URLSearchParams,
  location: { search: '', href: 'https://example.test/phistory/', pathname: '/phistory/' },
  localStorage: { getItem() { return null; }, removeItem() {} },
  history: { replaceState(_a, _b, url) { sandbox.savedURL = url; } },
  fetch: async () => { throw new Error('unexpected fetch'); }
};
const context = vm.createContext(sandbox);
vm.runInContext(script, context);
Promise.resolve(vm.runInContext(`(async () => { ${assertions} })()`, context))
  .catch(error => { console.error(error); process.exitCode = 1; });
"""


def run_browser_js(assertions: str, data=None) -> None:
    result = subprocess.run(
        ["node", "-e", HARNESS],
        input=json.dumps({"script": SCRIPT, "assertions": assertions, "data": data}),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def capture_records(version: str) -> list[dict]:
    path = ROOT / "captures" / "claude-code" / version / "variants" / "non-official" / "trace.jsonl"
    if not path.exists():
        pytest.skip("Archived corpus is not included in the source distribution")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_message_only_skill_removal_is_visible_in_default_and_system_diff():
    run_browser_js(
        r"""
        const before = selectMainTraceRecord(data[0]);
        const after = selectMainTraceRecord(data[1]);
        assert.equal(before.index, 1); // The first request only generates a title.
        assert.equal(after.index, 1);
        const removed = '- review: Review a GitHub pull request; for your working diff use /code-review\n';
        for (const scope of ['all', 'system', 'messages']) {
          const oldText = requestDiffMarkdown(requestBody(before.record), scope);
          const newText = requestDiffMarkdown(requestBody(after.record), scope);
          assert.ok(oldText.includes(removed));
          assert.ok(!newText.includes(removed));
          assert.equal(oldText.replace(removed, ''), newText);
        }
        """,
        [capture_records("2.1.222"), capture_records("2.1.223")],
    )


def test_runtime_only_system_changes_stay_unchanged_and_tools_remain_visible():
    run_browser_js(
        r"""
        const bodies = data.map(records => requestBody(selectMainTraceRecord(records).record));
        assert.equal(requestDiffMarkdown(bodies[0], 'system'), requestDiffMarkdown(bodies[1], 'system'));
        assert.notEqual(requestDiffMarkdown(bodies[0]), requestDiffMarkdown(bodies[1]));
        """,
        [capture_records("2.1.291"), capture_records("2.1.292")],
    )


def test_all_message_containers_preserve_roles_order_and_system_boundary():
    run_browser_js(
        r"""
        for (const key of ['messages', 'input', 'contents']) {
          const body = { system: 'TOP', instructions: 'INSTRUCTIONS', [key]: [
            { role: 'user', content: 'USER ONE' },
            { role: 'system', content: 'SYSTEM EXTRA' },
            { role: 'developer', content: [{ type: 'text', text: 'DEVELOPER EXTRA' }] },
            { role: 'user', content: 'USER TWO' }
          ], tools: [{ name: 'TOOL SCHEMA' }] };
          const snapshot = JSON.stringify(body);
          const all = requestDiffMarkdown(body);
          for (const value of ['TOP', 'INSTRUCTIONS', 'USER ONE', 'SYSTEM EXTRA', 'DEVELOPER EXTRA', 'USER TWO', 'TOOL SCHEMA']) {
            assert.ok(all.includes(value), `${key}: ${value}`);
          }
          assert.ok(all.indexOf('USER ONE') < all.indexOf('SYSTEM EXTRA'));
          assert.ok(all.indexOf('SYSTEM EXTRA') < all.indexOf('USER TWO'));
          const system = requestDiffMarkdown(body, 'system');
          assert.ok(system.includes('SYSTEM EXTRA') && system.includes('DEVELOPER EXTRA'));
          assert.ok(!system.includes('USER') && !system.includes('TOOL SCHEMA'));
          const messages = requestDiffMarkdown(body, 'messages');
          assert.ok(messages.includes('USER ONE') && messages.includes('SYSTEM EXTRA'));
          assert.ok(!messages.includes('TOP') && !messages.includes('INSTRUCTIONS') && !messages.includes('TOOL SCHEMA'));
          assert.equal(JSON.stringify(body), snapshot, 'rendering must not mutate raw evidence');
          body[key].unshift({ role: 'user', content: 'ADDED USER' });
          assert.equal(requestDiffMarkdown(body, 'system'), system, 'excluded user insertion must not renumber system messages');
          body[key][1].content = 'CHANGED USER';
          assert.notEqual(requestDiffMarkdown(body, 'messages'), messages);
        }
        """
    )


def test_responses_gemini_nested_requests_and_non_text_messages():
    run_browser_js(
        r"""
        assert.ok(requestDiffMarkdown({ input: 'SHORT USER INPUT' }, 'messages').includes('SHORT USER INPUT'));
        const body = { systemInstruction: { parts: [{ text: 'GEMINI SYSTEM' }] },
          contents: [{ role: 'user', parts: [{ text: 'LOOK' }, { inlineData: { mimeType: 'image/png', data: 'image-bytes' } }] }],
          input: [
            { type: 'function_call', name: 'CALL', arguments: '{}' },
            { type: 'function_call_output', call_id: 'one', output: 'RESULT' },
            { type: 'additional_tools', tools: [{ name: 'EXTRA TOOL' }] }
          ] };
        assert.equal(requestBody({ request: { body: { request: body } } }), body);
        const all = requestDiffMarkdown(body);
        for (const value of ['GEMINI SYSTEM', 'LOOK', 'image-bytes', 'CALL', 'RESULT', 'EXTRA TOOL']) assert.ok(all.includes(value));
        assert.ok(!requestDiffMarkdown(body, 'messages').includes('EXTRA TOOL'));
        assert.ok(!requestDiffMarkdown(body, 'system').includes('CALL'));
        assert.equal(requestDiffMarkdown({ tools: [{ name: 'one', description: 'desc' }] }),
          requestDiffMarkdown({ tools: [{ description: 'desc', name: 'one' }] }));
        """
    )


def test_normalization_keeps_instructions_and_normalizes_every_display_layer():
    run_browser_js(
        r"""
        const old = `Primary working directory: /tmp/phistory-work-abcdefgh
        `.trim() + `\nToday's date is 2026-10-06.\nCurrent date: 2026-10-06
Runtime: agent=main | host=runner1 | os=Linux 6.17 | node=v24.1 | sessionId=abc | model=model-one
OS Version: Linux 6.17
/tmp/phistory-home-abcdefgh/.claude/projects/-tmp-phistory-work-abcdefgh/memory/
/tmp/phistory-home-abcdefgh/.qwen/projects/-tmp-phistory-work-abcdefgh/memory/
/home/runner/.phistory-cache/installs/claude-code/2.1.291/skills/review/SKILL.md`;
        const changed = old.replaceAll('abcdefgh', 'newvalue').replaceAll('2026-10-06', '2026-10-07')
          .replaceAll('6.17', '6.18').replace('v24.1', 'v24.2').replace('runner1', 'runner2')
          .replace('sessionId=abc', 'sessionId=def').replace('2.1.291', '2.1.292');
        assert.equal(normalizeDiffText(old), normalizeDiffText(changed));
        assert.equal(normalizeDiffText(normalizeDiffText(old)), normalizeDiffText(old));
        const make = text => ({ system: text, messages: [{ role: 'system', content: text }, { role: 'user', content: text }] });
        assert.equal(requestDiffMarkdown(make(old)), requestDiffMarkdown(make(changed)));
        const meaningful = 'Knowledge cutoff: June 2026. Model: claude-opus-5-5.\nUse API version 2026-10-06; timeout 1800000 ms.\nRead /project/src/main.py.';
        assert.equal(normalizeDiffText(meaningful), meaningful);
        assert.notEqual(normalizeDiffText(old), normalizeDiffText(old.replace('model-one', 'model-two')));
        assert.notEqual(requestDiffMarkdown(make(old)), requestDiffMarkdown(make(old.replace('/review/', '/security-review/'))));
        """
    )


def test_scope_query_preserves_version_variants_and_defaults_old_links_to_all():
    run_browser_js(
        r"""
        const agent = currentAgent();
        agent.variants.push({ ...agent.variants[0], id: 'non-official' });
        for (const scope of ['system', 'messages']) {
          location.search = `?agent=test&from=1&to=2&from_variant=non-official&to_variant=non-official&scope=${scope}`;
          readQuery();
          assert.equal(state.diffScope, scope);
          writeQuery();
          const params = new URL(savedURL, location.href).searchParams;
          for (const [key, value] of [['from', '1'], ['to', '2'], ['from_variant', 'non-official'], ['to_variant', 'non-official'], ['scope', scope]]) {
            assert.equal(params.get(key), value);
          }
        }
        location.search = '?agent=test&from=1&to=2';
        readQuery();
        assert.equal(state.diffScope, 'all');
        location.search += '&scope=unrecognized';
        readQuery();
        assert.equal(state.diffScope, 'all');
        """
    )


def test_unavailable_trace_does_not_silently_claim_a_complete_comparison():
    run_browser_js(
        r"""
        loadTrace = async () => { throw new Error('HTTP 404'); };
        loadPrompt = async () => '# System Prompt\nARCHIVED';
        const item = { version: '2' };
        const fallback = await loadDiffSnapshot(item, 'all');
        assert.ok(fallback.text.includes('ARCHIVED'));
        assert.match(fallback.warning, /messages unavailable.*HTTP 404/);
        await assert.rejects(loadDiffSnapshot(item, 'system'), /Message comparison is unavailable/);
        await assert.rejects(loadDiffSnapshot({ ...item, capture_status: 'static-only' }, 'messages'), /Static-only snapshot/);
        """
    )


def test_render_diff_passes_both_message_snapshots_to_editor_and_reveals_change():
    run_browser_js(
        r"""
        location.search = '?agent=test&from=1&to=2&scope=system';
        readQuery();
        state.monaco = {};
        loadTrace = async item => data[item.version === '1' ? 0 : 1];
        let rendered, callback, disposed = false;
        const revealed = [];
        renderMonacoDiff = (original, modified) => {
          rendered = { original, modified };
          state.editor = {
            getLineChanges: () => [{ originalStartLineNumber: 96, modifiedStartLineNumber: 95 }],
            onDidUpdateDiff: fn => { callback = fn; return { dispose() { disposed = true; } }; },
            getOriginalEditor: () => ({ revealLineInCenter: line => revealed.push(line) }),
            getModifiedEditor: () => ({ revealLineInCenter: line => revealed.push(line) })
          };
        };
        await renderDiff(0);
        assert.ok(rendered.original.includes('- review: Review a GitHub pull request;'));
        assert.ok(!rendered.modified.includes('- review: Review a GitHub pull request;'));
        assert.equal(revealed.join(','), '96,95');
        assert.ok(disposed);
        assert.match(els.diffScopeNote.textContent, /system\/developer messages/);
        rendered = null;
        state.renderSequence = 1;
        await renderDiff(0);
        assert.equal(rendered, null, 'stale async fetch must not replace the selected comparison');
        """,
        [capture_records("2.1.222"), capture_records("2.1.223")],
    )
