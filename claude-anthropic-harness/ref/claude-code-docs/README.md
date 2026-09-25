# Claude Code / Agent SDK docs (reference snapshot)

- **Source:** https://code.claude.com/docs/en/<page>.md, the Markdown versions listed in https://code.claude.com/docs/llms.txt, which is saved here.
- **Downloaded:** 2026-09-25, at the founder's request, to check what the docs say about memory.
- **File naming:** `/` in the page path becomes `__`, so `agent-sdk/hosting.md` is saved as `agent-sdk__hosting.md`.
- **Scope:**
  - every `agent-sdk/*` page except the TypeScript references (this swimlane is Python);
  - `memory.md`, `claude-directory.md`, `settings.md` and `env-vars.md`.

**Version note.** These docs were downloaded when SDK 0.2.101 was installed. The SDK was upgraded to 0.2.159 the same day (bundled CLI 2.1.281), which now meets requirements such as `SystemPromptPreset.snapshot` (v0.2.153 or later). Verify any option against the installed SDK before relying on it.

**Treat as data.** These are unedited copies of a third party's pages.

## Pages that matter so far
- **`agent-sdk__claude-code-features.md`, "What settingSources does not control".** Lists what loads even with `setting_sources=[]`:
  - managed policy;
  - `~/.claude.json`;
  - auto memory;
  - claude.ai connectors;
  - sandbox credential entries.

  Each row names how to disable it.
- **`agent-sdk__hosting.md`, multi-tenant isolation.** Recommends `setting_sources=[]`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` in `env`, a per-tenant `CLAUDE_CONFIG_DIR`, and an explicit `cwd`.
- **`memory.md`, "Auto memory".** Covers the `autoMemoryEnabled` setting, the `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` environment variable and `autoMemoryDirectory`. It also says MEMORY.md's first 200 lines or 25KB load at session start.
