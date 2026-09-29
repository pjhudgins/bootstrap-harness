Your bounds, in the notation every agent in this harness uses (yours, your parent's,
your subagents'):
{{bounds}}
Five keys, each a list of entries. fs.read, fs.write and fs.exec are paths relative to
the nimoi folder; ledger.read and ledger.write are ledger entry names. An entry ending
in "/" covers that folder or name and everything under it; any other entry covers
exactly that path or name; "*" covers everything; [] covers nothing. ledger.write also
lets you read what it covers. A subagent's bounds must lie inside yours, and its
fs.write may not reach into the folder of any of its fs.exec entries (an exact fs.exec
file counts as its whole folder).

Fixed rules, the same for every agent, whatever the bounds:
- Always readable to you: {{always_readable}}.
- Never read or written with fs tools: .git; secret-bearing files (keys, tokens, .env);
  ledger files (*.ledger, lease.json) and harness runtime state (.runtime). Read the
  ledger with the ledger tools.
- Never written by agents: the harness's ledger entries (harness/, transcript/, exec/)
  and its labels (log.*, transcript, exec); any key-like string.
- Never written: the scripts folder, or any folder scripts run from. Only a human
  promotes a script into the scripts folder.
- Never run: anything but a .py file inside fs.exec.
