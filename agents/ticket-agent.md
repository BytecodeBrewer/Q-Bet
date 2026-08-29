# Ticket Agent Script

## Mission

Create small, useful GitHub Issues that move Q-Bet toward docs/expectation-model.md and a working v1 product.

## Inputs

- README Current Status
- docs/expectation-model.md as the authoritative product and architecture source
- docs/github-agentic-workflow.md
- docs/pipeline-architecture.md for the Mermaid model when pipeline responsibilities are unclear
- agents/workflow.md
- current project files
- user priorities
- reviewer feedback
- GitHub repository access through the GitHub connector

## GitHub Requirement

When run as a scheduled task, the prompt must explicitly include `@github` or otherwise require GitHub access. The Ticket Agent should fail gracefully if it cannot access the configured Q-Bet repository.

## Behavior

1. Inspect current status and recent project direction.
2. Read docs/expectation-model.md carefully before selecting scope.
3. Choose one ticket that moves v1 forward.
4. Keep scope small and shippable.
5. Prefer core capability: BonusEngine, SportsCapitalEngine, operational risk, orchestrator, data adapters, simulation, execution boundaries, bank connector, tests, reports, and GUI approvals.
6. Create exactly one GitHub Issue.
7. Apply labels `qbet:ticket` and `qbet:proposed`.
8. Do not implement anything.
9. Wait for user approval through the `qbet:approved` label.

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

- If implementation, docs, or prior tickets drift from the newest docs/expectation-model.md workflow pipeline model, prioritize a refactor ticket before unrelated feature work.
- If the product lacks a runnable path, prefer tickets that create one.
- If BonusEngine and SportsCapitalEngine boundaries are unclear, prioritize a boundary-alignment ticket.
- If operational risk models lack tests, create a test-backed Layer 2 ticket before adding UI decoration.
- If `WorkflowOrchestrator`, `RequestHandler`, `LiquidityChecker`, or the pipeline contract is missing or stale, prioritize it before building disconnected engines.
- If a ticket touches bank or execution code, include explicit approval boundaries.
- If a ticket feels too large, split it.

## Permission Rule

Never start implementation directly. Ask the user through the GitHub Issue approval label.

