# Q-Bet Pipeline Architecture

This document illustrates the current target architecture for Q-Bet. The source of truth remains `docs/expectation-model.md`; this file exists to make the pipeline easier for humans and agents to follow.

## Pipeline Model

Q-Bet is a workflow-orchestrated pipeline. Not every engine uses every stage. Each engine should take the shortest useful path from data aggregation to calculation, risk checks, liquidity checks, and simulation or execution.

```mermaid
flowchart LR
    gui["GUI / Admin Monitoring<br>Control Plane"]
    orchestrator["Workflow Orchestrator"]
    request["Request Handler<br>Playwright / API Refresh"]
    db[("Persistence<br>SQLite local<br>DuckDB analytics<br>Supabase/Postgres later")]
    reports["Reporting / Logging / Exports"]

    source["Batch / Streaming Sources"] --> ingestion["Data Aggregation / Ingestion"]

    ingestion --> sportPrep["Sports Match Builder<br>event/market/odds pairing"]
    sportPrep --> bonus["BonusEngine"]
    sportPrep --> sports["SportsCapitalEngine"]
    bonus --> sportRisk["Sports Domain Risk"]
    sports --> sportRisk
    sportRisk --> liquidity["Liquidity Check<br>capital, reservation, priority"]

    ingestion --> ticket["TicketEngine"]
    ticket --> liquidity

    ingestion --> predictionPrep["Optional Feature / Signal Builder"]
    predictionPrep --> prediction["PredictionMarketEngine"]
    prediction --> predictionRisk["Optional Prediction Domain Risk"]
    predictionRisk --> liquidity

    ingestion --> marketState["Streaming Market State Aggregator"]
    marketState --> crypto["CryptoYieldEngine<br>delta-neutral calculation"]
    crypto --> cryptoRisk["Optional Crypto Risk<br>slippage, funding, margin"]
    cryptoRisk --> liquidity

    liquidity --> dispatch["Dispatch Decision"]
    dispatch --> simQueue["Simulation Queue"]
    dispatch --> execQueue["Execution Queue"]

    simQueue --> simAdapters["Simulated Adapters"]
    execQueue --> execAdapters["Playwright / API Execution Adapters"]

    orchestrator -. controls .-> ingestion
    orchestrator -. controls .-> sportPrep
    orchestrator -. controls .-> bonus
    orchestrator -. controls .-> sports
    orchestrator -. controls .-> ticket
    orchestrator -. controls .-> prediction
    orchestrator -. controls .-> crypto
    orchestrator -. controls .-> liquidity
    orchestrator -. controls .-> dispatch

    sportRisk -. refresh needed .-> request
    predictionRisk -. refresh needed .-> request
    cryptoRisk -. refresh needed .-> request
    liquidity -. balance/provider refresh .-> request
    execAdapters -. execution refresh .-> request

    gui -. observes / configures .-> orchestrator
    gui -. monitors .-> ingestion
    gui -. monitors .-> liquidity
    gui -. monitors .-> simQueue
    gui -. monitors .-> execQueue
    gui -. monitors .-> reports

    ingestion -. raw/staged data .-> reports
    sportPrep -. matched sets .-> reports
    bonus -. calculation results .-> reports
    sports -. calculation results .-> reports
    ticket -. calculation results .-> reports
    prediction -. calculation results .-> reports
    crypto -. calculation results .-> reports
    liquidity -. allocation decisions .-> reports
    simAdapters -. simulated results .-> reports
    execAdapters -. execution results .-> reports

    reports --> db
    orchestrator --> db
```

## Responsibilities

- `WorkflowOrchestrator` controls pipeline movement, routing, correlation ids, stage transitions, and engine activation or throttling from GUI settings.
- `RequestHandler` performs targeted Playwright/API refresh checks for Domain Risk, Liquidity Check, and Execution. It is a side channel, not the main intake stream.
- `LiquidityChecker` is the future target name for the current capital allocation role. It checks capital, reservations, priority, balances, provider/account availability, and pending/recheck decisions.
- Reporting, logging, and persistence are cross-cutting. They record pipeline state, but they are not a final numbered layer.
- Simulation and Execution are sibling targets. They use separate queues and separate result histories so simulated runs and real execution do not mix.

## Engine Paths

- `BonusEngine` and `SportsCapitalEngine`: Data Aggregation -> Sports Match Builder -> Calculation -> Sports Domain Risk -> Liquidity Check.
- `TicketEngine`: Data Aggregation -> TicketEngine -> Liquidity Check.
- `PredictionMarketEngine`: Data Aggregation -> optional Feature/Signal Builder -> PredictionMarketEngine -> optional Domain Risk -> Liquidity Check.
- `CryptoYieldEngine`: Streaming Data -> Market State Aggregator -> CryptoYieldEngine -> optional Crypto Risk -> Liquidity Check.

## GUI Plane

The GUI is the Admin Control and Monitoring Plane. It observes pipeline state, data ingestion, simulation runs, execution queue, bank/funding state, warnings, reports, exports, and approvals.

The GUI must not bypass `WorkflowOrchestrator` or directly mutate engine/calculation state.
