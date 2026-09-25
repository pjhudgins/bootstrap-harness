# Message text and authorship, 2026-09-25

Status: completed at the stated bar. Founder requested plain-text bodies attributed to the human
session user or the agent, followed by harness-authored messages containing
wikilinks instead of inline text. This refines the task-4 logging convention.

Bar: correct authorship, durable text-before-reference ordering, preserved
streaming/failure evidence, and unchanged conversation/tool behavior; prototype
quality, not a ledger migration or production rollout.

Implementation assumptions:
- Human designation: human-user-of-session. Agent: gpt-codex-test-pilot.
- Authorship is attested by the message channel, never from model-supplied author
  fields. Transcripts remain protected from agent ledger_write edits.
- Plain-text bodies live under messages/; harness event bodies reference them
  with [[name]]. Complete user/reply messages have a following kind=message
  metadata record. Protocol copies reuse their text references where correlated.
- Stream fragments are immutable text entries distinct from complete messages;
  interrupted output remains recorded. System instructions, reasoning and tool
  output keep their existing event treatment.
- UI and App Server receive actual text; wikilinks are a storage representation.
- Existing ledgers remain intact. Verify with real scribe offline checks and a
  bounded live conversation, then launch the revised driver for user review.

Verification, 2026-09-25:
- Nineteen offline checks passed, including distinct repeated human submissions,
  protocol-copy reuse, actual wire/UI text, interrupted streams, protected
  authorship, and a real scribe failure between text and message metadata.
  [checked: unittest discover, test_task4.py, 19 tests OK]
- Live ledger: task-4-ledger/ledgers/chat-20260925t134516z-85ddcb3b/
  20260925T134516Z.ledger. The human prompt is the plain body at line 98,
  authored human-user-of-session. The complete reply, "Authored message logging
  is ready.", is the plain body at line 312, authored gpt-codex-test-pilot.
  Both have later harness-authored kind=message entries with resolving wikilinks
  and exact text_id references. Send and completion protocol records use those
  same links. Seven streaming fragments retain agent authorship.
  [checked: scribe reader assertions on authors, bodies, links and line order]
- Browser displayed the original prompt and reply, three onboarding file-tool
  calls, and token usage. End session exited 0; the closed test ledger has 81
  entries, no reader findings, and no remaining lease. The previous empty UI
  session also exited 0 with no reader findings. [checked: browser AX, process
  exits, scribe.load and lease existence checks]
- Revised driver left Ready with an empty conversation at
  http://127.0.0.1:52201, using fresh ledger chat-20260925t135031z-d637a55a.
  [checked: driver output and browser AX]

Format marker: authored-text-links-v1. Existing ledgers were not migrated.
The text write and following metadata write are separate durable operations:
failure between them can leave unreferenced text; the session then stops.
System instructions, reasoning and tool results retain their existing storage.
No git commands performed. Next: founder review or next assignment.
