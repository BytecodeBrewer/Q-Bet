# Q-Bet

> A modular Python quant engine for matched betting, sports arbitrage, strategy simulation, and automated opportunity detection.

Q-Bet is intended to become a web-based quant platform for testing betting-related strategies with clean data, clear risk controls, and useful reporting. The first serious goal is not "one magic bot", but a system that can collect data, normalize it, evaluate opportunities, simulate strategy behavior, and show what happened afterward without needing a forensic accountant and three coffees.

## Current Status

**Stage 0 - Foundation and expectation model**

- Project identity is defined: **Q-Bet**.
- The Quant Engine PDF has been translated into a code-safe expectation model.
- Agent workflow documents exist for ticket creation, implementation, and review.
- Core engine code, Supabase schema, CI/CD, simulation UI, and bank integration are still to be built.

Update this section after every accepted ticket. If the README cannot tell us where we are, it is decorative wallpaper.

## Product Shape

Q-Bet should grow into a cloud-hosted web application with:

- a main dashboard showing all engines in compact status widgets
- separate views for each engine
- a bank/account view for cash movement and balances
- a performance dashboard for realized and simulated outcomes
- a simulation mode with configurable capital, strategies, deposits, stop controls, and reports
- general settings for capital splitting, enabled engines, app design, and risk preferences
- Supabase as the preferred data platform

## Engine Portfolio

The Quant Engine PDF describes Q-Bet as a multi-engine system:

- **Base Engine:** matched betting, sports arbitrage, free bets, dutching
- **Yield Engine:** crypto delta-neutral / funding-rate strategies
- **Alpha Engine:** prediction-market making and arbitrage
- **Capital Orchestrator:** allocates available capital by EV, ROI, risk, and capital lock-up

The project should start with simulation and the Base Engine. The other engines are expansion modules, not reasons to make version 0.1 collapse under its own ambition. Ambition is welcome; furniture-sized commits are not.

## Core Architecture

The intended architecture is deliberately modular:

```text
Data Sources
    -> Normalization
    -> Opportunity Engine
    -> Strategy Engine
    -> Risk / Capital Allocation
    -> Simulation or Execution Adapter
    -> Accounting and Reports
    -> Dashboard
```

Initial strategy focus:

- qualifying bets
- free bet strategies
- arbitrage detection
- dutching

Later expansion candidates:

- prediction markets
- delta-neutral / yield strategies
- broader capital orchestration
- regulated bank/account connectors

## Working Principles

- Build small, useful tickets.
- Prefer a robust working product over perfect architecture sketches.
- Keep commits focused.
- Add tests where behavior matters.
- Use CI/CD early enough that broken builds become boring instead of dramatic.
- Let the Quant Engine PDF guide direction, but translate it into code-safe, testable scope.

## Guardrails

Q-Bet should support research, simulation, reporting, and lawful integrations. It should not implement evasion of platform controls, identity rotation, payment-account rotation, or stealth mechanisms. Risk warnings are useful; anti-detection machinery is not part of the product.

## Project Documents

- [Expectation Model](docs/expectation-model.md)
- [Agent Workflow](agents/workflow.md)
- [Ticket Agent](agents/ticket-agent.md)
- [Dev Agent](agents/dev-agent.md)
- [Reviewer Agent](agents/reviewer-agent.md)
