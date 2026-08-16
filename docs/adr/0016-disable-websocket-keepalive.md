# ADR-0016: Jetstream connections run without websocket keepalive

## Status

Accepted

## Options considered

- **Raise `ping_timeout`** to 60 or 120 seconds. Keeps automatic
  dead-connection detection, but pong latency scales with throughput, so any
  fixed timeout names a rate above which the client disconnects itself. It
  moves the threshold rather than removing it.
- **Raise `max_queue`** so `websockets` reads further ahead and reaches a
  pong sooner. Addresses one contributing factor, buying headroom in
  proportion to the buffer and trading memory for it. The pong still queues
  behind data at a high enough rate.
- **Disable client keepalive** with `ping_interval=None`: no
  client-initiated pings, so no client-initiated timeout. Removes the failure
  rather than deferring it, and gives up automatic detection of a socket that
  dies without a close frame. Chosen.

## Decision

`JetstreamClient.connect()` passes `ping_interval=None`.

## Why

Under sustained backfill, readers dropped their connections repeatedly:

```
Error occurred while reading from Jetstream: sent 1011 (internal error)
keepalive ping timeout; no close frame received
```

`sent` identifies the closer as the client. `websockets` 17.0.1 defaults to
`ping_interval=20`, `ping_timeout=20` and `max_queue=16`, so it pings every
20 seconds and closes the connection when the pong does not arrive within
another 20. Each drop re-queued and re-read the range, so ingest continued at
a reduced and oscillating rate.

Two plausible causes were tested and neither reproduces the failure. A
`recv()` stalled for 50 seconds, mimicking a reader blocked on a full output
queue, leaves the connection healthy. Blocking the event loop for 45 seconds,
and again for five consecutive 25-second stretches, also leaves it healthy —
a blocked loop delays the ping timer as much as the pong, so nothing times
out.

Load reproduces it. On a **single** connection consuming 4,180 messages/sec,
an explicit `ws.ping()` failed to round-trip within 10 seconds: the pong is a
frame in an already-saturated stream and waits behind the data ahead of it.
Production runs six to twelve such connections on one event loop, so
exceeding the 20-second timeout is routine while the connection stays
healthy. Measured directly after the change: 125,388 messages in 30 seconds
on one connection, with the connection still `OPEN`.

Keepalive exists to detect a peer that has stopped responding, and a firehose
reports that condition through the data itself, so the check is redundant
here.

Accepted costs:

- **A silently dead socket now hangs `recv()`** instead of raising, and that
  reader's range stalls until shutdown. The cost is bounded to that one
  range, since the other readers keep claiming from the shared queue.
  Wrapping `client.recv()` in an `asyncio.timeout` converts the stall into
  the error path the reader already handles, and is the fix if this is ever
  observed.
- **The server can still detect us.** `websockets` answers server-initiated
  pings automatically regardless of `ping_interval`, so Jetstream's own
  liveness checks are unaffected.
- Reconnect churn is **not** the source of the raw store's duplicates. The
  share was 11.41% inside the actively backfilled window before this change,
  and a clean run afterwards measured 20.70% across 33.1M rows, so removing
  the disconnects did not reduce it. The cause is cursor clamping
  ([ADR-0020](0020-live-tail-cursor-clamping.md)).
