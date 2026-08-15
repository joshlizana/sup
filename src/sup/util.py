"""Shared logging helpers.

`register`/`Identity` tag every log line with a token unique to the
instance that emitted it. `displayTime` renders a `time_us` cursor as a
readable UTC timestamp.
"""

import sys
import uuid
from datetime import datetime, timezone
import logging

def register(logger, class_name):
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

    def __init__(self, logger, identifier: str):
        super().__init__(logger, extra={"id": identifier})

    def process(self, msg, kwargs):
        """Prefix the message with the identifier and set `id` on the
        `LogRecord`, where a formatter can reach it as `%(id)s`."""
        kwargs["extra"] = self.extra
        return f"[{self.extra['id']}] {msg}", kwargs


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