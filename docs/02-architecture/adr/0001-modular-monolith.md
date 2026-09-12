# ADR-0001: Modular monolith for the initial architecture

- Status: Accepted
- Date: 2026-09-12

## Context

TourCRM is a new system for a single school tourism club. It includes many business domains, integrations and personal data, but the initial user base and operational scale do not justify the operational cost of multiple independent services.

## Decision

Build the core application as a **modular monolith** with explicit business module boundaries.

The application will run as a small number of containers, primarily the web frontend, API, database and optional infrastructure services.

Internal modules must avoid accidental coupling. Shared code belongs in explicitly named shared packages or infrastructure layers rather than ad-hoc imports between business domains.

## Consequences

### Positive

- simpler development and deployment;
- easier local development;
- straightforward transactional operations across related domains;
- lower infrastructure overhead;
- easier testing in the initial phase;
- future extraction remains possible when a concrete need appears.

### Negative

- module boundaries require discipline;
- a deployment contains the whole backend;
- high-scale components cannot be independently scaled until extracted.

## Revisit when

Reconsider this decision if a domain has materially different scaling, security, deployment or operational requirements, especially TourSlet integration or notification processing.
