"""Pydantic models describing the Jetstream message shape.

A message carries `did`, `time_us` and `commit` at the top level, with the
record nested inside the commit. `Commit.record` is a
discriminated union keyed on each record's `$type`, with one model per
collection in the client's `wantedCollections` filter.

The mart transform validates through these models and extracts
per-collection columns from the matched record, routing on
`commit.collection` (ADR-0010, ADR-0011). The types routing reads are
closed and the rest are open strings (ADR-0024). Ingest reads none of
this; it stores payloads verbatim. `record` is None for deletes.
"""

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

class PostRecordReply(BaseModel):
    """The `reply` subtree of a post record, if present."""

    root: StrongRef | None = None 
    parent: StrongRef | None = None

class PostEmbedExternal(BaseModel):
    """The `embed.external` subtree of a post record, if present."""

    uri: str | None = None
    title: str | None = None
    description: str | None = None

class PostRecordEmbed(BaseModel):
    """The `embed` subtree of a post record, if present.

    `$type` is taken as found, so an embed type outside the six in
    TDD-0004 validates and reaches `embed_type`. `extra="allow"` retains
    the per-type keys the six shapes carry, which the mart holds as the
    `embed` JSON column. `external` is filled on link-card embeds and
    absent on the rest.
    """

    model_config = ConfigDict(extra="allow")

    record_type: str | None = Field(default=None, alias="$type")
    external: PostEmbedExternal | None = None


class PostRecordFeature(BaseModel):
    """One typed span inside a facet.

    `$type` selects which of `tag`, `uri` and `did` carries the value, and
    clients write the other two as an explicit null. It is taken as found,
    so a feature type outside the three official ones validates and is
    dropped at extraction — 4 of 31,893 features sampled.
    """

    record_type: str | None = Field(default=None, alias="$type")
    tag: str | None = None
    uri: str | None = None
    did: str | None = None

class PostRecordFacet(BaseModel):
    """One facet of a post's rich text.

    `features` carries the typed spans, present on all 31,893 facets
    sampled. The byte range beside it feeds no mart column, so it stays
    undeclared. TDD-0004's three facet columns come from bucketing every
    feature in every facet on its `$type`.
    """

    features: list[PostRecordFeature] | None = None

class PostRecord(BaseModel):
    """A post. `langs` is optional; `text` and `createdAt` are always
    present."""

    # The `$type` alias carries the NSID the discriminated union selects
    # on. `tags`, `labels` and the client-name `via` stay undeclared, on
    # the measured shares TDD-0004 records.
    record_type: Literal["app.bsky.feed.post"] = Field(default="app.bsky.feed.post", alias="$type")
    text: str
    createdAt: str
    langs: list[str] | None = None
    reply: PostRecordReply | None = None
    embed: PostRecordEmbed | None = None
    facets: list[PostRecordFacet] | None = None

class StrongRef(BaseModel):
    """A `com.atproto.repo.strongRef`: a record pointer of `{uri, cid}`.

    Carries `subject` and `via` on the record models and `root` and
    `parent` inside a reply. Some clients add `commit`, `validationStatus`
    and `rkey` beside the pair, which validation drops.
    """

    cid: str
    uri: str

class LikeRecord(BaseModel):
    """A like. `subject` holds the `cid` and `uri` of the liked record."""

    record_type: Literal["app.bsky.feed.like"] = Field(default="app.bsky.feed.like", alias="$type")
    createdAt: str
    subject: StrongRef
    via: StrongRef | None = None

class RepostRecord(BaseModel):
    """A repost, carrying the same subject shape as a like."""

    record_type: Literal["app.bsky.feed.repost"] = Field(default="app.bsky.feed.repost", alias="$type")
    createdAt: str
    subject: StrongRef
    via: StrongRef | None = None

class FollowRecord(BaseModel):
    """A follow. `subject` is the followed account's DID."""

    record_type: Literal["app.bsky.graph.follow"] = Field(default="app.bsky.graph.follow", alias="$type")
    createdAt: str
    subject: str
    via: StrongRef | None = None

class BlockRecord(BaseModel):
    """A block. `subject` is the blocked account's DID."""

    record_type: Literal["app.bsky.graph.block"] = Field(default="app.bsky.graph.block", alias="$type")
    createdAt: str
    subject: str
    via: StrongRef | None = None

class Commit(BaseModel):
    """One repository operation: a create, update, or delete.
    `(did, rkey, rev)` is an event's identity."""

    rev: str
    operation: Literal["create", "update", "delete"]
    collection: Literal["app.bsky.feed.post", "app.bsky.feed.like", "app.bsky.feed.repost", "app.bsky.graph.follow", "app.bsky.graph.block"]
    rkey: str
    # Jetstream marks cid and record `omitempty`, documented as "Empty for
    # deletes", so both are optional here.
    cid: str | None = Field(default=None)
    record: PostRecord | LikeRecord | RepostRecord | FollowRecord | BlockRecord | None = Field(default=None, discriminator="record_type")

class BlueskyMessage(BaseModel):
    """A Jetstream commit event, as delivered on the wire.

    `time_us` is the event's position in the stream. Requiring `commit`
    admits commit events alone, matching what the raw store holds: ingest
    drops account and identity events, 0.16% of live traffic. The two
    unnumbered endpoints add a top-level `cursor`, dropped here.
    """

    did: str
    time_us: int
    commit: Commit