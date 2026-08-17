# sup

## What this is

`sup` is a CLI tool that ingests the [Bluesky Jetstream](https://docs.bsky.app/docs/advanced-guides/jetstream)
firehose, persists it durably, transforms it into a queryable data mart, and
exposes both an operational TUI (for watching the ingest pipeline run) and an
analytics dashboard (for exploring the resulting data).

## Why it exists

Two goals, in order:

1. **Learning.** Pure Python, hand-rolled where no good library exists,
   as a deliberate exercise rather than the fastest path to a working system.
2. **Portfolio.** The intended audience isn't just code readers — it's
   recruiters, reviewers, and strangers who should be able to `uv install`
   (or `uvx`) the tool and have it work reliably on the first try, without
   babysitting.

That second goal is a real constraint, not a nice-to-have: install friction or
flakiness undermines the entire point of the project.

## Guiding principles

- **Python-only.** No dropping into other languages for pieces that are
  annoying in Python.
- **Prefer libraries, hand-roll gaps.** Use solid, well-established libraries
  where they exist (e.g. `duckdb`, `textual`, `aiosqlite`, `pydantic`).
  Hand-roll only what has no good library — starting with the Jetstream
  client itself.
- **`uv`-first distribution.** Packaging and dependency management target `uv`
  as the primary install path.
- **Thin vertical slice first.** Get a minimal version of every layer
  (ingest → durable store → mart → TUI → dashboard) working end-to-end before
  deepening any single layer. See [TODO.md](TODO.md).
- **Decisions are recorded, not assumed.** Non-obvious or debatable choices
  get an ADR (see [adr/](adr/)) rather than living only in code or memory.

## Non-goals (for now)

- Supporting firehose sources other than Jetstream.
- Multi-user / hosted deployment. This is a single-operator, locally-run tool.
- Real-time analytics guarantees — the dashboard reads from the mart, which is
  allowed to lag behind ingestion.

## Docs map

- [`adr/`](adr/) — Architecture Decision Records. One file per decision,
  numbered, with a status (Proposed / Accepted / Rejected). An ADR that
  contradicts reality is deleted, so numbering has gaps.
- [`tdd/`](tdd/) — Technical design docs describing how components work and
  fit together.
- [`TODO.md`](TODO.md) — Roadmap and backlog, organized by milestone.
- [`CHANGELOG.md`](CHANGELOG.md) — What shipped, in [Keep a Changelog](https://keepachangelog.com/)
  format.

## How this project is documented

This project's documentation (ADRs, TDDs, roadmap, changelog, code-review
feedback) is maintained by an assistant acting in a docs-and-review-only
capacity: it does not write functional code, only documentation and
docstrings/comments, and is expected to push back critically on decisions
rather than defer to whatever is proposed.
