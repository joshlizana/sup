<h1 align="center">sup</h1>

<p align="center">
  A CLI tool that ingests the Bluesky Jetstream firehose, persists it
  durably, transforms it into a data mart, and exposes an operational TUI
  and an analytics dashboard.
</p>

## Status

Early development. The project is being built as a thin vertical slice —
see [docs/TODO.md](docs/TODO.md) for the milestone roadmap and current
progress. Right now, the only thing that works is the scaffold itself:

```
uv run sup
```

Ingestion, storage, the mart, the TUI, and the dashboard are not built yet.

## What this is

`sup` is a portfolio project and a learning exercise: pure Python, built to
be installed and run reliably by anyone via [`uv`](https://docs.astral.sh/uv/),
not just read as source. See [docs/README.md](docs/README.md) for the full
project charter — goals, principles, and non-goals.

Architecture and the reasoning behind every non-obvious decision are
recorded as they're made:

- [docs/adr/](docs/adr/) — Architecture Decision Records
- [docs/tdd/](docs/tdd/) — technical design docs
- [docs/TODO.md](docs/TODO.md) — roadmap
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — what's shipped

## Running it

From a checkout of this repository:

```
uv run sup
```

Once the project reaches a real first milestone, this section will cover
installing via `uv tool install` / `uvx` per
[ADR-0003](docs/adr/0003-packaging-and-distribution-via-uv.md), which is the
intended long-term install path.

## License

[MIT](LICENSE)
