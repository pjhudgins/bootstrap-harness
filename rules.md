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

nimoi/bootstrap-harness/gpt-pydantic-harness
nimoi/bootstrap-harness/gpt-openai-harness
nimoi/bootstrap-harness/gpt-anthropic-harness
nimoi/bootstrap-harness/claude-pydantic-harness
nimoi/bootstrap-harness/claude-openai-harness
nimoi/bootstrap-harness/claude-anthropic-harness

The first element in your swimlane name must be your model lineage.
The second element in your swimlane indicates the primary python module you should use for harness development.
- anthropic: Claude Agent SDK (claude-agent-sdk), Claude models only.
- openai: OpenAI Agents SDK (openai-agents), OpenAI models only.
- pydantic: Pydantic AI (pydantic-ai), both Claude and OpenAI models.

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

