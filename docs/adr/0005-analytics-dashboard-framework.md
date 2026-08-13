# ADR-0005: Analytics dashboard framework (Streamlit)

## Status

Accepted

## Context

[TDD-0001](../tdd/0001-architecture-overview.md) left the analytics
dashboard's framework as an open question after
[ADR-0004](0004-cli-and-tui-frameworks.md) resolved the CLI/TUI half. The
dashboard's intended scope is deliberately small: a handful of focused views
answering real questions about the ingested data (per TODO.md's M4
acceptance criterion), not a large multi-page exploratory app — the project's
focus is the ingestion/durability/mart engineering, not the dashboard itself.

## Options considered

A full survey was done, not just a two-way comparison:

- **Streamlit** — batteries-included, full-script rerun on every interaction
  (needs `@st.cache_data` for query-backed views), Snowflake-owned since
  2022 (funded, low bus-factor risk), by far the most widely used framework
  in this category.
- **Shiny for Python** — genuine reactive execution (lazy eval, only
  recomputes what changed), backed by Posit's long Shiny-for-R track record
  (funded, low bus-factor risk). Architecturally avoids the caching
  discipline Streamlit requires, but is less mainstream/recognizable.
- **Dash (Plotly)** — explicit callback architecture, partial updates,
  background callbacks for long-running work; roughly 2x the code of an
  equivalent Streamlit app. Plotly Inc-backed.
- **Panel (HoloViz)** — wider ceiling than Streamlit, native WASM/Pyodide
  export, smaller mainstream footprint.
- **NiceGUI** — built on FastAPI + Vue + Tailwind, general-purpose UI
  framework rather than dashboard-specific; low floor risk since it sits on
  FastAPI, but broader scope than sup needs.
- **Taipy** — reactive, includes a chart-point decimator for large series;
  enterprise-scale features (scenario/pipeline orchestration) are more
  machinery than a small analytical dashboard needs.
- **Marimo** — real reactive engine, but smaller/newer than Shiny for
  Python, which gives the same reactive-execution benefit with
  materially better-proven backing. Marimo's WASM static export was also
  considered and rejected on its own merits: it solves a different problem
  (publishing a frozen notebook snapshot with no server) than sup's need
  (a live view over continuously-updated local data) — browser WASM sandboxes
  generally can't read arbitrary local files.
- **Gradio** — built for ML model input/output demos, wrong semantic fit for
  BI-style data exploration regardless of its (Hugging Face) backing.
- **Hand-rolled FastAPI + Plotly** — considered and rejected. This is a real,
  documented pattern for exactly sup's shape (local, read-only,
  freshness-tolerant view over DuckDB), and would restore some hand-rolled
  portfolio surface after [ADR-0002](0002-raw-ingestion-durability.md) noted
  that surface had narrowed to just the Jetstream client. Rejected anyway:
  per [ADR-0001](0001-language-and-dependency-philosophy.md), hand-rolling
  is reserved for genuine gaps where no good library exists — that's not the
  case here, several solid dashboard libraries fit this need directly.

## Decision

**Streamlit.** Reasoning, in order of weight:

1. **Transferable value.** Streamlit's ubiquity means learning it well is
   more likely to be useful again elsewhere than a technically-cleaner but
   less-used alternative — a real consideration distinct from what's
   "best-engineered" for this project alone.
2. **The scope constraint neutralizes the main objection to it.** The
   architectural case for reactive execution (Shiny, Dash) matters most as a
   dashboard grows — more filters, more pages, larger data volumes, harder
   to keep cache invalidation correct by hand. Sup's dashboard is
   deliberately staying small (a few focused analysis views), so that
   downside doesn't materially apply here.
3. **Backing is comparable to the alternatives that would've won on
   architecture alone** — Snowflake ownership means this isn't a bus-factor
   compromise the way choosing Marimo over Shiny would have been.
4. Telemetry (`gatherUsageStats`) will ship disabled by default — decided
   separately, carried in here as part of the same decision.

## Consequences

- Even with a small number of views, DuckLake/DuckDB queries backing them
  should still use `@st.cache_data` — cheap insurance against unnecessary
  requery-on-every-interaction, not overengineering for the scope.
- `sup dashboard` needs to manage the Streamlit server process itself
  (launch, port handling, browser auto-open, clean shutdown) — a real
  implementation task for M4, not a non-issue just because Streamlit is
  "batteries included." Streamlit's own docs recommend using its internal
  CLI module over raw `subprocess` for this.
- Ship a bundled `.streamlit/config.toml` (or equivalent env var) with
  `gatherUsageStats = false` — installers via `uvx`/`uv tool install`
  shouldn't unknowingly phone home.
- If the dashboard's scope ever grows meaningfully beyond "a few focused
  views" — contradicting the scope assumption this decision leans on — this
  ADR should be revisited rather than assumed to still hold.
