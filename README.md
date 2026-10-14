# Harbor Health patient portal

A small patient/provider appointment portal built with a React frontend and a
Python FastAPI backend. The demo uses SQLite, seeded appointment data, and
identity switching for local testing only; it is not an authentication system.

## Overview

- Patients can browse future appointments, request a visit, and cancel
  confirmed visits.
- Providers can view appointment schedules, confirm pending requests, and
  reschedule active visits.
- Each appointment keeps a version number for optimistic concurrency control.
- Every action creates an event snapshot showing the before and after record in
  the appointment history.
- Confirmations generate notification records that are processed by a background
  outbox worker.
- SQLite database rules enforce provider overlap protection and serialise writes
  with immediate transactions.

See [PROJECT_WORKFLOW.md](./PROJECT_WORKFLOW.md) for the end-to-end patient and
provider workflows, API responsibilities, and a wireframe-style application
diagram.

See [USER_PERSPECTIVE.md](./USER_PERSPECTIVE.md) for a screenshot-driven guide
to patient/provider workflows, including requests, confirmations,
rescheduling, history, cancellation, empty state, overlap rejection, and stale
updates.

## Local setup

From the project root, create the Python environment and start the API:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn backend.main:app --reload
```

In a second terminal, start the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173> in the browser. The API is available at
<http://localhost:8000/docs>.

The first backend startup creates the SQLite database at `data/appointments.db`
and seeds demo patients, providers, and sample appointments. Existing databases
are migrated without dropping prior appointments, and missing demo records are
added for newer identities. To point the app at a different database, set
`DATABASE_PATH` before starting the API.

## Demo identities

The local demo includes:

- Patients: Alex Morgan and Jamie Lee
- Providers: Dr. Riley Chen and Dr. Samira Patel

Use the Preview as selector in the UI to switch among the four demo identities.
API requests are scoped to the selected demo user, but that is only for local
illustration; it is not production authorization.

## Appointment behavior

- Requests start in a `pending` state and must use a timezone-aware future time.
- Providers can confirm pending requests and reschedule active appointments.
- Appointment updates carry a version value; stale edits return `409 Conflict`
  and the UI reloads the latest record with an explanatory message.
- Invalid transitions, unknown users, invalid roles, and out-of-scope access are
  rejected by the API.
- Every appointment action writes a history record with the action name, actor,
  timestamp, and a before/after snapshot.
- Notification confirmations insert a row into the persistent outbox. The worker
  claims pending messages in a short transaction, logs delivery outside the
  write lock, and marks the message as delivered in a second short transaction.
  Failed log attempts release the claim for retry; abandoned claims expire after
  five minutes.
- The patient list only shows future non-cancelled appointments, while provider
  views refresh automatically on a five-second timer.
- if there is overlap of time and user try to confirm the schedule appointment it will not possible with 30 min buffer time for each appointment.
- SQLite `BEGIN IMMEDIATE` serializes writes, and database triggers reject
  overlapping confirmed appointments for the same provider. Back-to-back
  appointments remain valid. The database guard is the final authority.

## Testing and build verification

Run the backend test suite and coverage check:

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m coverage run --branch -m unittest discover -s tests -v
.venv/bin/python -m coverage report -m --include='backend/*.py'
```

Run the frontend test suite and coverage report for the React app:

```bash
cd frontend
npm install
npm test
npm run test:coverage
```

The frontend uses Vitest and React Testing Library to exercise the main patient
and provider flows, including appointment requests, confirmation and
rescheduling, stale-version conflict handling, history view errors, polling
refreshes, and empty/loading states.

Build the frontend bundle:

```bash
cd frontend
npm run build
```

## Project status

Current verification status:

- Backend test suite: passing
- Frontend test suite: passing
- Backend coverage: tracked with `coverage` for the Python API
- Frontend coverage: Vitest coverage enabled for the React app, with the main
  patient/provider flows covered and the source-level report checked in CI/local
  runs

## Notes

This project is intended as a local demo and frontend prototype. For real-world
production use, add proper identity and authorization controls, a real delivery
provider with idempotency safeguards, and operational monitoring.

See [TRANSCRIPT.md](./TRANSCRIPT.md) for the AI-assisted implementation session
transcript.
