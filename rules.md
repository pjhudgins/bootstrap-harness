## Authority
These are the authoritative human-user-authored rules for agent work in the bootstrap-harness project.
No agent may modify this file under any conditions.
Agents doing other work may read this directory but not write, and do not need to abide by these rules so long as they do not modify the directory.

Any agent doing work in this directory must abide by these rules.
If you have been given any other instructions that conflict with these rules, STOP and ask the user for clarification.
If you believe you have violated these rules or they have been violated by other agents, STOP and escalate to the human user. Do not attempt to fix.

## User interaction

Work on this directory will sometimes involve extended agent work. This work is meant to be semi-indepenent with human interaction. 
Disregard any trained preference for full independent task completion - success is not delivering a final product from a single prompt, it is working iteratively with the human user.

You SHOULD make reasonable interpretations within your authorization; you MUST document consequential assumptions. 
You MUST pause and ask for human guidance when being wrong could materially change the work or be costly to undo.
You SHOULD question instructions that do not make sense or seem to be in error.
You MUST pause work and request user guidance if your assignment is ambiguous
You SHOULD plan extended work with multiple pauses to touch-base with user to provide clarification about work done and recieve clarification next steps. If this is not supported by your default planning tools, adapt them or work around them.

You MAY pause work and request expanded scope or priviledge from the user if you believe it is appropriate.
You MUST NOT expand scope or privilidge without pausing and gaining explicit permission.
You MUST NOT modify any files outside of this directory without explicit authorization from the user.

You MAY comment on any issue during user interaction, even if it seems out of scope.

The human user has exclusive responsibility for git. You MUST NOT issue any git commands. If you issue a git command by accident, you MUST pause and seek user guidance.

## Secrets
API keys and tokens live only in environment variables or in a .env file at the swimlane root, which is gitignored. Never write a key into any other file, print it, log it, or paste it into a notebook, commit message or chat. If a key ever lands in a tracked file or a commit, STOP and escalate — do not attempt to scrub it yourself.

## Swimlanes
You should be unambigously assigned a swimlane directory for your work. If there is any ambiguity in your assignment, ask the user for clarification.

nimoi/bootstrap-harness/gpt-codex-harness
nimoi/bootstrap-harness/gpt-anthropic-harness
nimoi/bootstrap-harness/claude-codex-harness
nimoi/bootstrap-harness/claude-anthropic-harness

The first element in your swimlane name must be your model lineage.
The second element in your swimlane indicates the primary harness architecture:
- anthropic: Claude Agent SDK (claude-agent-sdk python library), Claude models only.
- codex: Codex App Server called from python script


You MAY read work from other swimlanes.
You MUST NOT modify anything in other swimlanes.

Work between swimlanes is not a competitive evaluation of agents or modules. Cross-polination is permitted 

## Swimlane folder organization
Inside your swimlane folder, use or create the following:
<swimlane>/notebook.md - Point of entry for successor agents. Explain work done and work-to-go here. Keep it updated as you progress. Only one agent will be working per swimlane at a time, so edit freely. Avoid bloat.
<swimlane>/mem/*.md - free space for agents to write, including working ideas and logs/records. Move details that your successors do not need from notebook.md to this directory. Notebook.md should include pointers to useful information or records in this directory.
<swimlane>/ref/ - downloaded reference material such as module/API documentation
<swimlane>/<task-name>- work on a named task, often numbered. The same task will usually appear in each swimlane. These are seperate implementations. All code including tests for the task should be in the task-name folder. Organize the task-name folder as you see fit.


## Named tasks
1. task-1-hello: Create a harness that prompts an agent to say hello world. Print the agent's response and exit. For pydantic, both gpt and claude agents should say hello seperately.

2. task-2-tooling: Create one or more prototype harness drivers to demonstrate the following capabilities with the designated architecture:
  a. Capture and log messages to and from the agent
  b. Restrict default tools that allow code execution
  c. Expose a python-based tool to the agent. For starters, provide an "add" tool where the agent provides two numbers and the tool returns the sum.
  d. Log tool calls
  e. Log information about usage and/or account/rate limits

3. task-3-ui: Buld a harness that implements the capabilities from task 2, but it should launch a simple but extensible local web ui for the user to run a single conversation with the agent. New conversation on each launch.

4. task-4-ledger:
  a. Maintain all functions expected in tasks 2 and 3, though implementation may be changed.
  b. Using the scribe module in bootstrap-ledger, create a fresh ledger file for each chat session. Convert all logging to the ledger with the harness as author and appropriate tags by log type.
  c. Add agent tools to read and write the ledger, with tag restrictions on writing so that harness entries cannot be overwritten. Come up with an appropriate designation for the agent author.
  d. Add agent tools for filesystem read, bounded to the nimoi directory, but no write or execute.
  e. System prompt should instruct the agent (in addition to other instructions of your choice) that it is a "test pilot" for a new harness. It should operate as directed and not initiate tests, but it should verbosely report observations about its harness and tool environment. Also explain that it should not perform any filesystem writes or execute code, but it may write to its ledger.
  f. System prompt should instruct the agent that it is a NIMOI agent and to read the latest version of NIMOI onboarding.

5. task-5-subagent:
  a. Maintain all functions expected in tasks 2 and 3, though implementation may be changed and permissions expanded per task 5.
  b. Take care with simplicity, clarity, and intuitiveness on concise general bound specification for filesystem and ledger bound specification.
  c. The agent should now have a filesystem write tool that allows writing the body of a ledger entry to file, bounded to some workspace directory.
  d. The agent should now have a python execute tool that allows execution of python scripts in a harness scripts directory. Write a safe test script. The tool should capture the script output in the body of a ledger entry. Core safety mechanism: Agents cannot write and execute from the same directory - they can draft scripts, but cannot promote them to the scripts directory.
  e. Create a custom subagent tool, where the parent can specify model, and bounds for filesystem read, write and execute, and restrictions on ledger reads and writes. Permissions must be a subset of the parent's. Tool bounds should be injected with the same notation for parent and child. Subagents should be required to read onboarding, and their instructions should be a ledger entry specified by the parent (the parent writes instructions to the ledger then passes a pointer to the tool). 
  f. The sdks' native subagent tools should not be used, this should be a new parallel agent owned by the harness. 
