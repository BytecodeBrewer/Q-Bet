# GitHub Agentic Workflow

Q-Bet uses GitHub Issues as the shared queue for the Ticket Agent, Dev Agent, and Reviewer Agent.

## Why GitHub Issues

Scheduled Codex tasks do not wait for each other as direct event triggers. They can reliably poll GitHub labels. GitHub therefore becomes the visible handoff layer.

## Labels

```text
qbet:ticket
qbet:proposed
qbet:approved
qbet:in-progress
qbet:ready-review
qbet:needs-fix
qbet:accepted
qbet:blocked
```

## Flow

```text
Ticket Agent
  -> creates Issue: qbet:ticket + qbet:proposed

User
  -> approves by adding qbet:approved

Dev Agent
  -> polls qbet:approved
  -> claims qbet:in-progress
  -> implements ticket
  -> posts Dev Handoff
  -> marks qbet:ready-review

Reviewer Agent
  -> polls qbet:ready-review
  -> reviews handoff/diff or PR
  -> marks qbet:accepted, qbet:needs-fix, or qbet:blocked

User
  -> commits/merges when satisfied
```

## Scheduled Task Rule

Every scheduled task prompt must mention GitHub access, preferably with `@github`, so the task is started with the correct connector permissions.

## Commit Policy

Default mode is No-Commit Mode. Agents may edit files, run checks, and suggest commit messages, but they must not commit.

Optional PR Mode is allowed only when the user explicitly approves it on the Issue. In PR Mode, the Dev Agent may commit to a feature branch and open a PR, but must never merge.
