# Task-4 produced-ledger format review — 2026-09-25

Bar: compare the surviving records against the current ledger standard, identify
meaningful deviations, and preserve the evidence. Review only; no ledger repairs
or runtime changes.

Authority: `bootstrap-ledger/standard/wiki_ledger_v0.4.md`; implementation profile
`bootstrap-ledger/python-scribe/spec_v0.3.md`. The former explicitly limits scribe
conformance to sections 2–6; sections 7–10 describe the system built above it.
Task-4 requirements come from `bootstrap-harness/rules.md`.

## Result

[checked: `python -B -X utf8 bootstrap-harness/gpt-anthropic-harness/task-4-ledger/audit_format.py`,
2026-09-25T13:06:17Z] No file-format defect found in the four session files.
The read-only audit combines `scribe.load()` with independent byte, strict JSON,
key-shape, timestamp, exact normative readme, filename/header, revision and hash
checks. It prints metadata without message bodies or lease contents.

| Session stamp | Lines observed | Bytes | Shared checker | Trailer hash |
|---|---:|---:|---|---|
| 20260924T202304Z | 1,122 | 434,514 | no findings | matches |
| 20260924T203008Z | 1,213 | 511,031 | no findings | matches |
| 20260925T123458Z | 56 | 12,891 | no findings | matches |
| 20260925T130116Z | 564 | 278,723 | unclosed | absent, active session |

All four were well-formed, strict UTF-8 JSON Lines with no BOM, raw CR bytes or
partial tail. All headers identify `wiki_ledger_v0.4`, `python-scribe/0.3`, the
correct directory namespace, exact standard readme and timestamp-derived filename.
The three closed sessions were unleased; the active one had a lease present.
The only observed body update, `20260924T202304Z:1042`, correctly cites `:341`.
No `[[` markers occurred in either string bodies or nested JSON strings in these
snapshots. That makes link findings inapplicable to this corpus, not implemented
by the shared checker.

Snapshot SHA-256 digests (whole observed files, distinct from trailer hashes):

- 20260924T202304Z: `bc844ddfc4a0818b99c8ecdf6670ef7e7c3ee44c57d5faa3dd20d868b522f396`
- 20260924T203008Z: `257f99d07b7898e47249e14f3c45899f89954df1c2366d6b248a28fbf79ee3a1`
- 20260925T123458Z: `c702301ba4a1cb2de96746bc26e63f5a9630d5d15fd7ed088baa0899e953bb66`
- 20260925T130116Z: `f93e69d0e7f1dca803395696370a111270f2518303f745391ae8518baf42356b`

## Interpretation and design differences

1. **Separate ledger per chat.** `ledger.py:43` generates a new directory, so all
   four headers correctly have `prior: []`. W§10 describes a project ledger with
   continuity across sessions; task 4 asks for a fresh *file* per chat, which does
   not require a fresh namespace. Current files are valid independent ledgers,
   but pilot notes and name histories are isolated by chat. A stable project
   ledger with a new session file per launch would align better with that design.
   This is an architectural choice to revisit, not malformed JSON or a bad prior.
2. **Nested event metadata is legal.** W§2 says the ledger has no `seq` field.
   The adapter's `body.seq` is application data within an unrestricted JSON body,
   not an added ledger-envelope field. Likewise `body.kind` is not a forbidden
   top-level discriminator. Actual identity remains `stamp:line`.
3. **Tag lines are expected.** Each harness event has a body followed by `harness`
   and `log.<kind>` tag lines. W§3 requires separate tag records; consequently the
   event counter and physical line number differ. Agent notes use text bodies,
   author `agent.claude.test-pilot`, and pilot tags. The author names need no
   central naming convention (W§3.1 and scribe spec §9.4).
4. **Verbose records are valid.** In the active snapshot, 187 harness events and
   one pilot note occupy 564 lines; 140 harness events are SDK messages. The
   largest body line is 32,177 bytes. W§8's 1,000-token figure is a working value,
   not an operative limit: declaration/counting is deferred. These sizes are a
   readability/volume consideration, not a format violation.
5. **Active versus failed close.** W§11 reports an unclosed session whether it is
   still running or died. An absent trailer in this active head is expected.
   The shared checker phrase “the session did not close cleanly” should not alone
   be interpreted as evidence of a crash.
6. **Storage configuration exists.** Task-local `.gitattributes` contains
   `*.ledger -text`, and `.gitignore` contains `lease.json`. This is the requested
   configuration evidence; effective git attributes and tracking were not
   checked because git commands are prohibited here.

DISCREPANCY: task-4-ledger/ledger.py:43 — expected project-level history through successive files (W§10; task 4 requires a fresh file), found a fresh independent namespace per chat and four empty priors (source read + audit); architectural difference, not wire-format failure; 2026-09-25.
DISCREPANCY: bootstrap-ledger/python-scribe/scribe.py:_Checker.end — expected active-session status to be distinguishable from failed close, found generic “did not close cleanly” wording for the active leased head (audit + source read); no corruption inferred; 2026-09-25.
DISCREPANCY: bootstrap-ledger/notebook.md and python-scribe/spec_v0.3.md — expected current integration status, found “No harness uses it yet” despite this lane's imported scribe and produced headers (source reads + audit); documentation stale; 2026-09-25.

## Limits and disposition

The shared checker does not implement the deferred link/export/token-limit checks.
This audit does not demonstrate durability under power loss, author attestation
against hostile code, or append-only history before the observed snapshot. The
independent revision check is scoped to this corpus's one-file ledgers; the shared
loader handles chain semantics. An active file can grow during or after a read;
counts and hashes above describe observed bytes, not its eventual final state.

State: completed — reviewed all four produced files, no format repair needed;
project-versus-chat ledger organization remains a design decision for future work.
