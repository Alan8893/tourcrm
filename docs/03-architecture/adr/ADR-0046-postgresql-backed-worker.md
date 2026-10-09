# ADR-0046 — PostgreSQL-backed Worker for Asynchronous Jobs

- **Status:** Accepted
- **Date:** 2026-10-08
- **Decision owner:** Product Owner / CTO
- **Decision type:** Architecture decision
- **Closes:** ODR-005
- **Refines:** ADR-0007 (background processing), ADR-0045 (Notification Center)
- **Related:** ADR-0044, `docs/08-infrastructure/infrastructure-and-devops.md`

## 1. Context

TourCRM needs asynchronous processing for Notification Center delivery and future background jobs.

The existing application stack already uses PostgreSQL as the transactional source of truth and SQLAlchemy as the persistence layer. The Notification Center uses a PostgreSQL transactional outbox.

The repository currently has no Celery, RQ, ARQ, Dramatiq or equivalent worker framework dependency. Introducing a broker-backed framework would add another operational dependency before the product has a demonstrated need for one.

ADR-0007 requires the concrete worker technology to be decided before implementation. ADR-0045 deliberately leaves that decision open.

## 2. Decision

For the current TourCRM architecture, asynchronous background jobs are processed by a **dedicated TourCRM Python worker process using PostgreSQL as the durable job queue**.

No external queue/broker framework is introduced for the Notification Center MVP.

The worker:

- runs as a separate service/process from FastAPI;
- uses the existing SQLAlchemy/PostgreSQL stack;
- consumes durable outbox/job rows from PostgreSQL;
- claims work using PostgreSQL row locking with `FOR UPDATE SKIP LOCKED`;
- uses a lease/visibility timeout so a worker crash does not permanently own a job;
- records attempts and terminal errors in PostgreSQL;
- is safe to run with multiple worker processes when the job contract allows concurrent processing;
- contains orchestration only; business rules remain in application/domain services.

PostgreSQL `SKIP LOCKED` is explicitly intended for queue-like tables with multiple consumers, and SQLAlchemy supports `with_for_update(skip_locked=True)`. citeturn1search0turn1search2

## 3. Why this option

### 3.1 Existing architecture

PostgreSQL is already mandatory and is the source of truth for transactional business data.

The Notification Center already requires durable Notification, Delivery and Outbox state.

Using the same datastore means the business mutation and enqueue operation can remain one transaction without a second broker participating in correctness.

### 3.2 Operational simplicity

The worker requires:

- the existing application image;
- PostgreSQL access;
- one additional process/service.

It does not require Redis, RabbitMQ, a broker cluster, broker persistence configuration or a separate result backend.

The current production infrastructure specification already contains a dedicated `worker` service concept.

### 3.3 Correctness model

The outbox is the durable source of pending work.

Worker claims are transient coordination state. If a worker dies, its lease expires and another worker can claim the job.

Delivery is **at-least-once** at the worker level. Exactly-once external delivery is not assumed.

Notification/Delivery idempotency is therefore mandatory at the application boundary.

## 4. Alternatives considered

### Celery + Redis

Rejected for the MVP.

Celery is mature and supports Redis and other brokers, but adopting it would introduce a broker as a new production dependency. Current Celery documentation also describes broker and result-backend configuration as separate infrastructure concerns. citeturn0search1turn0search3

TourCRM already has durable PostgreSQL outbox state, so duplicating queue state in Redis would increase operational complexity without solving a demonstrated product requirement.

### Celery + PostgreSQL/PGMQ

Not selected for the MVP.

Current Celery supports PostgreSQL PGMQ as a broker, but that introduces Celery/Kombu/PGMQ-specific infrastructure and a second queue abstraction on top of the application's own outbox model. citeturn0search0

The current requirement does not justify replacing the canonical outbox with a framework-specific broker.

### ARQ

Rejected for the MVP because it is Redis-based and would make Redis a mandatory dependency for the worker.

### RQ

Rejected for the MVP for the same reason: Redis becomes part of the mandatory queue path.

### Dramatiq

Rejected for the MVP because its normal broker model adds another messaging infrastructure component that the PostgreSQL outbox already makes unnecessary at current scale.

### Custom PostgreSQL worker

**Selected.**

It is deliberately small and purpose-built around the canonical TourCRM outbox rather than introducing a general distributed task framework.

## 5. Worker contract

### 5.1 Process boundary

The worker is a separate process/service.

It must not run inside the FastAPI application process or depend on API request lifecycle.

### 5.2 Claiming work

A worker transaction selects eligible pending jobs in deterministic order and claims a bounded batch with:

```sql
FOR UPDATE SKIP LOCKED
```

The claim must be atomic with the lease update.

The exact SQLAlchemy implementation is fixed by the implementation issue; the concurrency invariant is normative.

### 5.3 Lease

A claimed job receives a finite `locked_until`/lease expiration.

If the worker terminates before completing the job, the expired lease makes the job eligible again.

A job must never remain permanently invisible because a worker died.

### 5.4 Attempts and retry

Every attempt increments a persisted attempt counter.

Retryable failures receive a future `next_attempt_at`.

Permanent failures become a terminal error/dead state and remain observable.

A handler may **defer** a job it finds temporarily not runnable — for a notification delivery, a channel paused by the Global Admin Policy (ADR-0048 §2.8). A deferral is not an attempt: the job returns to `pending` with a future `next_attempt_at` (the re-check interval, so it is not re-claimed in a busy loop), its lease is cleared and the claim's attempt is not counted, exactly like a job handed back at shutdown. Deferral is fenced by the lease like any other result.

The exact backoff schedule and maximum attempt count are implementation configuration and must be documented before production.

### 5.5 Idempotency

The worker must tolerate redelivery.

A delivery identity must be stable for the logical Notification/Delivery pair.

A provider call may occur more than once after an uncertain network failure; the application must not assume that a timeout means the provider did not accept the message.

### 5.6 Shutdown

The worker must stop claiming new jobs after shutdown is requested and finish or safely release the currently claimed work according to its lease semantics.

A forced termination must leave work recoverable by lease expiry.

### 5.7 Observability

The worker logs:

- job type;
- job identifier;
- attempt number;
- result/status;
- duration;
- safe error code.

It must never log secrets, message bodies containing credentials, SMTP passwords or Telegram bot tokens.

## 6. Scope

This ADR establishes the worker mechanism for:

- Notification outbox delivery;
- future asynchronous jobs that fit the same PostgreSQL-backed job contract.

It does not require every future background workload to use PostgreSQL. A future workload may justify another queue, but that requires a new architecture decision.

ADR-0044 remains authoritative for periodic system jobs and supercronic. The worker does not replace the scheduler.

## 7. Consequences

### Positive

- no new mandatory broker for Notification Center;
- transactional PostgreSQL outbox remains the single durable source of pending work;
- existing SQLAlchemy/PostgreSQL stack is reused;
- simple local development;
- dedicated worker can scale independently from FastAPI;
- crash recovery is explicit through leases;
- multiple workers can coordinate through row locking.

### Negative

- custom worker code must be maintained and tested;
- PostgreSQL carries both transactional workload and queue polling;
- very high job throughput may eventually justify a dedicated broker;
- exactly-once external delivery cannot be guaranteed;
- queue monitoring must be implemented at the application level.

## 8. Exit criteria for reconsideration

Re-evaluate this decision if measured production requirements demonstrate one or more of:

- PostgreSQL queue contention materially affects transactional workload;
- sustained job throughput exceeds what the database-backed worker can safely process;
- cross-service asynchronous messaging becomes a first-class requirement;
- delayed/priority/routing semantics become substantially more complex;
- operational requirements justify a dedicated broker.

Such a change requires a new ADR; it must not be introduced opportunistically into a feature implementation.

## 9. Implementation order

1. Implement the Notification persistence/outbox contract (#318).
2. Implement the worker process against that contract.
3. Add worker service to the production topology.
4. Add worker health/observability checks.
5. Implement Email and Telegram adapters behind the Notification Delivery boundary.

