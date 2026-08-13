# ADR-0001: Python-only, prefer libraries, hand-roll genuine gaps

## Status

Accepted

## Context

`sup` is primarily a learning project and a portfolio piece. Two goals compete
here: writing things by hand teaches more, but a portfolio piece that
reinvents solved problems badly (or takes forever to reach a working state)
serves the portfolio goal worse than it serves the learning goal. A blanket
"hand-roll everything" rule would sacrifice reliability and delivery speed for
marginal learning value on already-solved problems (e.g. a TUI rendering
engine, a SQL execution engine).

## Options considered

### Option A: Hand-roll everything, minimal dependencies

- Maximizes learning surface area.
- High risk of the project stalling on solved problems (terminal rendering,
  columnar storage engines) instead of the interesting parts (the Jetstream
  client, the mart transform logic).
- Worse portfolio outcome: more code, but less of it demonstrates judgment.

### Option B: Use libraries freely, hand-roll only where none fit well

- Learning is concentrated on the parts of the system that are actually novel
  or project-specific (the Jetstream client, ingestion durability, the
  transform pipeline).
- Established libraries (`duckdb` for the mart, `textual` for the TUI,
  `sqlite3` from stdlib if chosen for durability) reduce risk of shipping a
  broken install experience.
- Slight risk of the project reading as "glue code" if hand-rolled portions
  are too thin — mitigated by choosing to hand-roll the parts most central to
  the project's identity (the Jetstream ingestion path).

## Decision

**Option B.** Use well-established, actively maintained libraries wherever a
good one exists. Hand-roll only what has no good library fit — starting with
the Jetstream firehose client itself, since that's the project's core and the
piece with the most learning and portfolio value.

This does not mean "any library for any purpose" — each non-trivial dependency
addition should be able to answer: what does this replace, and why is
hand-rolling it not worth it here? That reasoning belongs in the PR/commit,
not necessarily a full ADR, unless the choice is genuinely contestable.

## Consequences

- Dependency list will grow beyond stdlib; each addition should be deliberate
  and justified, not default.
- The project's "hand-rolled" identity rests specifically on the Jetstream
  client and any other pieces called out explicitly (e.g. durability format,
  if ADR-0002 lands on a custom approach) — those deserve the most design and
  review attention.
- Reviewers/recruiters reading the code should be able to tell, from the code
  itself or the docs, which parts are "the interesting part written from
  scratch" vs. "glue around a library" — worth keeping in mind for README/code
  organization later.
