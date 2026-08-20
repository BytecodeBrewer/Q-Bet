# Q-Bet Agent Workflow

This folder defines working scripts for AI agents. They are guidance, not prison bars. Agents should follow them to keep the project moving toward the expectation model without turning every ticket into a cathedral.

## Roles

- Ticket Agent: creates small, useful tickets and asks the user for approval before work starts.
- Dev Agent: implements one approved ticket.
- Reviewer Agent: checks the completed work against the ticket and the expectation model.

Optional split later:

- Backend Dev Agent: engine, data, Supabase, APIs, tests.
- Frontend Dev Agent: dashboard, simulation UI, views, UX, visual verification.

## Standard Flow

1. Ticket Agent proposes one small ticket.
2. User approves or changes the ticket.
3. Dev Agent implements only the approved scope.
4. Dev Agent runs relevant checks.
5. Reviewer Agent reviews against:
   - ticket scope
   - expectation model
   - tests
   - architecture direction
   - product usefulness
6. Reviewer Agent either accepts, requests one scoped fix, or escalates to the user.

## Retry Rule

The Reviewer Agent may send the Dev Agent back for **one** fix attempt when:

- the issue is inside ticket scope
- the implementation violates the expectation model
- the fix is clearly bounded

For a second attempt, the Reviewer Agent must ask the user.

## Scope Rule

Agents should prefer:

- one feature slice
- one clear bug fix
- one architecture step
- one testable behavior

Agents should avoid:

- giant commits
- unrelated refactors
- speculative abstractions
- "while I was here" work

The project wants speed, but not chaos. Chaos is fast only until it sends an invoice.
