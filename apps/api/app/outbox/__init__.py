"""Generic PostgreSQL-backed transactional outbox (Issue #318, ADR-0046).

One shared job table for every asynchronous job type — never a
per-provider outbox. See app.outbox.vocabulary for the job status
vocabulary, app.outbox.service for the in-transaction enqueue boundary,
app.outbox.claiming for `FOR UPDATE SKIP LOCKED` claiming, leases and fenced
completion, and app.outbox.worker for the worker runtime (#325) run by
app.cli.run_outbox_worker.
"""
