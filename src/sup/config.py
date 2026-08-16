"""Shared config: the resolved data directory every other module derives
its storage paths from, plus the Jetstream endpoints to read from."""

import time
from pathlib import Path
from pydantic import BaseModel, ConfigDict, computed_field
from platformdirs import PlatformDirs

class Config(BaseModel):
    """Frozen settings, constructed fresh at each point of use.

    Computed fields resolve on every access: `retention` against the
    current clock, the path properties creating their directories.
    """

    model_config = ConfigDict(frozen=True)

    default_path: Path = PlatformDirs("sup", ensure_exists=True).user_data_path

    # Every endpoint carries the same firehose; readers take disjoint time
    # ranges across them. - (endpoint, v2)
    endpoints: list[tuple[str, int, int]] = [("wss://jetstream1.us-east.bsky.network/subscribe", 0),
                                            ("wss://jetstream2.us-east.bsky.network/subscribe", 0),
                                            ("wss://jetstream1.us-west.bsky.network/subscribe", 0),
                                            ("wss://jetstream2.us-west.bsky.network/subscribe", 0),
                                            ("wss://jetstream.us-west.bsky.network/subscribe", 1),
                                            ("wss://jetstream.us-east.bsky.network/subscribe", 1)]

    @computed_field
    @property
    def retention(self) -> int:
        """Policy cap on how far back to look, as a `time_us` timestamp.

        `GapAuditor` uses whichever is more recent, this or the oldest
        position the endpoints still serve, and falls back to this when
        every probe fails.
        """
        return (int(time.time() * 1_000_000) - (24 * 60 * 60 * 1_000_000))# 24 hours ago in microseconds

    @computed_field
    @property
    def data_path(self) -> Path:
        """Directory holding `raw.db` and `index.db`. Created on access."""
        data_path = self.default_path / "data"
        data_path.mkdir(exist_ok=True)
        return data_path

    @computed_field
    @property
    def control_path(self) -> Path:
        """Directory holding each service's lock, socket, and authkey,
        separate from `data_path`. Created on access."""
        control_path = self.default_path / "control"
        control_path.mkdir(exist_ok=True)
        return control_path
