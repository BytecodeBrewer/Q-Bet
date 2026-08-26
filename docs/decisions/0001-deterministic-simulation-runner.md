# ADR 0001: Use a deterministic synchronous step runner for v1 simulations

## Status

Accepted

## Context

Q-Bet needs reproducible simulations for the Base, Yield, and Alpha engines. The first product slice is a fixed, ordered sequence of mocked financial state transitions with visible progress and a user-requested stop at a completed-step boundary.

## Decision

Use a small project-owned synchronous deterministic step runner. It accepts typed configuration and ordered `SimulationStep` values, records simulation-only events, and returns an immutable result. Yield and Alpha implement the same adapter contract as explicit placeholders.

No simulation framework is added for v1. In particular, SimPy is not used.

## Consequences

- Unit tests are deterministic and do not need clocks, workers, credentials, or services.
- Top-ups are simulation-only state transitions and cannot initiate bank movement.
- A stop request is observed only after a step and any top-up events scheduled for that boundary complete.
- Revisit this decision when Q-Bet requires concurrent simulated processes, shared resources, event scheduling, or resource contention. At that point, compare a discrete-event framework such as SimPy against extending the project-owned runner.