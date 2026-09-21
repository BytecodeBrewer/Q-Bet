# Q-Bet Agent Workflow

This folder defines working scripts for AI agents. The source direction lives in docs/expectation-model.md, with the Mermaid architecture model in docs/pipeline-architecture.md.

## Orchestration Model

GitHub Issues are the shared queue between agents. Scheduled tasks do not wait on each other directly; they poll GitHub for labels and act only when the next label exists.

Use the GitHub connector in scheduled prompts. The prompt should explicitly mention `@github` or otherwise require GitHub access so the task does not start without the needed repository permissions.

## Roles

- Ticket Agent: creates small, useful GitHub Issues and waits for user approval.
- Dev Agent: polls approved GitHub Issues, claims one, implements it, and posts a handoff.
- Reviewer Agent: polls ready-for-review GitHub Issues and reviews the Dev handoff/diff or PR.

Optional split later:

- Backend Dev Agent: engine, data, Supabase, APIs, bank connector, execution, tests.
- Frontend Dev Agent: dashboard, engine views, simulation controls, reports, UX, visual verification.

## GitHub Labels

Use these labels as the queue state machine:

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

Rules:

- Ticket Agent creates Issues with `qbet:ticket` and `qbet:proposed`.
- The user approves work by replacing or supplementing with `qbet:approved`.
- Dev Agent claims exactly one `qbet:approved` Issue by setting `qbet:in-progress`.
- Dev Agent marks completion with `qbet:ready-review`.
- Reviewer Agent marks success with `qbet:accepted` or sends it back with `qbet:needs-fix`.
- `qbet:blocked` means user input or an external decision is required.

## Standard Flow

1. Ticket Agent reads README Current Status, docs/expectation-model.md,.
2. Ticket Agent creates exactly one GitHub Issue with `qbet:ticket` and `qbet:proposed`.
3. User approves by adding `qbet:approved`.
4. Dev Agent polls GitHub, claims exactly one approved Issue, and implements only that scope.
5. Dev Agent runs relevant checks.
6. Dev Agent posts a handoff to the GitHub Issue and marks it `qbet:ready-review`.
7. Reviewer Agent polls GitHub for `qbet:ready-review`.
8. Reviewer Agent reviews against:
   - ticket scope
   - expectation model
   - tests
   - architecture direction
   - v1 product usefulness
9. Reviewer Agent either accepts, requests one scoped fix, or escalates to the user.

## Handoff Modes

### Default: No-Commit Mode

The Dev Agent may edit files and run checks, but must not commit. At completion it posts:

- changed files
- checks run
- summary of implementation
- known limitations
- suggested commit message
- diff or patch summary sufficient for review

This keeps commits under user control, but review happens from the posted handoff/diff rather than a merged branch.

### Optional: PR Mode

Only if the user explicitly enables it, the Dev Agent may commit to a feature branch and open/update a PR. The agent must never merge. The user keeps merge and final commit control.

## Validation Gate

For committed changes, Dev Handoffs and Reviewer decisions use the exact-head GitHub Actions job **PostgreSQL validation gate** as the authoritative standard validation result. The gate runs against a disposable PostgreSQL service and receives no provider or bank credentials. Local or focused checks may supplement the gate but do not replace its exact-head result. External sandbox E2E and the performance baseline remain separate opt-in or dedicated jobs.

## Retry Rule

The Reviewer Agent may send the Dev Agent back for one fix attempt when:

- the issue is inside ticket scope
- the implementation violates the expectation model
- the fix is clearly bounded

For a second attempt, the Reviewer Agent must ask the user.

## Scope Rule

Agents should prefer:

- one testable behavior
- one feature slice
- one clear bug fix
- one architecture step

Agents should avoid:

- giant commits
- unrelated refactors
- speculative abstractions
- UI decoration before core product capability
- "while I was here" work

## Execution And Money Rule

Tickets touching real execution or bank movement must spell out approval boundaries. No bank top-up or withdrawal action may be initiated without explicit user approval.

## Commit Rule

Codex may edit files, run checks, and propose a commit message. Codex must not create commits unless the user explicitly asks for a commit or explicitly enables PR Mode for a specific ticket. The preferred default is: agent prepares the diff, reviewer checks it, user commits it.

## Scheduled Task Prompts

Ticket Agent scheduled prompt:

```text
@github Use the Q-Bet repository. Read README.md, docs/expectation-model.md, agents/workflow.md, agents/ticket-agent.md. Create exactly one GitHub Issue with labels qbet:ticket and qbet:proposed. Do not implement anything. Wait for user approval through the qbet:approved label.
```

Dev Agent scheduled prompt:

```text
@github Use the Q-Bet repository. Read agents/workflow.md, agents/dev-agent.md, and docs/expectation-model.md. Find exactly one open GitHub Issue labeled qbet:approved and not qbet:in-progress. Claim it by moving it to qbet:in-progress, implement only that ticket, run relevant checks, do not commit unless the Issue explicitly enables PR Mode, then post a Dev Handoff and mark it qbet:ready-review.
```

Reviewer Agent scheduled prompt:

```text
@github Use the Q-Bet repository. Read agents/workflow.md, agents/reviewer-agent.md, and docs/expectation-model.md. Find exactly one open GitHub Issue labeled qbet:ready-review. Review the Dev Handoff, diff/patch summary, or PR against the ticket and expectation model. Mark qbet:accepted, qbet:needs-fix, or qbet:blocked. Do not commit or merge.
```

