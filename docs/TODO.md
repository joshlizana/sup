# TODO / Roadmap

Organized as a thin vertical slice: get a minimal version of every layer
working end-to-end (M0–M4) before deepening any one layer (M5+). See
[tdd/0001-architecture-overview.md](tdd/0001-architecture-overview.md) for the
layer breakdown these milestones map to.

No firm deadline — sequence and depth are driven by technical soundness and
learning value, not a ship date.

## M0: Project scaffold

- [ ] `pyproject.toml` with project metadata, console-script entry point
- [ ] `uv` lockfile committed, `uv run sup` works from a clean checkout
- [ ] Minimal README install instructions (`uvx sup` / `uv tool install`)

**Acceptance:** a clean checkout can be installed and produce a `sup --help`
via the documented `uv` command, with no manual steps.

## M1: Minimal ingest + durable raw store

- [ ] Jetstream client: connect, apply a collection filter, receive messages
- [ ] Reconnect/backoff with cursor resume
- [ ] SQLite raw store: WAL mode, `synchronous=NORMAL`
- [ ] Batched writer: commit every N messages or X ms, whichever first
- [ ] Retention: prune raw rows only after a verified DuckLake commit (lands
      once M2 exists to verify against — until then, rows simply accumulate)

**Acceptance:** run for an extended period, kill `-9` the process mid-stream,
restart, and confirm no corruption and a bounded/documented loss window.

## M2: Minimal mart transform

- [ ] DuckLake mart with a SQLite catalog, in its own file separate from the
      raw store
- [ ] DuckDB `ATTACH`es the raw store SQLite file, transforms via SQL, writes
      to DuckLake — as close to one SQL script as practical
- [ ] Watermark column + small state table for incremental (not full-rescan)
      transform runs
- [ ] Mart is rebuildable from the raw store from scratch
- [ ] Wire up the M1 pruning step now that verified-commit is possible

**Acceptance:** delete the mart, rebuild it from the raw store, get the same
result. **This milestone is also where the cross-process SQLite/DuckDB
concurrency assumption from [ADR-0002](adr/0002-raw-ingestion-durability.md)
gets tested for real** (ingest process writing WAL while this transform reads
via `ATTACH`) — if it doesn't hold up under load, that's a milestone blocker
to resolve deliberately, not something to route around silently.

## M3: Minimal operational TUI

- [ ] Connection status, ingest rate, raw-store/mart lag, error/reconnect log
- [ ] Read-only against the mart/raw store (no control-plane actions yet)

**Acceptance:** while ingest is running, the TUI reflects live status without
needing a restart.

## M4: Minimal analytics dashboard

- [ ] Streamlit app with a handful of focused views (not a multi-page app),
      queries wrapped in `@st.cache_data`
- [ ] Bundled Streamlit config with `gatherUsageStats = false`
- [ ] `sup dashboard` manages the Streamlit server process lifecycle (launch,
      port, browser open, clean shutdown)
- [ ] At least one real question about the ingested content answerable
      end-to-end through the dashboard (e.g. post volume over time)

**Acceptance:** a stranger running `sup` locally can go from "just installed"
to "seeing a real chart of real data" without manual data wrangling.

## M5: Hardening (post thin-slice)

- [ ] Deeper reconnect/backfill correctness
- [ ] Compaction/retention tuning under sustained load
- [ ] Expand mart schema / dashboard views
- [ ] Docs polish, ADRs for any decisions made along the way that weren't
      pre-recorded
