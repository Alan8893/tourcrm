# ADR-0044 — Scheduler for Periodic System Jobs

- **Status:** Accepted
- **Date:** 2026-10-06
- **Decision owner:** Product Owner / CTO
- **Decision type:** Architecture decision (documentation only — no implementation in this ADR)
- **Supersedes:** none
- **Refines:** ADR-0007 (background processing) for periodic system jobs; the `scheduler` entry of `docs/08-infrastructure/infrastructure-and-devops.md` §5 and the "worker/scheduler technology" item of §24
- **Does not close:** ODR-005 (Redis/queue worker framework) — remains open for asynchronous queued jobs
- **Related:** ADR-0012, ADR-0018, Issue #281, Issue #289, Issue #290, `apps/api/README.md` "Event lifecycle reconciliation"

## 1. Context

Issue #281 implemented time-based Event lifecycle reconciliation (ADR-0018 "Time-based lifecycle synchronization", `docs/04-modules/events-and-schedule.md` §4) as a system CLI:

```bash
python -m app.cli.reconcile_event_lifecycle
```

The command moves due Events `published -> in_progress` and `in_progress -> completed`. Its properties (see `apps/api/app/events/lifecycle_reconciliation.py` and `apps/api/README.md`) are:

- **idempotent** — re-running is a no-op for every already-synchronized Event;
- **catch-up** — one run converges every transition a previous run missed, as ADR-0018 requires ("the system must not require the service to be running at the exact timestamp");
- **concurrency-safe** — each due Event is reconciled in its own transaction under the same `SELECT ... FOR UPDATE` row lock as the manual status endpoint; two concurrent runs serialize on the row;
- **per-Event failure isolation** — a failing Event does not stop the others; the run still exits non-zero if any Event could not be reconciled, and the next run retries it;
- **system operation** — no HTTP endpoint exposes it; it is reachable only from the backend codebase as a CLI.

Issue #289 found the operational gap: nothing in the repository invokes this command periodically. There is no production deployment topology and no scheduler, so without manual operator setup Event statuses change only through manual controls.

The canonical documents do not allow choosing a scheduler ad hoc:

- `docs/08-infrastructure/infrastructure-and-devops.md` §24 requires an ADR for "worker/scheduler technology" and, separately, for "deployment topology" before production.
- `docs/03-architecture/application-architecture.md` §3 states that worker technology is an architecture decision documented separately before implementation.
- ADR-0008 ODR-005 keeps the Redis worker framework open until the first mandatory queued background job.

This ADR records the PO/CTO decision for Issue #290 and selects the scheduler technology.

## 2. Problem

A CLI is an execution entrypoint, not a schedule. On its own it provides none of the following:

1. **Periodic triggering** — something must start it once per minute, indefinitely.
2. **Process ownership** — the trigger must live in a defined process with a defined lifecycle (start, restart, shutdown), not in an operator's ad-hoc host crontab that is invisible to the repository.
3. **Observable failure** — a non-zero exit must be visible in logs; otherwise a permanently failing reconciliation looks identical to a healthy one.
4. **Continuity after failure** — one failed run must not stop later runs.
5. **A single trigger per deployment** — duplicate execution must not be the normal operating mode, even though reconciliation tolerates it.

The obvious shortcut — a background loop inside the FastAPI process — conflicts with the stateless-API principle (`application-architecture.md` §2.3): every API replica (and every uvicorn worker) would start its own competing loop, the job would share the web process's lifecycle (reloads, restarts, request load), and scaling the API (`infrastructure-and-devops.md` §22 "multiple backend replicas") would silently multiply the job.

## 3. Decision

1. **Dedicated scheduler.** TourCRM runs periodic system jobs from a dedicated scheduler process/service.
2. **Separate from FastAPI.** The scheduler is not part of the FastAPI web process. No in-process scheduler, background loop, startup task or `BackgroundTasks`-based timer is used for periodic system jobs in the API process.
3. **Single replica.** The scheduler is intended to run as exactly one replica per deployment. It does not scale with the backend API. Changing this requires a new ADR.
4. **CLI-based execution.** The scheduler invokes existing system CLI entrypoints (`python -m app.cli.<command>`). It contains no business logic and does not reimplement, wrap or partially duplicate reconciliation. The CLI's own semantics (idempotency, locking, catch-up, exit status) are authoritative.
5. **Event lifecycle cadence.** `python -m app.cli.reconcile_event_lifecycle` is scheduled **once per minute**.
6. **Failure isolation between iterations.** A failed iteration is observable (logged with its exit status) and never prevents subsequent scheduled iterations.
7. **Safety layer.** The existing reconciliation idempotency and row-locking guarantees remain the final safety layer. The scheduler architecture does not rely on duplicate execution as normal operation: the scheduler must not start a second instance of a job while the previous instance of the same job is still running.
8. **No distributed job framework.** Redis, Celery, RQ, arq, Dramatiq or any other broker-backed job framework is not introduced for this requirement.
9. **Technology.** The scheduler is implemented with **supercronic** (§5).
10. **Scheduler technology ≠ deployment topology.** This ADR fixes the scheduler contract and technology. It does not design TourCRM's production deployment (§7).

Adding any further periodic job to the scheduler (for example `python -m app.cli.reconcile_achievements`) is **not** decided here; it requires its own approved decision stating the job and its cadence. The mechanism defined here is reusable for such jobs.

## 4. Technology options

Options were limited to mechanisms that satisfy §3 without new infrastructure. Criteria are those of Issue #290.

| Criterion | Debian/Vixie `cron` in a container | BusyBox `crond` | **supercronic** | Custom Python loop / APScheduler in a separate process | Ofelia (Docker-label job runner) | Host `systemd` timers / host crontab |
|---|---|---|---|---|---|---|
| Operational simplicity | Medium — needs env, logging and PID 1 workarounds | Medium — small, but same env/PID 1 caveats | High — one static binary + one crontab file | Low — custom code TourCRM must own | Medium — extra service | Medium — outside the repository |
| Container compatibility | Poor — designed for multi-user hosts, normally runs as root, purges the job environment (breaks env-driven config, §9) | Fair — foreground mode exists; typically runs as root | Designed for containers; runs as the image's non-root user; passes the container environment to jobs | Good | Needs the Docker socket (conflicts with §18 "Docker socket exposure minimized") | Not container-based; ties jobs to one host |
| Logging | Job output goes to mail/syslog unless redirected by hand | Daemon logs to stderr/file; job output not captured per job without manual redirection | Job stdout/stderr, start, success and exit status logged to the container output, tagged per job and iteration; optional `-json` structured output | Whatever we write | Container output | journald / syslog, outside container logging |
| Failure handling / exit status | Exit status not reported in a usable form | Minimal | Logs non-zero exit status at error level; scheduler keeps running | Whatever we write | Reported | Reported by systemd |
| Overlap protection | None by default | None | Built in: never starts a job while its previous instance runs; warns "job took too long" | Must be written | Configurable | `systemd`: built in; cron: none |
| Signal / shutdown | Poor as PID 1: does not forward `SIGTERM`, can orphan jobs | Poor as PID 1 | `SIGTERM`/`SIGINT`/`SIGQUIT` → graceful shutdown waiting for running jobs; reaps child processes as PID 1 | Must be written correctly | Good | Good (host init) |
| Testability | Low | Low | `supercronic -test <crontab>` validates the crontab without running jobs; usable in CI | Unit-testable, but it is new code to test | Low | Low |
| Extra infrastructure | None | None | None (single binary in an image) | New dependency (APScheduler) or new code | Docker socket access | Host-level configuration |
| Future production suitability | Weak | Fair | Good — portable to any container runtime and to an orchestrator later | Fair — maintenance burden grows with each job | Tied to the Docker engine | Tied to a specific host; contradicts the container baseline (§2, §5) |

Excluded without a full comparison:

- **Celery beat, RQ scheduler, arq cron, Dramatiq periodiq** — all require a Redis/broker; excluded by §3.8.
- **In-process FastAPI scheduler (including APScheduler inside the API)** — excluded by §3.2.
- **Kubernetes `CronJob` / cloud-managed schedulers** — depend on a deployment platform TourCRM does not use (`infrastructure-and-devops.md` §2 and §22: no Kubernetes at the first stage). Moving to one later requires a new ADR (§8).

## 5. Selected technology — supercronic

**supercronic** (https://github.com/aptible/supercronic) is selected: a single static binary that reads a standard crontab file and was built specifically to run cron schedules inside containers.

Rationale:

1. **Cron semantics without cron's container problems.** The schedule is a standard crontab expression (`* * * * *`), but the job inherits the container environment (so `DATABASE_URL` and other env-driven configuration work as in the backend), runs as a non-root user (`infrastructure-and-devops.md` §6.1), and logs to the container output instead of mail/syslog.
2. **Failure behaviour matches §3.6 directly.** Each run's start, output, success or non-zero exit status is logged with the job and iteration; a failing run does not stop the schedule.
3. **Overlap protection matches §3.7 directly.** By default a job is not started again while its previous instance is still running, and a falling-behind job is reported. TourCRM therefore gets "no duplicate execution as normal operation" from the scheduler, with reconciliation locking as the backstop.
4. **Correct process lifecycle.** Graceful shutdown on `SIGTERM` (waits for the running job), child reaping when it is PID 1.
5. **Testable.** The crontab is a versioned file that `supercronic -test` validates in CI without a database.
6. **No new infrastructure.** No broker, no database tables, no Docker socket, no host configuration, no Python dependency.
7. **Portable.** It runs unchanged in development Compose, a future production Compose, or another container runtime; the deployment topology only decides *where* the container runs (§7).

It is not chosen because it is the easiest addition to the development Compose file; BusyBox `crond` would be equally easy there. It is chosen because of environment propagation, per-job exit-status logging, overlap protection and shutdown behaviour, which matter in production.

## 6. Operational behaviour (contract)

These requirements bind any implementation (Issue #289) and any future deployment topology.

### 6.1 Periodic execution

- The scheduler reads a crontab file versioned in the repository.
- The Event lifecycle entry runs `python -m app.cli.reconcile_event_lifecycle` on the five-field schedule `* * * * *` (every minute). An every-minute schedule does not depend on the scheduler's timezone; any later job with a time-of-day schedule must state its timezone explicitly.
- The command runs against the **same application release** (same commit/image) as the deployed backend, with the same database configuration, so the scheduler and the API never run different lifecycle code.
- Missed minutes (scheduler down, deployment in progress) are not replayed by the scheduler; the next run catches them up through reconciliation (ADR-0018).

### 6.2 Concurrency

- One scheduler replica per deployment (§3.3).
- Overlapping instances of the same job are disabled (supercronic's default; the `-overlapping` flag must not be used).
- If a second scheduler or a manual operator run executes concurrently, correctness is preserved by reconciliation's row locks and idempotency; this is the safety layer, not the operating mode.

### 6.3 Failure handling

- A run that exits non-zero — Events that failed to reconcile, a database outage, an unhandled exception — is logged by the scheduler with its exit status, and the next minute's run proceeds normally. That next run is the retry; the scheduler adds no in-iteration retry loop.
- In terms of ADR-0007 and NFR-REL-002: the job is idempotent and retry-safe; its retry policy is "re-evaluate on the next scheduled iteration"; no work item can be lost because the state to reconcile lives in PostgreSQL, so a dead-letter store is not applicable.
- **Bounded execution time.** Because overlap is disabled, a hung run would block later runs of the same job. The implementation must bound one run's execution time (for example by a command-level timeout in the crontab entry or database statement/lock timeouts), so that a stuck run ends as an observable failed iteration and does not silently stop the schedule. The specific bound is an implementation decision of Issue #289.
- If the scheduler process itself exits, restarting it is the responsibility of the deployment topology (restart policy). Reconciliation catches up the gap on its first run after the restart.

### 6.4 Logging and observability

- All scheduler and job output goes to the container's stdout/stderr; no syslog, mail or log files inside the container.
- For each iteration the logs show: job start, the CLI's summary line, and success or failure with the exit status. A "job took too long" warning signals a run exceeding its interval.
- Production deployments should enable supercronic's JSON log format (`-json`) so the logs can be machine-processed as `docs/08-infrastructure/observability.md` §3 requires. Full alignment with the canonical structured-log fields (service, environment, correlation/job execution id) and alerting on failed or missing runs belong to the monitoring/observability implementation and the monitoring-stack ADR (`infrastructure-and-devops.md` §24), not to this ADR.
- Logs must not contain secrets; the scheduler must not print the job environment.

### 6.5 Process lifecycle and shutdown

- supercronic is the container's main process (PID 1), so it receives stop signals directly and reaps child processes.
- On `SIGTERM` it stops scheduling new runs and waits for the running job to finish. The deployment topology must allow a stop grace period long enough for one run.
- If a run is killed anyway, only the Event currently being reconciled is affected, and its uncommitted transaction is rolled back (each Event is reconciled in its own transaction); the next run converges it.
- The scheduler has no HTTP health endpoint. Its liveness is "the process is running". Detecting a scheduler that runs but whose job keeps failing or stops reporting is a monitoring concern (§6.4).

### 6.6 Testability

- The crontab must be validated in CI with `supercronic -test`, without a database.
- Reconciliation behaviour stays covered by the existing CLI/reconciliation tests; the scheduler adds no logic that needs separate business tests.
- Local development must not require the scheduler to run the application; developers can still run the CLI directly (`apps/api/README.md`).

## 7. Deployment boundary

- **This ADR does not design TourCRM's production deployment topology.** Host layout, the production image, the production Compose (or other) definition, restart policy, stop grace period, resource limits, secret delivery, log collection and monitoring remain separate decisions (`infrastructure-and-devops.md` §24 "deployment topology", "monitoring stack", "secret management mechanism").
- **The current `docker-compose.yml` and `apps/api/Dockerfile` are development-only** and remain so; their own headers already say so. Nothing in this ADR makes them a production deployment. Whether Issue #289 adds a development scheduler to them is an implementation decision of #289, which must keep them labelled development-only and must not make the scheduler a prerequisite for running the application locally.
- **A future deployment topology must instantiate the scheduler according to this ADR**: one dedicated scheduler service/container running supercronic as its main process with the repository's crontab, built from the same application release as the backend, on the private network segment (ADR-0012), with access to PostgreSQL only. The scheduler exposes no port and is not reachable from the reverse proxy.

## 8. Consequences

### Positive

- Event lifecycle automation gets a defined, repository-owned trigger with a one-minute cadence.
- The API stays stateless; scaling API replicas cannot multiply scheduled jobs.
- No new infrastructure (no Redis, broker, schema or host configuration) and no new Python dependency.
- Business logic stays in one place — the CLI and its domain service; the scheduler is configuration only.
- Failures are visible in the container logs, and a failing iteration never disables the schedule.

### Negative / limitations

- **Single point of scheduling.** While the scheduler is down, no reconciliation runs; correctness is restored by catch-up on restart, but timeliness is lost for that period. There is no automatic failover by design (single replica).
- **No high availability or leader election.** Running more than one replica is a misconfiguration; it remains safe only because of reconciliation's locking.
- **A process per run.** Each iteration starts a Python interpreter and opens a database session. This is acceptable at a one-minute cadence; a much higher frequency would need re-evaluation.
- **Third-party binary.** supercronic is an external tool; the implementation must pin its version and verify the downloaded binary's checksum.
- **Monitoring is not automatic.** Alerting on failed or missing runs requires the future monitoring stack.
- **Not a job queue.** This ADR does not provide asynchronous, event-driven or retried-with-backoff jobs (notifications, file processing). Those remain under ADR-0007 and ODR-005.

## 9. Relationship to existing decisions

- **ADR-0007:** periodic housekeeping is background processing; this ADR fixes how *periodic system jobs* are triggered without Redis, consistent with ADR-0007's rule that Redis is used only where a separate queue is justified and must not become mandatory for simple use cases.
- **ADR-0008 ODR-005:** remains open for the asynchronous queue worker framework. If a future queue framework offers its own periodic scheduling, moving periodic system jobs to it requires a new ADR superseding this one.
- **ADR-0012:** the scheduler is an internal service on the private segment; it does not change the public entry point.
- **ADR-0018:** cadence and catch-up behaviour implement ADR-0018's time-based synchronization without changing the lifecycle.
- **`infrastructure-and-devops.md` §5:** the `scheduler` service exists because periodic system jobs need it, not because a job framework requires it; §5 is updated to reference this ADR.

## 10. Future work

When a production deployment topology is defined (separate ADR), it must:

1. define the scheduler service (single replica) built from the same release artifact as the backend;
2. define its restart policy and a stop grace period covering one run;
3. order it with migrations so the scheduler never runs a release against a schema that is not yet migrated;
4. deliver database credentials through the chosen secret mechanism;
5. collect its stdout/stderr through the chosen log pipeline, with JSON logs enabled;
6. add monitoring/alerting for failed runs and for "no successful run within N minutes";
7. set resource limits.

Issue #289 implements the scheduler according to this ADR (crontab, supercronic installation with a pinned version and checksum, bounded run time, CI validation of the crontab, documentation of the actual mechanism).

Any change to the single-replica assumption, adoption of a distributed or broker-backed scheduler, or migration to an orchestrator-native scheduler requires a new ADR.
