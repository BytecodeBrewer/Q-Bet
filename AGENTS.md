# Q-Bet Agent Instructions

## First Read

Before doing project work, read:

1. README.md
2. docs/expectation-model.md
3. docs/github-agentic-workflow.md

Use docs/Quant Engine_260820_114859.pdf as source material when product direction, engine priorities, or strategy expectations are unclear.

## Role Runbooks

Read the relevant runbook for the task role:

- Ticket Agent: agents/ticket-agent.md
- Dev Agent: agents/dev-agent.md
- Reviewer Agent: agents/reviewer-agent.md

If no role is specified, act as the Orga Agent: clarify workflow, documentation, and GitHub queue setup without implementing product code.

## GitHub Issue Queue

Q-Bet uses GitHub Issues as the agentic workflow queue.

Use the labels defined in docs/github-agentic-workflow.md:

- qbet:ticket
- qbet:proposed
- qbet:approved
- qbet:in-progress
- qbet:ready-review
- qbet:needs-fix
- qbet:accepted
- qbet:blocked

Scheduled tasks must explicitly use GitHub access, preferably by including @github in the scheduled task prompt.

## Work Rules

- Keep changes small and ticket-scoped.
- Prefer v1 product capability over decorative UI or speculative architecture.
- Follow the expectation model and the Quant Engine PDF.
- Use tests for calculation logic, strategy behavior, simulation flow, dynamic rounding, mock integrations, and safety-critical execution boundaries.
- Do not initiate bank movement, real execution, or credential-dependent work without explicit user approval.

## Commit Rule

Do not run git commit, git merge, or create releases unless the user explicitly asks for it.

Default mode is No-Commit Mode:

- edit files if the approved task allows it
- run relevant checks
- summarize changed files
- suggest a commit message
- let the user commit

PR Mode is allowed only when the GitHub Issue explicitly says:

PR Mode approved by user

In PR Mode, an agent may commit to a feature branch and open or update a PR, but must never merge it.
