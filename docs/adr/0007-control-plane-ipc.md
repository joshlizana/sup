# ADR-0007: Control plane IPC (Unix domain socket / named pipe, not TCP)

## Status

Accepted

## Context

`sup ingest` and `sup transform` need a way to (a) guarantee only one
instance of each runs against a given data directory at a time — a second
concurrent instance of either would corrupt shared state (the batched-writer
and watermark logic isn't designed for concurrent writers) — and (b) give
the orchestrator (ADR-0006) a readiness signal during startup ordering,
beyond just polling for a file's existence.

The original idea was a TCP loopback socket (`127.0.0.1`). Researched
before accepting it, since this is local IPC where the security/performance
tradeoffs are well-established and easy to get wrong by default.

## Options considered

### TCP loopback (127.0.0.1)

- Works, and is the most immediately familiar option.
- Visible to **any** process or user on the machine that can reach
  localhost — there's no access control beyond "can reach the loopback
  interface," which is a much weaker boundary than it sounds like. TCP
  services on loopback "accidentally becoming reachable" (bind-address
  mistakes, firewall/interface changes) is a documented, recurring class of
  real-world incidents — a risk category that doesn't exist for a channel
  that was never on the network stack to begin with.
- Slower than a Unix domain socket for same-machine IPC (measurably —
  30-66% higher latency, up to 7x lower throughput).
- Requires picking/managing a port and a discovery mechanism for other
  processes to find it.

### Unix domain socket / Windows named pipe, via stdlib `multiprocessing.connection`

- Binds to a filesystem path, not a port, and is never exposed to the
  network stack at all — but **the socket file's permissions are not
  restrictive by default.** `bind()` creates it at `0777` minus the
  process's umask (typically `0755`), which is connectable by any local
  user, not just the owner, unless explicitly tightened after creation
  (see Decision). This is the same class of issue CPython itself shipped as
  a real CVE ([CVE-2022-42919](https://github.com/python/cpython/issues/97514)) —
  the `multiprocessing` forkserver's control socket used to be an abstract
  socket with no filesystem permissions at all, letting any local user
  inject code; the fix was moving to filesystem-backed sockets specifically
  so permissions could apply. Filesystem-backed alone isn't the same as
  filesystem-*restricted* — that step has to be done explicitly.
- Faster than TCP loopback for the same use case.
- `multiprocessing.connection.Listener`/`Client` (stdlib) picks the right
  primitive automatically per platform (`AF_UNIX` on Unix, `AF_PIPE` named
  pipes on Windows) and provides built-in HMAC-based `authkey`
  authentication, independent of file permissions — CPython's own
  maintainers have discussed wanting exactly this for the forkserver socket
  too, "so that it isn't even relying on filesystem permissions" alone. No
  custom wire protocol or per-platform socket-API branching needs to be
  hand-rolled either way.
- The fixed-path bind has a useful side effect: it doubles as a
  single-instance lock (see Decision) — a second process can't bind the
  same path while a live one holds it.

## Decision

**`multiprocessing.connection`, Unix domain socket / named pipe, not TCP.**
Each of `sup ingest` and `sup transform` binds its own control socket at a
fixed path inside the shared data directory (e.g.
`<data-dir>/control/ingest.sock`), on startup, regardless of whether it was
launched standalone or by the orchestrator — the correctness risk from a
second concurrent instance exists either way.

**Single-instance lock design**, paired with the socket rather than relying
on the bind alone:

- A `flock`'d PID file (e.g. `<data-dir>/control/ingest.lock`) is the actual
  lock — `flock` acquisition is atomic, avoiding a race that a naive
  "try to connect, unlink if refused, then bind" sequence would have between
  the check and the act.
- On startup: attempt to acquire the flock (non-blocking). Success → proceed
  to bind the control socket (removing any stale socket file left behind by
  a prior unclean exit, now safe since the flock proves no live holder
  exists). Failure → another live instance holds it; print a clear error and
  exit non-zero rather than silently doing nothing or racing.

**Socket and authkey hardening — both required, neither is a substitute for
the other:**

- Immediately after the `Listener` creates the socket file,
  `os.chmod(socket_path, 0o600)` — closes the actual default-permissive gap
  described in Options above.
- The `authkey` is generated once (`os.urandom(32)` or similar) and
  persisted to a file in the shared data directory (e.g.
  `<data-dir>/control/authkey`), since two independent, standalone
  invocations of `sup ingest` need to arrive at the same key with no shared
  parent process to hand one off — this isn't optional given the
  standalone-invocation requirement above. Written with restrictive
  permissions from the moment it exists — `os.open(path, os.O_CREAT |
  os.O_WRONLY | os.O_EXCL, 0o600)`, not a separate `open()` + `chmod()`
  after the fact, which would leave a brief window at default permissions.
- Both matter independently: the chmod fixes the socket's own default gap;
  the authkey remains a check that doesn't depend on the permissions being
  set correctly, matching CPython's own stated reasoning for wanting
  authentication on its forkserver socket rather than relying on
  filesystem permissions alone.

**Scope of the lock:** ingest and transform only — the writers where a
second instance is a correctness risk. Not applied to the dashboard
(stateless/read-only; a duplicate instance is a port-conflict nuisance, not
a correctness problem).

**Not a replacement for signals:** this is purely additive. `sup ingest` and
`sup transform` must still respond correctly to SIGTERM/Ctrl+C when run
standalone, with no orchestrator or control-plane client involved — that
remains the primary shutdown mechanism (per
[ADR-0006](0006-cli-orchestration-model.md)/[TDD-0002](../tdd/0002-cli-orchestration.md)).
The control plane is available *in addition* when orchestrated.

## Consequences

- The orchestrator's startup-ordering wait (ingest → transform → dashboard →
  TUI, from TDD-0002) can use "ingest's control socket becomes connectable"
  as its readiness signal instead of polling for the raw store file to
  exist — more precise, and reuses the same mechanism rather than adding a
  second one.
- A crashed process's stale socket file is now handled deliberately (via the
  flock check) rather than being an unhandled edge case.
- Explicitly deferred: whether the control plane grows beyond lock +
  readiness into richer live status/command queries (e.g. the TUI querying
  ingest rate directly instead of via raw-store reads, per TDD-0001 [4a]) is
  not decided. Out of scope until a real need shows up — not designing for
  it speculatively now.
