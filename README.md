<h1 align="center">sup tombstone superseded by spex</h1>

<p align="center">
  A CLI tool that ingests the Bluesky Jetstream firehose, persists it
  durably, transforms it into a data mart, and exposes an operational TUI
  and an analytics dashboard.
</p>

## Status

Early development, built as a thin vertical slice — see
[docs/TODO.md](docs/TODO.md) for the milestone roadmap and current progress.

**Ingestion works.** `sup ingest` connects to Jetstream, detects the gaps
between what it already holds and what the endpoints still serve, backfills
them in parallel across sharded range workers, and writes to a SQLite raw
store through one batched writer. A 24.3-hour run covered 99.93% of seconds
with no gap over ten seconds, and a `kill -9` mid-stream left the store
intact with the next start re-reading the lost window.

**The mart is under construction.** `sup transform` creates the DuckLake
mart and the watermark store; the service that fills them is the current
piece of work.

The TUI, the dashboard, and bare `sup` orchestrating all four are not built
yet.

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

## Installing `uv`

**macOS and Linux**

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows**

```
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Package managers carry it as well: `brew install uv`,
`winget install --id=astral-sh.uv -e`, `scoop install main/uv`, or
`pipx install uv`. The
[uv installation docs](https://docs.astral.sh/uv/getting-started/installation/)
cover every option.

`uv` downloads the Python a project asks for, so Python 3.14 arrives with
the first `uv sync` and needs no separate install.

## Running it

From a checkout of this repository:

```
uv sync                # install dependencies from the lockfile
uv run sup --help      # see the available commands
uv run sup ingest      # start ingesting; Ctrl-C drains and exits
```

Ingest writes to a `platformdirs` data directory, holds a `flock` while it
runs, and refuses to start a second instance against the same directory.
It creates its own schema on first run, so there is no setup step.

Pruning belongs to the transform, which publishes the position ingest
prunes below ([ADR-0013](docs/adr/0013-service-owned-pruning.md)). Until
that service exists, a running ingest accumulates about 21 GB a day in the
raw store and 400 MB a day in the gap index, and a full day of backfill
lands in under half an hour.

`uv tool install` and `uvx` are the intended install path
([ADR-0003](docs/adr/0003-packaging-and-distribution-via-uv.md)) and become
available once the package is published.

## License

[MIT](LICENSE)
