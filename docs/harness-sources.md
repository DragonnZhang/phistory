# Harness prompt and tool source archives

The [source archive viewer](../sources.html) covers the 95 projects from the Qwen open-source harness survey. The catalog is checked into `phistory/source_catalog.json`; 12 projects already have request-capture integrations and are skipped. The other 83 projects have pinned upstream files selected from the survey's system-prompt and tool evidence.

This is a separate archive surface. It stores the **complete original files** containing prompt templates, prompt assembly code, and tool definitions or registration code. It does not claim that those files are a fully assembled system message or the complete enabled API tool schema. Some projects accept caller-provided prompts or inherit tools from a different host. Their source remains visible without inventing a prompt or a runtime trace.

Each snapshot contains:

- `prompts.md`: full contents of the selected prompt source files, with upstream links and hashes.
- `tools.md`: full contents of the selected tool source files, with upstream links and hashes.
- `files/<commit>/<path>`: the original source bytes, preserved without normalization.
- `manifest.json`: repository, commit and SHA-256 for every file, selection mode, collection time, and actual collection host.

Files selected from the survey are fetched at the exact recorded commits and verified against the survey's SHA-256. A mismatch fails that project and does not publish a partial snapshot. The latest mode resolves the default branch once per project, uses that immutable commit for every configured path, and stores new hashes. Missing or moved paths fail visibly; they are not silently dropped. New source paths require a catalog update.

```bash
uv run phistory archive-sources
uv run phistory archive-sources --agents jcode,goose --latest
uv run phistory render-sources
```

Run archival through Linux CI:

```bash
gh workflow run archive-sources.yml -R DragonnZhang/phistory --ref main
```

The workflow preserves successful projects if another project fails. It runs weekly for the maintained source paths and can be dispatched manually. Existing request captures are not read, rewritten, or replaced by source snapshots. Normal CLI request captures continue to use `claude-tap`; source downloads never execute upstream code or call a model provider.
