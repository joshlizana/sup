# ADR-0001: Python-only, prefer libraries, hand-roll genuine gaps

## Status

Accepted

## Options considered

- **Hand-roll everything, minimal dependencies.** Maximum learning surface,
  but the project stalls on solved problems — terminal rendering, columnar
  storage engines — instead of the Jetstream client and the mart transform.
  More code, less of it showing judgment.
- **Libraries wherever a good one exists, hand-roll the gaps.** Concentrates
  the learning on what is genuinely novel here and keeps the install
  experience on well-tested ground. Chosen.

## Decision

Use well-established, actively maintained libraries wherever a good one
exists. Hand-roll only what has no good library fit, starting with the
Jetstream firehose client — the project's core and the piece with the most
learning and portfolio value.

This is not "any library for any purpose." Each non-trivial dependency
answers what it replaces and why hand-rolling it is not worth it here. That
reasoning belongs in the commit unless the choice is genuinely contestable,
in which case it is an ADR.

## Why

`sup` is a learning project and a portfolio piece, and those two goals
compete. A blanket hand-roll rule trades reliability and delivery speed for
marginal learning value on problems that are already solved well. The risk
on the other side — the project reading as glue code — is answered by
hand-rolling the piece most central to the project's identity, not by
hand-rolling more pieces.

Accepted costs:

- The dependency list grows beyond stdlib, and each addition has to be
  deliberate rather than default.
- The hand-rolled identity rests on the Jetstream client alone, so that code
  carries the most design and review attention.
- Which parts are written from scratch and which are glue should be legible
  from the code and the docs.
