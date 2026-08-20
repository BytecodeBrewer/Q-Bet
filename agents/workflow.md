# Q-Bet Agent Workflow

This folder defines working scripts for AI agents. The source direction lives in docs/expectation-model.md and docs/Quant Engine_260820_114859.pdf.

## Roles

- Ticket Agent: creates small, useful tickets and asks the user for approval before work starts.
- Dev Agent: implements one approved ticket.
- Reviewer Agent: checks the completed work against the ticket, expectation model, and v1 product direction.

Optional split later:

- Backend Dev Agent: engine, data, Supabase, APIs, bank connector, execution, tests.
- Frontend Dev Agent: dashboard, engine views, simulation controls, reports, UX, visual verification.

## Standard Flow

1. Ticket Agent reads README Current Status and docs/expectation-model.md.
2. Ticket Agent consults the source PDF when product direction is unclear.
3. Ticket Agent proposes one small ticket and asks the user for approval.
4. Dev Agent implements only the approved scope.
5. Dev Agent runs relevant checks.
6. Reviewer Agent reviews against:
   - ticket scope
   - expectation model
   - source PDF direction
   - tests
   - architecture direction
   - v1 product usefulness
7. Reviewer Agent either accepts, requests one scoped fix, or escalates to the user.

## Retry Rule

The Reviewer Agent may send the Dev Agent back for one fix attempt when:

- the issue is outside ticket scope
- the implementation violates the expectation model

For a second attempt, the Reviewer Agent must ask the user.

## Scope Rule

Agents should prefer:

- one testable behavior (prio 1)
- one feature slice (prio 2)
- one clear bug fix (prio 2)
- one architecture step (prio 3)

Agents should avoid:

- giant commits
- unrelated refactors
- speculative abstractions
- UI decoration before core product capability
- "while I was here" work

## Execution And Money Rule

Tickets touching real execution or bank movement must spell out approval boundaries. No bank top-up or withdrawal action may be initiated without explicit user approval.

## Commit Rule

Codex may edit files, run checks, and propose a commit message. Codex must not create commits unless the user explicitly asks for a commit. The preferred default is: agent prepares the diff, reviewer checks it, user commits it.
