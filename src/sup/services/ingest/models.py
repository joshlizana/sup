"""Pydantic models describing the Jetstream message shape.

A message carries `did`, `time_us`, `kind`, and `commit` at the top level,
with the record nested inside the commit. `Commit.record` is a
discriminated union keyed on each record's `$type`, with one model per
collection in the client's `wantedCollections` filter.

Unused by the ingest path, which stores payloads verbatim. The M2 mart
transform validates through these models and extracts per-collection
columns from the matched record, routing on `commit.collection`
(ADR-0010, ADR-0011). `record` is None for deletes.
"""

from pydantic import BaseModel, Field
from typing import Literal, Union, List, Dict


class PostRecord(BaseModel):
    """A post. `langs` is optional; `text` and `createdAt` are always
    present."""

    # The `$type` alias carries the NSID the discriminated union selects
    # on. Records also carry fields such as `reply`, `embed`, and `facets`,
    # which each model leaves undeclared.
    record_type: Literal["app.bsky.feed.post"] = Field(default = "app.bsky.feed.post", alias="$type")
    text: str
    createdAt: str
    langs: List[str] | None = None

class LikeRecord(BaseModel):
    """A like. `subject` holds the `cid` and `uri` of the liked record."""

    record_type: Literal["app.bsky.feed.like"] = Field(default = "app.bsky.feed.like", alias="$type")
    createdAt: str
    subject: Dict[str, str]

class RepostRecord(BaseModel):
    """A repost, carrying the same subject shape as a like."""

    record_type: Literal["app.bsky.feed.repost"] = Field(default = "app.bsky.feed.repost", alias="$type")
    createdAt: str
    subject: Dict[str, str]

class FollowRecord(BaseModel):
    """A follow. `subject` is the followed account's DID."""

    record_type: Literal["app.bsky.graph.follow"] = Field(default = "app.bsky.graph.follow", alias="$type")
    createdAt: str
    subject: str

class BlockRecord(BaseModel):
    """A block. `subject` is the blocked account's DID."""

    record_type: Literal["app.bsky.graph.block"] = Field(default = "app.bsky.graph.block", alias="$type")
    createdAt: str
    subject: str

class Commit(BaseModel):
    """One repository operation: a create, update, or delete.
    `(did, rkey, rev)` is an event's identity."""

    rev: str
    operation: str
    collection: str
    rkey: str
    # Jetstream marks cid and record `omitempty`, documented as "Empty for
    # deletes", so both are optional here.
    cid: str | None = Field(default=None)
    record: Union[PostRecord, LikeRecord, RepostRecord, FollowRecord, BlockRecord] | None = Field(default=None, discriminator="record_type")

class BlueskyMessage(BaseModel):
    """A Jetstream event, as delivered on the wire.

    `time_us` is the event's position in the stream. `kind` is `commit`,
    `identity`, or `account`.
    """

    did: str
    time_us: int
    kind: str
    commit: Commit