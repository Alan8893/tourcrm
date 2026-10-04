"""Trip / TripParticipant tourism-fact foundation (Issue #245).

Persistence: app.db.trips. Authorization reuses the Event authorization
infrastructure (app.events.authorization) with the existing `trip.read`/
`trip.manage` permissions — there is no Trip-specific authorization
model.
"""
