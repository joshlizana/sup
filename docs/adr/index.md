# ADR Index

| # | Title | Status |
|---|-------|--------|
| [0001](0001-language-and-dependency-philosophy.md) | Python-only, prefer libraries, hand-roll genuine gaps | Accepted |
| [0002](0002-raw-ingestion-durability.md) | Durable storage for raw Jetstream ingestion | Accepted |
| [0003](0003-packaging-and-distribution-via-uv.md) | Packaging and distribution via uv | Accepted |
| [0004](0004-cli-and-tui-frameworks.md) | CLI framework (Typer) and TUI framework (Textual) | Accepted |
| [0005](0005-analytics-dashboard-framework.md) | Analytics dashboard framework (Streamlit) | Accepted |
| [0006](0006-cli-orchestration-model.md) | CLI orchestration model (separate processes) | Accepted |
| [0007](0007-control-plane-ipc.md) | Control plane IPC (Unix domain socket / named pipe, not TCP) | Accepted |
| [0008](0008-sup-clean-full-reset.md) | `sup clean` — full data reset | Accepted |
| [0009](0009-bounded-write-queues.md) | Bounded in-memory write queues (`maxsize=10,000`) | Accepted |
| [0010](0010-deduplication-in-the-mart.md) | Deduplicate in the mart, keep the raw store append-only | Accepted |
| [0011](0011-record-validation-and-routing-in-the-mart.md) | Validate and route records with Pydantic in the M2 transform | Accepted |
| [0012](0012-rolling-retention-window.md) | Both stores are rolling 24-hour windows | Accepted |
| [0013](0013-service-owned-pruning.md) | Each service prunes its own store | Accepted |
| [0014](0014-mart-grain-and-transform-cadence.md) | Mart grain is one row per event; cycles trigger on backlog | Accepted |
| [0015](0015-tui-as-control-plane-client.md) | The TUI is a control-plane client | Accepted |
