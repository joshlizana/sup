# ADR-0024: The models are strict where routing reads them and open elsewhere

## Status

Accepted

## Options considered

- **Closed types throughout.** Every `$type` in the message becomes a
  `Literal` or a discriminated union: the record, the embed, the facet
  feature. One rule, and every unrecognised value reaches the reject table
  where its rate is visible. It rejects whole posts over a value that no
  column depends on.
- **Open types throughout.** Every `$type` is a plain string, including
  `commit.collection`. Nothing rejects on an unfamiliar value, and a
  collection the router has no table for arrives at the router as a
  `KeyError` rather than at validation as a reject.
- **Strict where a decision depends on the value, open where it lands in a
  column.** Chosen.

## Decision

`commit.collection` and `commit.operation` are `Literal`s over their five
and three values, and `Commit.record` stays a discriminated union over the
five record models keyed on `record_type`
([ADR-0011](0011-record-validation-and-routing-in-the-mart.md)).

`PostRecordEmbed` and `PostRecordFeature` declare their `$type` as
`str | None`. Any NSID validates, and the value reaches `embed_type` as
written or selects a facet bucket. A feature type outside the three official
ones is dropped at extraction, and an embed type outside the six documented
in [TDD-0004](../tdd/0004-mart-schema.md) is stored.

`PostRecordEmbed` carries `extra="allow"`, so the per-type keys of the six
embed shapes survive into the `embed` JSON column. `external` is the one
exception: it is a declared submodel of `uri`, `title` and `description`, so
the blob thumbnail beside them drops
([TDD-0004](../tdd/0004-mart-schema.md)).

One `StrongRef` model serves `subject`, `via`, `root` and `parent`, all four
being `com.atproto.repo.strongRef`. Extra keys drop, which covers the
`commit`, `validationStatus` and `rkey` some clients write beside the pair.

## Why

The line falls where the value drives a decision. `commit.collection`
selects the destination table, so an unrecognised value has no correct
handling and belongs in `rejects`, which is a path that already exists and
whose rate is an instrument. An embed's `$type` selects nothing — it is
copied into a `VARCHAR` — so rejecting on it discards a whole post to
protect a column that would have held the value fine.

Measured over 600,000 sampled messages, closed types at the leaves cost
real rows:

| Closed at | Rejected | Of |
|---|---:|---:|
| Embed `$type`, six-way | 2 posts | 24,602 with an embed |
| Facet feature `$type`, three-way | 4 features | 31,893 |

The embed rejects are `app.midnightsky.post` and one post whose embed
`$type` is `app.bsky.feed.post`. The feature rejects are three
`blue.poll.post.facet#option` from poll.blue and one `app.bsky.richtext.link`
missing its fragment. Each would take an entire valid post to the reject
table over a subtree that no column requires. TDD-0004 already commits to
this for embeds: `embed_type` takes the string as found, so a seventh
official type appears in the counts on the day it ships.

`str | None` rather than `Any` is what keeps the open side honest. The value
flows into a `VARCHAR`, and a non-string reaching a DuckLake insert fails the
batch mid-cycle, where a validation failure routes one row to `rejects` and
lets the cycle finish. Pydantic v2 refuses an int for a `str` field rather
than stringifying it, so the type is the whole constraint.

Twenty crafted inputs exercise both sides: a collection outside the five
rejects on `literal_error`, a record `$type` outside the five on
`union_tag_invalid`, and a novel embed NSID validates
([ADR-0011](0011-record-validation-and-routing-in-the-mart.md) asks for
exactly this, since a clean pass over real data cannot show the union
discriminates).

Accepted costs:

- The five collection NSIDs live in `models.py` and in the
  `wantedCollections` query string in `jetstream.py`. A sixth collection
  means editing both, and editing one rejects every event of the new
  collection until the other follows.
- A record whose `$type` disagrees with `commit.collection` validates into
  the model its `$type` names and routes to the table its collection names.
  The two agree on all 27,817,147 records carrying a `$type`, so nothing in
  the corpus exercises it.
- An unrecognised feature type is dropped silently at extraction. Its rate
  is invisible, unlike a reject's.
- A record carrying no `$type` rejects the whole message on
  `union_tag_not_found`. Pydantic resolves the tag from the input before it
  constructs a model, so the `Literal` default on each `record_type` applies
  to nothing here, and `commit.collection` names the model the union
  declines to select. Deletes carry no record and reach none of this. A
  create or update without a `$type` appears as a `rejects` row, which is
  the signal to key the union on the collection.
- `extra="allow"` on the embed retains every undeclared key on every embed,
  which is the memory ADR-0010 measured as worth watching. It is scoped to
  one subtree rather than to the record models.
