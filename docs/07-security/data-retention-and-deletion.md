# TourCRM — Data Retention and Deletion Policy Contract

## 1. Purpose

This document defines the technical framework for retention, archival, deletion and access restriction of personal and operational data.

It is a technical policy contract, not a legal opinion. Concrete retention periods that depend on applicable law, club policy, educational requirements, accounting rules or contractual obligations must be approved by the project owner and/or competent legal adviser before production use.

## 2. Principles

1. Collect only data required for a defined purpose.
2. Every sensitive data category must have an identified owner/purpose.
3. Operational deletion must not silently destroy legally or historically required records.
4. Historical records should be archived or anonymized where deletion is not appropriate.
5. File retention and database retention are separate but linked processes.
6. Backups follow their own retention policy and must not become an indefinite hidden copy of deleted production data.
7. Access to archived data is more restricted than access to active operational data unless a documented rule requires otherwise.

## 3. Data classes

### Class A — Account and security data

Examples:

- authentication identifiers;
- password hashes;
- sessions/revocation data;
- security events.

Retention is driven by security and account-lifecycle requirements. Password hashes must be removed when the account is permanently deleted according to the approved deletion policy; security audit retention may be longer where required.

### Class B — Club membership and historical activity

Examples:

- membership history;
- group membership history;
- event participation;
- attendance;
- trip participation;
- achievements and qualifications.

These are historically significant and should normally be archived rather than physically deleted unless a legally valid deletion requirement applies.

### Class C — Sensitive personal data

Examples may include:

- medical restrictions;
- emergency contacts;
- identity/document information;
- consent records.

Access must be explicitly permission-controlled. Retention periods must be defined per data type before production deployment.

### Class D — Files

Examples:

- scanned documents;
- signed consents;
- GPX files;
- participant photos;
- generated reports.

Each file must have an owner, purpose, lifecycle state and storage reference. Expired files are not automatically deleted unless the applicable policy explicitly requires deletion.

### Class E — Financial records

Retention must follow the applicable accounting/legal policy and must not be inferred from general application retention settings.

### Class F — Analytics and logs

Operational logs should have the shortest practical retention compatible with troubleshooting and audit requirements. Logs must avoid secrets and unnecessary personal data.

## 4. Lifecycle states

Where applicable, records progress through:

`active -> inactive -> archived -> deleted/anonymized`

Not every entity supports every transition.

## 5. Deletion semantics

### User/account deletion

Deleting an account does not automatically delete the Person record if the person is referenced by historical club activity, finance, trips or documents.

The system must distinguish:

- account deactivation;
- membership termination;
- anonymization;
- full deletion.

### Person deletion

A Person may only be physically deleted when referential integrity, audit requirements, historical requirements and approved retention policy permit it.

Where deletion is not permitted, identifying information may be anonymized to the minimum extent allowed while retaining required non-identifying history.

### File deletion

Deleting a file record must not leave inaccessible orphaned binary objects. A controlled asynchronous cleanup process should detect and remove orphan objects after the policy-defined safety interval.

## 6. Backups

Backups are not an authorization bypass. Production deletion requests must be processed according to the backup-retention policy and documented purge window.

Restore procedures must define how data deleted in production is treated after a historical backup restore.

## 7. Legal/organizational approval points

The following values are deliberately not hard-coded by this document:

- exact retention periods for personal data;
- exact retention periods for medical information;
- exact retention periods for consent records;
- exact financial retention periods;
- exact audit/security-log retention;
- exact backup retention;
- legal basis and consent wording.

These values must be captured as approved policy configuration before production release.

## 8. Technical acceptance criteria

- Every persisted personal-data class has documented purpose and owner.
- Deactivation is distinguishable from deletion.
- Historical records are not accidentally cascade-deleted by routine account/member operations.
- Sensitive files cannot be downloaded without authorization.
- Orphan-file detection exists as an operational requirement.
- Backup/restore documentation explains retention implications.
- Automated tests cover the most important deletion and authorization invariants.
