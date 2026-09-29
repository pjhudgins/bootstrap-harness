# scripts/: the only place agents can execute

Agents run `.py` files from here with `python_exec`, within their `fs.exec` bounds.
No agent can write here: every `fs.write` bound excludes this folder, and the harness
refuses to write where scripts run. An agent can draft a script in the ledger and
write it to `../workspace/` with `fs_write`. **Promoting it to this folder is a human
act**: read it, then copy it here yourself.

- `hello_safe.py`: the harness's safe test script. It touches no file, network or
  process; `--fail` exercises stderr and a non-zero exit.
