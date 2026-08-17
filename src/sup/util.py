"""Shared helpers.

`register`/`Identity` tag every log line with a token unique to the
instance that emitted it. `displayTime` renders a `time_us` cursor as a
readable UTC timestamp, and `tid_us` decodes a TID into one.
"""

import sys
import uuid
import time
import logging
from datetime import datetime, timezone

def register(logger: logging.Logger, class_name: str):
    """Return an `Identity` adapter tagged for one instance.

    Called once per instance from `__init__`, passing the calling module's
    own `logging.getLogger(__name__)`. Free functions pass a literal name
    in place of `type(self).__name__`.
    """
    id = str(uuid.uuid4().hex[:8])
    return Identity(logger, identifier=f'{class_name}:{id}')


class Identity(logging.LoggerAdapter):
    """A `LoggerAdapter` that prefixes each message with `Class:token`,
    e.g. `Reader:a1b2c3d4`. The token is fixed for the adapter's life."""

    def __init__(self, logger: logging.Logger, identifier: str):
        super().__init__(logger, extra={"id": identifier})

    def process(self, msg: str, kwargs: dict):
        """Prefix the message with the identifier and set `id` on the
        `LogRecord`, where a formatter can reach it as `%(id)s`."""
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
        kwargs["extra"] = self.extra
        return f" {now_utc} [{self.extra['id']}] {msg}", kwargs


def displayTime(timestamp: int):
    """Render a `time_us` cursor as a UTC timestamp.

    `sys.maxsize` is the live tail's end cursor and renders as "Forever";
    it falls outside the range `datetime` accepts.
    """
    if timestamp == sys.maxsize:
        return "Forever"
    epoch_seconds = timestamp / 1_000_000
    dt_utc = datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
    return dt_utc.strftime("%Y-%m-%d %H:%M:%S %Z")

def tid_us(tid: str) -> int:
    """Decode a TID to microseconds, or 0 when the result is implausible.

    Some implementations mint `rev` as a counter rather than a clock, so
    a well-formed TID can decode to seconds after the epoch (ADR-0018).
    Anything outside 2022 to an hour ahead of now returns 0. The forward
    hour covers a PDS clock running ahead of this one, measured at ~98 s.
    """
    ALPHA = "234567abcdefghijklmnopqrstuvwxyz"
    v = 0
    for c in tid:
        v = v * 32 + ALPHA.index(c)
    tid_us = v >> 10

    return tid_us if tid_us > 1_640_995_200_000_000 and tid_us < int(time.time() * 1_000_000) + 3_600_000_000 else 0