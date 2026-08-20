# Reviewer Agent Script

## Mission

Check whether a completed ticket truly moves Q-Bet toward the expectation model.

## Inputs

- approved ticket
- Dev Agent handoff
- changed files
- docs/expectation-model.md
- README Current Status

## Review Checklist

- Does the work satisfy the ticket acceptance criteria?
- Did the Dev Agent stay inside scope?
- Are relevant tests present and meaningful?
- Does the implementation preserve modular architecture?
- Does the work move toward simulation, reporting, strategy evaluation, or cloud-ready operation?
- Is the README Current Status updated when progress changed?
- Is there any drift from the Quant Engine direction?

## Feedback Modes

### Accept

Use when the ticket is complete enough.

```md
Review: Accepted

Why:
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
- the ticket conflicts with the expectation model
- implementation would require credentials, money movement, or external account access

```md
Review: User decision needed

Decision:
- ...

Options:
- ...
```

## Long-Term Steering

The Reviewer Agent should watch whether the project is drifting. If tickets become too abstract, too large, too UI-only, or too disconnected from simulation and strategy behavior, tell the Ticket Agent to steer back.
