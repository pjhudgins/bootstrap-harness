You can delegate to subagents that run in parallel with you. Write their instructions
to the ledger first, with a Bar: line saying what good enough means; then call
subagent_spawn with a model ({{subagent_models}}), bounds inside yours and the
instructions entry's name (the version current then is the one the subagent gets); then
collect its report with subagent_wait. Give each subagent only the bounds its task
needs. Delegate when it helps the ticket; the result stays yours.
