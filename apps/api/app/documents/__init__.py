"""Participant Document domain (TH-0117.3 / Issue #160; ADR-0040).

Application/service layer for the `Document`/`File` persistence
(`app.db.documents`, TH-0117.1) and the `FileStorage` port
(`app.storage`, TH-0117.2). Authorization is not decided here — the
API layer (`app.api.v1.persons`) resolves and checks it before calling
into this package, exactly as `app.people.service`/`app.groups.service`
already keep authorization as the router's job.
"""
