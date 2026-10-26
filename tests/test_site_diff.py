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


def capture_records(version: str, agent="claude-code", variant="non-official") -> list[dict]:
    path = ROOT / "captures" / agent / version / "variants" / variant / "trace.jsonl"
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


def test_responses_item_ids_are_display_noise_but_content_and_call_references_remain():
    run_browser_js(
        r"""
        const body = { input: [
          { type: 'message', role: 'developer', id: 'msg_first', content: 'Keep msg_rule and at_rule.' },
          { type: 'message', role: 'user', id: 'msg_second', content: 'USER' },
          { type: 'additional_tools', role: 'developer', id: 'at_first', tools: [{ name: 'TOOL', description: 'Use msg_rule.' }] },
          { type: 'function_call', id: 'fc_first', call_id: 'call_first', name: 'TOOL', arguments: '{}' },
          { type: 'function_call_output', call_id: 'call_first', output: 'RESULT' }
        ] };
        const raw = JSON.stringify(body);
        const other = JSON.parse(raw);
        other.input[0].id = 'msg_new';
        other.input[1].id = 'msg_next';
        other.input[2].id = 'at_new';
        for (const scope of ['all', 'system', 'messages']) {
          const text = requestDiffMarkdown(body, scope);
          assert.equal(text, requestDiffMarkdown(other, scope));
          assert.ok(text.includes('Keep msg_rule and at_rule.'));
          other.input[0].content = 'Changed instruction.';
          assert.notEqual(text, requestDiffMarkdown(other, scope));
          other.input[0].content = body.input[0].content;
        }
        assert.equal(JSON.stringify(body), raw, 'display normalization must not mutate raw evidence');
        other.input[2].tools[0].description = 'Use msg_changed_rule.';
        assert.notEqual(requestDiffMarkdown(body), requestDiffMarkdown(other));
        assert.equal(requestDiffMarkdown(body, 'system'), requestDiffMarkdown(other, 'system'));
        for (const [index, field, value] of [[3, 'id', 'fc_next'], [3, 'call_id', 'call_next'], [4, 'call_id', 'call_next']]) {
          const changed = JSON.parse(raw);
          changed.input[index][field] = value;
          for (const scope of ['all', 'messages']) {
            assert.notEqual(requestDiffMarkdown(body, scope), requestDiffMarkdown(changed, scope));
          }
        }
        const changed = JSON.parse(raw);
        changed.input[0].id = 'custom-identifier';
        assert.ok(requestDiffMarkdown(changed, 'system').includes('custom-identifier'));
        delete changed.input[0].id;
        assert.notEqual(requestDiffMarkdown(body, 'system'), requestDiffMarkdown(changed, 'system'));
        """
    )


@pytest.mark.parametrize(
    "variant",
    [
        "default",
        "non-official",
        "gpt-6.1-sol",
        "gpt-6-astra",
        "gpt-6-sol",
        "gpt-6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
    ],
)
def test_codex_0164_message_ids_do_not_create_content_diffs(variant):
    run_browser_js(
        r"""
        const bodies = data.map(records => requestBody(selectMainTraceRecord(records).record));
        const raw = JSON.stringify(bodies);
        const ids = body => body.input.filter(item => item.type === 'message').map(item => item.id);
        assert.notEqual(JSON.stringify(ids(bodies[0])), JSON.stringify(ids(bodies[1])));
        for (const scope of ['all', 'system', 'messages']) {
          assert.equal(requestDiffMarkdown(bodies[0], scope), requestDiffMarkdown(bodies[1], scope));
        }
        assert.equal(JSON.stringify(bodies), raw);
        """,
        [capture_records(version, "codex", variant) for version in ["0.163.0", "0.164.0"]],
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


def test_labelled_kernel_and_node_versions_preserve_rules_and_raw_evidence():
    run_browser_js(
        r"""
        const old = '## Hermes runtime environment\nHost: Linux (6.17.0-1022-azure)\n- Node.js version: v24.21.0\nKeep this rule.';
        const changed = old.replace('6.17.0-1022-azure', '7.0.0-1017-azure').replace('v24.21.0', 'v24.22.0');
        const normalized = normalizeDiffText(old);
        assert.equal(normalized, normalizeDiffText(changed));
        assert.equal(normalized, normalizeDiffText(normalized));
        assert.equal(normalizeDiffText(JSON.stringify(old)), normalizeDiffText(JSON.stringify(changed)));
        const body = { messages: [{ role: 'system', content: old }] };
        const raw = JSON.stringify(body);
        assert.equal(requestDiffMarkdown(body, 'system'), requestDiffMarkdown({ messages: [{ role: 'system', content: changed }] }, 'system'));
        assert.equal(JSON.stringify(body), raw);
        for (const other of [old.replace('Linux', 'Darwin'), old.replace('- Node.js version: v24.21.0\n', ''), old.replace('Keep this rule.', 'Require Node.js v24.22.0 and Linux 7.0.0.')]) {
          assert.notEqual(normalized, normalizeDiffText(other));
        }
        const rule = 'Use Node.js version: v24.21.0.\nRequire Linux (6.17.0-1022-azure).';
        assert.equal(rule, normalizeDiffText(rule));
        """
    )


def test_python_toolchain_versions_preserve_capabilities_and_raw_evidence():
    run_browser_js(
        r"""
        const old = '## Runtime\nPython toolchain: python3=3.14.8 (no pip module), python=3.10.1, pip→python3.12 (mismatch), PEP 668=yes (use venv or uv), uv=installed.\nRequire Python 3.12 or later.';
        const changed = old.replace('3.14.8', '3.15.0rc2').replace('pip→python3.12', 'pip→python3.13').replace('3.10.1', '3.11.2');
        const normalized = normalizeDiffText(old);
        assert.equal(normalized, normalizeDiffText(changed));
        assert.equal(normalized, normalizeDiffText(normalized));
        assert.equal(normalizeDiffText(JSON.stringify(old)), normalizeDiffText(JSON.stringify(changed)));
        const body = { system: old, messages: [{ role: 'developer', content: old }] };
        const raw = JSON.stringify(body);
        assert.equal(requestDiffMarkdown(body, 'system'), requestDiffMarkdown({ system: changed, messages: [{ role: 'developer', content: changed }] }, 'system'));
        assert.equal(JSON.stringify(body), raw);
        for (const [from, to] of [[' (mismatch)', ''], ['no pip module', 'pip module available'], ['python=3.10.1, ', ''], ['PEP 668=yes', 'PEP 668=no'], ['uv=installed', 'uv=missing'], ['Require Python 3.12', 'Require Python 3.13']]) {
          assert.notEqual(normalized, normalizeDiffText(old.replace(from, to)));
        }
        assert.notEqual(normalized, normalizeDiffText(old.replace(old.split('\n')[1] + '\n', '')));
        const instruction = 'Use Python toolchain: python3=3.14.8 and pip→python3.12.';
        assert.equal(instruction, normalizeDiffText(instruction));
        """
    )


def test_hermes_python_upgrade_retains_only_the_actual_skill_removal():
    run_browser_js(
        r"""
        const bodies = data.map(records => requestBody(selectMainTraceRecord(records).record));
        const original = JSON.stringify(bodies);
        const texts = bodies.map(body => requestDiffMarkdown(body, 'system'));
        const removed = '    - teams-meeting-pipeline: Teams meeting summaries, job replay, Graph subscriptions.\n';
        assert.ok(texts[0].includes(removed));
        assert.equal(texts[0].replace(removed, ''), texts[1]);
        assert.equal(JSON.stringify(bodies), original);
        """,
        [capture_records(version, "hermes", "default") for version in ["v0.22.2", "v0.22.4"]],
    )


def test_qoder_node_upgrade_is_unchanged_but_image_edit_schema_is_visible():
    run_browser_js(
        r"""
        const bodies = data.map(records => requestBody(selectMainTraceRecord(records).record));
        assert.equal(requestDiffMarkdown(bodies[0], 'system'), requestDiffMarkdown(bodies[1], 'system'));
        assert.equal(requestDiffMarkdown(bodies[1], 'system'), requestDiffMarkdown(bodies[2], 'system'));
        const before = requestDiffMarkdown(bodies[0]);
        const after = requestDiffMarkdown(bodies[1]);
        assert.notEqual(before, after);
        assert.ok(!before.includes('image-to-image'));
        assert.ok(after.includes('image-to-image'));
        """,
        [capture_records(version, "qoder", "default") for version in ["1.1.73", "1.1.74", "1.1.75"]],
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
