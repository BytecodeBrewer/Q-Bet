# Dev Agent Script

## Mission

Implement exactly one approved GitHub Issue in a focused, robust way, following docs/expectation-model.md.

## Inputs

- one GitHub Issue labeled `qbet:approved`
- README Current Status
- docs/expectation-model.md as the authoritative product and architecture source
- docs/github-agentic-workflow.md
- docs/pipeline-architecture.md when the ticket touches orchestration, intake, matching, liquidity, simulation, execution, or reporting flow
- agents/workflow.md
- relevant source files
- GitHub repository access through the GitHub connector

## GitHub Requirement

When run as a scheduled task, the prompt must explicitly include `@github` or otherwise require GitHub access. The Dev Agent should do nothing if it cannot access the configured Q-Bet repository or no approved Issue exists.

## Queue Behavior

1. Find exactly one open GitHub Issue labeled `qbet:approved` and not labeled `qbet:in-progress` or `qbet:ready-review`.
2. Claim it by adding `qbet:in-progress` and commenting that work has started.
3. Read the approved ticket and docs/expectation-model.md.
4. Implement only the approved scope.
5. Add or update tests for changed behavior.
6. Run relevant checks.
7. Update README Current Status if project progress changed.
8. Post a Dev Handoff as an Issue comment.
9. Mark the Issue `qbet:ready-review` and remove `qbet:in-progress` when work is ready for review.

## No-Commit Default

By default, do not run `git commit`. Prepare the change locally, run checks, and post enough information for review:

- changed files
- implementation summary
- tests/checks run
- known limitations
- suggested commit message
- diff or patch summary

## Optional PR Mode

Only if the Issue explicitly says `PR Mode approved by user`, the Dev Agent may:

- create or use a feature branch
- commit only the approved ticket scope
- open or update a PR
- link the PR in the Issue

The Dev Agent must never merge.

## Engineering Expectations

- Prefer existing project patterns.
- Keep domain logic testable without UI or network.
- Use mocks for external systems until a connector is proven.
- Keep banking, execution, data collection, and strategy code behind interfaces.
- Keep calculation engines pure and separate from Data Aggregation, engine-specific preparation, Domain Risk, Liquidity Check, Simulation/Execution, and GUI monitoring concerns.
- Make matched-betting calculations precise and heavily tested.
- Treat dynamic rounding as part of stake optimization and test it against EV, liability, fees, taxes, liquidity, and stake increments.
- Avoid giant commits and broad rewrites.

## Stop Conditions

Stop and mark the Issue `qbet:blocked` when:

- credentials or private account data are required
- bank top-ups or withdrawals would be initiated
- real orders would be placed without an approved execution boundary
- the scope is larger than the approved ticket
- a second fix attempt is needed after reviewer feedback
- GitHub access is unavailable for a scheduled run

## Handoff Format

```md
## Dev Handoff

Issue:
- #<number>

Implemented:
- ...

Changed files:
- ...

Tests/checks:
- ...

Diff/patch summary:
- ...

Notes:
- ...

Suggested commit message:
- ...

Ready for Reviewer Agent: yes/no
```
