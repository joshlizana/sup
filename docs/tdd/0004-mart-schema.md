# TDD-0004: Mart schema

## Summary

The mart holds five per-collection tables and one reject table, created by
the transform and written only by it
([TODO.md](../TODO.md) M2). This document fixes the columns of each, derived
from a full pass over a 22.7 GB raw store: 28,996,394 events spanning
26.5 hours, `time_us` 1786775698001562 to 1786871261061837. Every share and
count below comes from that store.

DuckLake requires the tables to exist before the first insert — the catalog
defines the schema and Parquet files are matched to it by field ID at read
time. Adding a column later is a metadata-only `ALTER TABLE`; changing a
column's type is restricted to lossless promotions such as `INT32` to
`INT64`. Widths here are chosen to leave that door open.

## Goals / Non-goals

**Goals:**

- One table per collection, each answerable without a JSON parse in the
  common query.
- Every event lands somewhere: a create, an update, a delete, or a reject.
- Column types that survive a Bluesky lexicon gaining fields.

**Non-goals:**

- Modelling the whole `app.bsky` lexicon. Fields are declared where the
  measured presence rate justifies a column.
- Reconstructing the original record byte-for-byte from the mart.
- Resolving references. `subject_uri` stays a string; joining likes to posts
  is the dashboard's problem.

## Design

### The measured shape of the source

| Collection | Events | Share | Creates | Updates | Deletes | Delete share |
|---|---:|---:|---:|---:|---:|---:|
| `app.bsky.feed.like` | 19,508,097 | 67.28% | 19,186,129 | 1 | 321,967 | 1.65% |
| `app.bsky.feed.post` | 3,623,705 | 12.50% | 3,357,775 | 1,643 | 264,287 | 7.29% |
| `app.bsky.feed.repost` | 3,101,534 | 10.70% | 2,955,894 | 0 | 145,640 | 4.70% |
| `app.bsky.graph.follow` | 2,450,192 | 8.45% | 2,033,663 | 0 | 416,529 | 17.00% |
| `app.bsky.graph.block` | 312,866 | 1.08% | 282,042 | 0 | 30,824 | 9.85% |

The five collections account for every row; the `wantedCollections` filter
admits nothing else. Deletes are 4.07% of events and updates 0.0057%,
concentrated in posts. `commit.cid` and `commit.record` are absent on all
1,179,247 deletes and present on all 27,817,147 creates and updates.

The record's `$type` equals `commit.collection` on all 27,817,147 records
carrying one, with zero disagreements. Routing on `commit.collection`
([ADR-0014](../adr/0014-mart-grain-and-transform-cadence.md)) and validating
through the discriminated union
([ADR-0011](../adr/0011-record-validation-and-routing-in-the-mart.md)) select
the same model.

### Columns every table carries

```sql
k1            UBIGINT   NOT NULL,  -- hash of did||rkey||rev (ADR-0026)
k2            UBIGINT   NOT NULL,  -- the same string salted
did           VARCHAR   NOT NULL,  -- 14 to 41 bytes; did:plc: and did:web:
rkey          VARCHAR   NOT NULL,  -- 13-byte TID on all but 2 rows
rev           VARCHAR   NOT NULL,  -- 13-byte TID, no exceptions
operation     VARCHAR   NOT NULL,  -- create | update | delete
event_time    TIMESTAMP NOT NULL,  -- decoded from rev (ADR-0018)
time_us       UBIGINT   NOT NULL,  -- stream position
endpoint      VARCHAR   NOT NULL,  -- the connection that witnessed it
received_at   TIMESTAMP NOT NULL,  -- when ingest wrote it to the raw store
processed_at  TIMESTAMP NOT NULL,  -- when the transform wrote it here
cid           VARCHAR,             -- NULL on deletes
created_at    TIMESTAMP,           -- client-supplied, NULL on deletes
```

`received_at` and `processed_at` bracket the pipeline: their difference is the
ingest-to-mart lag for one row, which the transform's cycle cadence drives
([ADR-0014](../adr/0014-mart-grain-and-transform-cadence.md)). The raw store
holds `received_at` as unix seconds, so the transform converts on the way in.

`(did, rkey, rev)` is the deduplication key
([ADR-0010](../adr/0010-deduplication-in-the-mart.md)), carried alongside as
`k1 UBIGINT` and `k2 UBIGINT`: a hash of `did||rkey||rev` and a salted hash
of the same string, which the insert anti-joins on
([ADR-0026](../adr/0026-uniqueness-on-insert-with-a-two-column-hash-key.md)).
`rkey` is `VARCHAR` because two follow rows carry a length other than 13.

`event_time` is the raw store's `tid_us`, which the reader fills from the
decoded `rev` and falls back to `time_us` for a TID that decodes outside 2022
to an hour ahead ([ADR-0018](../adr/0018-event-time-from-commit-rev.md)).
The column carries a value on every row of the 28,996,394-event store, so it
is `NOT NULL` and needs no coalesce at query time. It mixes two clocks for
the 0.2351% of rows ADR-0018 measured as taking the substitution, and the
mart preserves the raw store's inability to tell those apart.

`created_at` is whatever the client wrote. Across a 2,000,000-event sample,
97.4% use the 24-byte millisecond form and 98.7% end in `Z`, with the
remainder varying in precision and offset. Over the full store 1,600 post
records (0.048%) parse under no ISO-8601 reading and land as NULL, and
`created_at` disagrees with `time_us` by more than a day on 19,111 posts
(0.57%) and 7,509 follows (0.37%). It is a record attribute, and `event_time`
is the time dimension.

Every record-derived column below is nullable, because deletes fill the
identity columns and nothing else.

### `posts`

```sql
text             VARCHAR,      -- 100%, up to 1,156 bytes
langs            VARCHAR[],    -- 88.90%, one element in almost all cases
reply_root_uri   VARCHAR,      -- 45.54%
reply_root_cid   VARCHAR,
reply_parent_uri VARCHAR,
reply_parent_cid VARCHAR,
embed_type       VARCHAR,      -- 37.17%
embed            JSON,         -- the embed subtree; external keeps uri,
                               -- title and description
facet_tags       VARCHAR[],    -- #tag features
facet_links      VARCHAR[],    -- #link features, the uri
facet_mentions   VARCHAR[],    -- #mention features, the did
```

Three post fields stay out on their measured share: record-level `tags` at
0.94%, `labels.values[].val` at 1.33%, and the client-name `via` at 0.57%.
They join the undeclared fields below, reachable through the raw store
inside its retention window and absent from the mart.

`reply` is `{root, parent}` with both present whenever `reply` is, each a
strong ref of `{uri, cid}`, so four flat columns carry it without loss.

`embed` keeps its subtree as JSON because the six types below have six
shapes; `embed_type` carries the discriminator so counts and filters avoid
the parse. The `external` subtree carries `uri`, `title` and `description`,
and the blob thumbnail beside them stays out — 7,627 of 8,716 external
embeds in a 600,000-message sample hold one, and none of the mart's views
read it. Shares are of the 1,248,626 posts holding an embed:

| `embed_type` | Posts | Share |
|---|---:|---:|
| `app.bsky.embed.images` | 511,568 | 40.97% |
| `app.bsky.embed.external` | 456,068 | 36.53% |
| `app.bsky.embed.record` | 178,924 | 14.33% |
| `app.bsky.embed.video` | 61,120 | 4.89% |
| `app.bsky.embed.recordWithMedia` | 32,443 | 2.60% |
| `app.bsky.embed.gallery` | 8,394 | 0.67% |

A further 109 posts carry an embed of some other `$type`, including
`app.midnightsky.post` and `dev.chomu.embed.secret`. `embed_type` takes the
string as found, so a seventh official type appears in the counts on the day
it ships.

Facets arrive as a list of ranges each holding a list of features, and the
feature `$type` is what analysis asks about: hashtags, outbound links, and
the mention graph. Three arrays flatten that to one array lookup. Across a
2,000,000-event sample the features split 97,667 tag, 39,130 link, and 6,606
mention. `facets` is present on 21.95% of posts.

### `likes` and `reposts`

```sql
subject_uri        VARCHAR,   -- 100% of records
subject_cid        VARCHAR,
subject_collection VARCHAR,   -- the NSID parsed out of subject_uri
via_uri            VARCHAR,   -- likes 17.73%, reposts 29.20%
via_cid            VARCHAR
```

`subject` is a strong ref. `subject_collection` is derived at transform time
because the subject is not always a post: a sample of 1,500,000 events
returns feed generators and third-party lexicons such as
`community.blacksky.feed` alongside `app.bsky.feed.post`. A query for "likes
on posts" needs the distinction, and parsing an `at://` URI in the dashboard
costs more than storing the answer.

`via` is a strong ref pointing at the repost the like or repost came through,
covering 3,402,182 likes and 863,199 reposts — the difference between a like
on a post and a like on a post someone amplified.

### `follows` and `blocks`

```sql
subject  VARCHAR,   -- 100% of records; a DID, did:plc: or did:web:
via_uri  VARCHAR,   -- follows 15.88%; blocks 0%
via_cid  VARCHAR
```

The graph collections carry a bare DID as `subject`, not a strong ref.
`follows` carries `via` on 322,855 records, pointing at a starter pack. Both
tables declare the pair: no block record among 282,042 carries a `via`, and
holding the column costs a NULL that Parquet encodes away.

### `rejects`

```sql
k1           UBIGINT   NOT NULL,
k2           UBIGINT   NOT NULL,
did          VARCHAR   NOT NULL,
rkey         VARCHAR   NOT NULL,
rev          VARCHAR   NOT NULL,
collection   VARCHAR   NOT NULL,   -- read from the payload string
received_at  TIMESTAMP NOT NULL,
processed_at TIMESTAMP NOT NULL,
time_us      UBIGINT   NOT NULL,
error        VARCHAR   NOT NULL,
payload      JSON      NOT NULL
```

Validation failures land here, keeping one writer per store
([ADR-0013](../adr/0013-service-owned-pruning.md)). Ingest decodes
everything outside the record into `raw.events` columns, all of them
`NOT NULL`, so a rejected row carries its full identity and its key pair
regardless of what the record held. Its row count is an instrument: a rate
change means the lexicon moved.

### Undeclared fields

Records carry keys outside the lexicon. The full pass found 54 distinct ones,
written by bridges and third-party clients: `bridgyOriginalUrl` (47,004
posts), `bridgyOriginalText` (46,372), `fediverseId`, `uk.skyblur.post.uri`,
`net.mimonelu.klearsky.via`, `dk.aggemam.bot.id`, `social.rhize.*`, an
`emoji`/`seiranReactionId` pair on 25 likes, and an empty-string key on 113
records across three collections.

Two of them hold conflicting types across rows: `dk.aggemam.bot.id` is an
integer on 9 posts and a string on 2, and `text`/`facets` appear on likes as
JSON null on 3,357 records and as a string and array on 1 each. Nothing here
earns a column, and a typed column would break on the second row. Pydantic
ignores unknown keys by default, which is the behaviour the transform wants:
these records validate and land in the mart with their extensions dropped.
The `embed` subtree is the exception. It carries `extra="allow"`, so an
undeclared key inside an embed reaches the `embed` JSON column
([ADR-0024](../adr/0024-strict-at-the-boundary-open-at-the-leaves.md)).

### Type widths and evolution

DuckLake permits lossless type promotion and refuses narrowing, so a type
chosen too small is repairable and one chosen too large is not. Every integer
column is 64 bits wide, unsigned where the value is: `time_us` is a
microsecond position and `k1`/`k2` fill the full range `hash()` returns. 64
bits is the ceiling: DuckDB writes `HUGEINT` and
`UHUGEINT` to Parquet as `DOUBLE`, keeping 53 bits of mantissa and discarding
the rest without raising. A 128-bit column round-tripped through DuckLake
returns a different value on every row — 20,000,000 of 20,000,000 measured,
with distinct values falling from 19,559,726 to 19,359,726. `UBIGINT` writes
as `INT64`/`UINT_64` and round-trips intact.

Parquet carries 128 bits through other encodings, so a column needing that
width holds a 16-byte `BLOB`, a 32-character hex string, or a pair of
`UBIGINT`s. `DECIMAL(38,0)` writes as `FIXED_LEN_BYTE_ARRAY` and round-trips
exactly across the 29% of the unsigned 128-bit range it spans. Identifier columns are `VARCHAR`: `did` ranges from 14 to
41 bytes and `did:web:` has no upper bound. Parquet encodes by observed value
range
regardless of the declared width, so the generous choice costs no storage.

DuckLake accepts `NOT NULL` and `DEFAULT`, and rejects `CHECK`, `PRIMARY KEY`
and `UNIQUE` with `Not implemented Error` at `CREATE TABLE`. Every invariant
beyond nullability is the transform's to hold in code: deduplication on
`(did, rkey, rev)` and the watermark's single row both live there, with
nothing in the schema to catch a violation.

Table names are written qualified, as `sup_lake.main.<table>`. `USE` applies
to the cursor that runs it, and `aioduckdb` opens a cursor per statement, so
an unqualified `CREATE TABLE` lands in the in-memory DuckDB the catalog
attaches to and disappears when the connection closes, raising nothing. The
same boundary governs the per-chunk Arrow flush, which registers its table
and inserts it through `execute_on_self`
([ADR-0025](../adr/0025-connection-local-state-in-aioduckdb.md)).

`ATTACH` on a catalog path that holds no catalog creates an empty one and
succeeds, so a wrong path yields a mart with no tables and no error. The
catalog is `sup.ducklake` under `Config.ducklake_path`.

The tables are created with `CREATE TABLE IF NOT EXISTS` once at transform
startup, from the single writer. DDL concurrent with writes is where the
DuckLake catalog-desync reports cluster.

## Open questions

- Whether the mart partitions by day. A 24-hour window
  ([ADR-0012](../adr/0012-rolling-retention-window.md)) at 29M rows may not
  need it, and partitioning interacts with
  `ducklake_expire_snapshots`.
- Whether `facet_links` stores the URI or a parsed domain. The domain is
  what a "top linked sites" view groups on, and the URI is what reconstructs
  the link.

## Alternatives considered

**One wide table with a `collection` column.** Rejected: the union of five
records is mostly NULL, and the post columns dominate a table where posts are
12.50% of rows.

**Flattening `embed` into per-type columns.** Six types with disjoint fields
produce roughly twenty columns populated on under 5% of posts each. The JSON
column with `embed_type` beside it answers the same questions.

**Storing `created_at` as the raw string alongside the parsed timestamp.**
The parse fails on 1,600 rows in 27.8M, and the raw store holding the
original is pruned to a buffer ([ADR-0013](../adr/0013-service-owned-pruning.md)),
so the string is unrecoverable after the fact. A single `TIMESTAMP` with NULL
for the unparseable is the trade taken.

**Carrying the raw store's `pk` into the mart.** Deduplication collapses
several raw rows into one mart row, leaving the column ambiguous, and pruning
makes it dangle.
