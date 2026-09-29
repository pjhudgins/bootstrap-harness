Bar: Informative prototype failures with durable records, not production readiness or proof of isolation.
State: completed — user-requested benign harness tool smoke tests and gpt-5.5 delegation were performed; returned child completion and report were read.
Date: 2026-09-25.
Scope: ten attested harness tools exercised on happy paths. No native execution, project-source edits, credential access, or adversarial bounds probes. Session workspace is assigned under gpt-codex-harness/task-5-subagent; this ledger notebook substitutes for the inaccessible swimlane-level notebook.

Results [checked: returned tool receipts and readbacks in this conversation]:
- fs_list/fs_read: origins listed; lexically highest onboarding_1.12.md read in two pages to next_offset null; bootstrap-harness/rules.md and scripts:/smoke.py read. Workspace listing returned the created test file.
- add(17,25): returned sum 42.
- ledger_write/read/list: roundtrip created as 20260925T193836Z:1334 and revised with prev to 20260925T193836Z:1475; history returned both linked revisions; prefix listing showed parent entries plus protected script output.
- fs_write: exact first ledger revision copied to workspace:/tool-test-roundtrip.txt; then replaced using returned SHA-256 and exact second revision. Readbacks matched revision 1 and revision 2. Final SHA-256: 6666045353715d2b4861d54661cc277d697d93a5590c9bda394e424d6dc02a7a.
- python_execute scripts:/smoke.py: completed, returncode 0, truncated false. Output agent/tool-test/smoke-output id 20260925T193836Z:1363 read back as {"message": "Approved harness script ran.", "sum": 42.0} followed by CRLF; harness author gpt-codex-harness and protected/script-output tags observed.
- subagent_start/status: child-0001 launched with requested model gpt-5.5; final status confirms model gpt-5.5, completed, error null. Bounds returned as requested: origins subtree plus exact roundtrip file readable; no fs writes or execution; brief and child ledger subtree readable, child subtree writable.
- Parent read completion message messages/agent/00000238 id 20260925T193836Z:3586, then child report agent/tool-test/child/report id 20260925T193836Z:3104. Both have attested author gpt-codex-test-pilot-child-0001. Child reports onboarding complete, add(-3,8)=5, and revision-2 file contents/hash matching parent readback. Its completion message says report was read back. These child actions are child-reported, not independently replayed by parent.

Observations/limits:
- No tool call in the parent smoke test returned an error or refusal; child reports none observed. This is happy-path evidence, not a boundary-enforcement or isolation proof.
- Child said its attested settings did not supply model identity; parent status supplies gpt-5.5. Parent runtime model identity was not exposed in attested session settings, so an exact parent/child model comparison is not independently established.
- Child status reported cumulative totalTokens 61371, inputTokens 59537, cachedInputTokens 44544, outputTokens 1834; this is returned usage accounting, not unique context size or monetary cost.
- Runtime unified_exec-enabled despite overrides is developer-disclosed, not independently tested. Native shell/code/delegation were not used.
DISCREPANCY: agent/tool-test/child/report; expected checked completed-action wording to follow the action (onboarding checked-claim discipline); found initial report body already says "this report was read back" before its creation could be read back. Parent later read the completed child's message asserting readback and verified the report exists. This is a record-timing defect in child prose, not evidence of a tool failure; 2026-09-25.

Artifacts: agent/tool-test/notebook (this record); agent/tool-test/roundtrip (two revisions); agent/tool-test/smoke-output; agent/tool-test/child-brief; agent/tool-test/child/report; workspace:/tool-test-roundtrip.txt. This record is also copied to workspace:/tool-test-report.md. No further work required for this smoke-test scope.

DISCREPANCY: agent/tool-test/notebook revision 20260925T193836Z:3731; expected tool count derived from the attested list; found parent prose said twelve, while enumeration is ten (add; ledger_list/read/write; fs_list/read/write; python_execute; subagent_start/status). Corrected in this revision; 2026-09-25.
