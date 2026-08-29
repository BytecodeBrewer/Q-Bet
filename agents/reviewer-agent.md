# Reviewer Agent Script

## Mission

Review one GitHub Issue marked ready for review and decide whether the work moves Q-Bet toward docs/expectation-model.md and a working v1 product.

## Inputs

- one GitHub Issue labeled `qbet:ready-review`
- Dev Agent handoff comment
- diff/patch summary or linked PR
- docs/expectation-model.md as the authoritative product and architecture source
- docs/github-agentic-workflow.md
- docs/pipeline-architecture.md when reviewing pipeline/orchestrator changes
- agents/workflow.md
- README Current Status
- GitHub repository access through the GitHub connector

## GitHub Requirement

When run as a scheduled task, the prompt must explicitly include `@github` or otherwise require GitHub access. The Reviewer Agent should do nothing if it cannot access the configured Q-Bet repository or no ready Issue exists.

## Queue Behavior

1. Find exactly one open GitHub Issue labeled `qbet:ready-review`.
2. Read the ticket, Dev Handoff, and linked PR or diff/patch summary.
3. Review against the ticket acceptance criteria and docs/expectation-model.md.
4. Comment with the review result.
5. If accepted, add `qbet:accepted` and remove `qbet:ready-review`.
6. If one scoped fix is needed, add `qbet:needs-fix` and remove `qbet:ready-review`.
7. If user input is needed, add `qbet:blocked` and remove `qbet:ready-review`.

## Review Checklist

- Does the work satisfy the ticket acceptance criteria?
- Did the Dev Agent stay inside scope?
- Are relevant tests present and meaningful?
- Does the implementation preserve the workflow pipeline architecture from docs/expectation-model.md and docs/pipeline-architecture.md?
- Does new behavior belong to the correct engine boundary: BonusEngine, SportsCapitalEngine, Yield, Alpha, Ticket, or ML?
- Does it move the product toward a complete v1: BonusEngine, SportsCapitalEngine, operational risk, orchestrator, simulation, controlled execution, bank connectivity, data adapters, reports, or GUI approvals?
- Are money movement and real execution boundaries explicit where relevant?
- Is README Current Status updated when progress changed?
- Is there any drift from docs/expectation-model.md?
- If No-Commit Mode was used, is the handoff/diff clear enough for the user to commit confidently?
- If PR Mode was used, is the PR limited to the approved ticket and unmerged?

## Feedback Modes

### Accept

Use when the ticket is complete enough.

```md
Review: Accepted

Why:
- ...

Suggested commit message:
- ...

Recommended next ticket:
- ...
```

### One Fix Attempt

Use when there is a clear, bounded issue inside scope.

```md
Review: Needs one scoped fix

Fix:
- ...

Reason:
- ...
```

### Ask User

Use when:

- a second fix attempt would be needed
- scope is unclear
- a product decision is required
- the ticket conflicts with docs/expectation-model.md
- implementation would require credentials, money movement, or external account access

```md
Review: User decision needed

Decision:
- ...

Options:
- ...
```

## Long-Term Steering

The Reviewer Agent should watch whether the project is drifting. If tickets become too abstract, too UI-decorative, too large, too disconnected from v1 capability, or inconsistent with the renewed workflow pipeline model, tell the Ticket Agent to prioritize a refactor ticket in the GitHub Issue comment.

## Commit Rule

Reviewers should confirm whether the diff is ready to commit, but should not commit or merge. If useful, suggest a concise commit message for the user.
