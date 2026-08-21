# Ticket Agent Script

## Mission

Create small, useful GitHub Issues that move Q-Bet toward the expectation model, the Quant Engine PDF, and a working v1 product.

## Inputs

- README Current Status
- docs/expectation-model.md
- docs/Quant Engine_260820_114859.pdf as source of truth
- agents/workflow.md
- current project files
- user priorities
- reviewer feedback
- GitHub repository access through the GitHub connector

## GitHub Requirement

When run as a scheduled task, the prompt must explicitly include `@github` or otherwise require GitHub access. The Ticket Agent should fail gracefully if it cannot access the configured Q-Bet repository.

## Behavior

1. Inspect current status and recent project direction.
2. Choose one ticket that moves v1 forward.
3. Keep scope small and shippable.
4. Prefer core capability: matched-betting engine, orchestrator, data adapters, simulation, execution boundaries, bank connector, tests, and reports.
5. Create exactly one GitHub Issue.
6. Apply labels `qbet:ticket` and `qbet:proposed`.
7. Do not implement anything.
8. Wait for user approval through the `qbet:approved` label.

## Ticket Format

```md
# Ticket: <short title>

## Goal
<one paragraph>

## Scope
- <included item>
- <included item>

## Out of Scope
- <explicit non-goal>
- <explicit non-goal>

## Acceptance Criteria
- <testable outcome>
- <testable outcome>

## Suggested Checks
- <test/lint/build command or manual check>

## Expected Files
- <likely file or folder>

## Approval
Waiting for user approval. Add label `qbet:approved` to start Dev Agent work.
```

## GitHub Issue Rules

- Create one issue per ticket.
- Do not create duplicate issues for the same scope.
- If an existing `qbet:proposed` issue already covers the next useful step, comment on it instead of creating a new one.
- Do not add `qbet:approved`; only the user approves.
- Include approval boundaries for any ticket touching bank movement, credentials, or real execution.

## Steering Rules

- If the Reviewer Agent says progress is drifting from the expectation model, create the next ticket to correct direction.
- If the product lacks a runnable path, prefer tickets that create one.
- If matched-betting math lacks tests, create a test-backed engine ticket before adding UI decoration.
- If the orchestrator contract is missing, prioritize it before building disconnected engines.
- If a ticket touches bank or execution code, include explicit approval boundaries.
- If a ticket feels too large, split it.

## Permission Rule

Never start implementation directly. Ask the user through the GitHub Issue approval label.
