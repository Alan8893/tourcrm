"""Generic PostgreSQL-backed transactional outbox (Issue #318, ADR-0046).

One shared job table for every asynchronous job type — never a
per-provider outbox. See app.outbox.vocabulary for the job status
vocabulary and app.outbox.service for the in-transaction enqueue
boundary. The worker that claims jobs (`FOR UPDATE SKIP LOCKED`, lease
expiry) is a separate Issue.
"""
