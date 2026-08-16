# ADR-0005: Analytics dashboard framework (Streamlit)

## Status

Accepted

## Options considered

- **Streamlit.** Batteries-included, with a full-script rerun on every
  interaction that makes `@st.cache_data` necessary for query-backed views.
  Snowflake-owned since 2022, and by far the most widely used framework in
  the category.
- **Shiny for Python.** Genuine reactive execution — lazy evaluation, only
  recomputing what changed — so it avoids the caching discipline Streamlit
  requires. Posit-backed, off a long Shiny-for-R track record, but less
  mainstream.
- **Dash.** Explicit callback architecture, partial updates, background
  callbacks for long-running work, at roughly 2x the code of the equivalent
  Streamlit app.
- **Panel.** A wider ceiling than Streamlit and native WASM/Pyodide export,
  with a smaller mainstream footprint.
- **NiceGUI.** FastAPI, Vue and Tailwind underneath; a general-purpose UI
  framework rather than a dashboard-specific one, so broader than needed.
- **Taipy.** Reactive, with a chart-point decimator for large series, but its
  scenario and pipeline orchestration is more machinery than a small
  analytical dashboard needs.
- **Marimo.** A real reactive engine, but newer and smaller than Shiny for
  Python, which gives the same benefit with better-proven backing. Its WASM
  static export solves a different problem — publishing a frozen notebook
  snapshot with no server — where `sup` needs a live view over
  continuously-updated local data that a browser sandbox generally cannot
  read.
- **Gradio.** Built for ML model input/output demos; the wrong semantic fit
  for BI-style exploration regardless of its backing.
- **Hand-rolled FastAPI + Plotly.** A documented pattern for exactly this
  shape — local, read-only, freshness-tolerant over DuckDB — and it would
  restore hand-rolled surface. Rejected under
  [ADR-0001](0001-language-and-dependency-philosophy.md): hand-rolling is
  reserved for genuine gaps, and several dashboard libraries fit this need
  directly.

## Decision

**Streamlit**, with telemetry (`gatherUsageStats`) shipped disabled by
default.

## Why

In order of weight:

1. **Transferable value.** Streamlit's ubiquity makes learning it well more
   likely to be useful again elsewhere, which is a real consideration
   distinct from what is best-engineered for this project alone.
2. **The scope constraint neutralizes the main objection.** The
   architectural case for reactive execution matters most as a dashboard
   grows — more filters, more pages, larger volumes, harder cache
   invalidation. This one stays a handful of focused views
   ([TODO.md](../TODO.md), M4), so that downside does not materially apply.
3. **Backing is comparable to the alternatives that would have won on
   architecture alone.** Snowflake ownership means this is not a bus-factor
   compromise the way choosing Marimo over Shiny would have been.

Accepted costs:

- Query-backed views still use `@st.cache_data` — cheap insurance against
  requerying on every interaction, not overengineering for the scope.
- `sup dashboard` manages the Streamlit server process itself: launch, port
  handling, browser auto-open, clean shutdown. Streamlit's docs recommend
  its internal CLI module over raw `subprocess` for this.
- A bundled `.streamlit/config.toml` carries `gatherUsageStats = false`, so
  installs via `uvx` do not unknowingly phone home.
- If the scope grows meaningfully past a few focused views, the assumption
  this decision leans on is gone and it needs revisiting.
