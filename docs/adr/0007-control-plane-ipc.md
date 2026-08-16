# ADR-0007: Control plane IPC (Unix domain socket / named pipe, not TCP)

## Status

Accepted

## Options considered

- **TCP loopback (127.0.0.1).** The most familiar option and the weakest
  fit. It is visible to any process or user that can reach localhost, so
  access control stops at "can reach the loopback interface," and loopback
  services becoming unintentionally reachable through bind-address or
  firewall mistakes is a recurring class of incident — a risk that does not
  exist for a channel never on the network stack. Also 30–66% higher latency
  and up to 7x lower throughput than a Unix socket, and it needs a port plus
  a discovery mechanism.
- **Unix domain socket or Windows named pipe, via stdlib
  `multiprocessing.connection`.** Binds to a filesystem path and never
  reaches the network stack. `Listener`/`Client` picks the right primitive
  per platform (`AF_UNIX`, `AF_PIPE`) and provides HMAC `authkey`
  authentication independent of file permissions, so there is no wire
  protocol or per-platform branching to hand-roll, and the fixed-path bind
  doubles as a single-instance lock. Chosen. Filesystem-backed is not
  filesystem-restricted, though: `bind()` creates the socket at `0777` minus
  umask, typically `0755`, connectable by any local user until tightened
  explicitly. CPython shipped this class of issue as
  [CVE-2022-42919](https://github.com/python/cpython/issues/97514), where the
  forkserver's abstract control socket let any local user inject code and the
  fix was filesystem-backed sockets so permissions could apply.

## Decision

**`multiprocessing.connection`, Unix domain socket or named pipe.** Each
service binds its control socket at a fixed path in the shared data
directory on startup, whether launched standalone or by the orchestrator —
the correctness risk exists either way.

**Single-instance lock**, paired with the socket rather than relying on the
bind:

- A `flock`'d PID file is the actual lock. `flock` acquisition is atomic,
  avoiding the race a "try to connect, unlink if refused, then bind" sequence
  would have between check and act.
- On startup, acquire the flock non-blocking. Success means no live holder,
  so bind the socket and remove any stale file. Failure means another
  instance is running: print a clear error and exit non-zero.

**Socket and authkey hardening, both required:**

- `os.chmod(socket_path, 0o600)` immediately after the `Listener` creates the
  file, closing the default-permissive gap.
- The `authkey` is generated once and persisted in the data directory, since
  two standalone invocations must arrive at the same key with no shared
  parent. Written restrictive from the moment it exists —
  `os.open(path, O_CREAT | O_WRONLY | O_EXCL, 0o600)`, not `open()` then
  `chmod()`, which leaves a window at default permissions.

**Scope:** ingest and transform only, the writers where a second instance is
a correctness risk. A duplicate dashboard is a port conflict, not a
correctness problem.

## Why

Both services need a guarantee that only one instance runs against a data
directory — a second would corrupt shared state, since the writer and
watermark logic assume a single writer — plus a readiness signal the
orchestrator can wait on during startup ordering. One mechanism covers both.

Neither piece of hardening substitutes for the other: the chmod fixes the
socket's default gap, and the authkey is a check that does not depend on
permissions having been set correctly.

Accepted costs:

- The orchestrator's startup wait uses "the control socket becomes
  connectable" as its readiness signal, reusing this mechanism rather than
  polling for a file.
- A crashed process's stale socket is handled by the flock check rather than
  being an unhandled edge case.
- This is additive to signals. Both services still respond to SIGTERM
  standalone, with no orchestrator or client involved
  ([ADR-0006](0006-cli-orchestration-model.md),
  [TDD-0002](../tdd/0002-cli-orchestration.md)).
- The control plane carries pause, resume, shutdown and `status` over the
  same socket, with the TUI as a client
  ([ADR-0015](0015-tui-as-control-plane-client.md)).
