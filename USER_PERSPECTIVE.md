# Harbor Health: user workflow in live screenshots

This guide follows the main patient and provider journeys using screenshots
captured from the running React application connected to its FastAPI backend.
The app was operated in Chrome; the screenshots show actual rendered screens
and responses, not mockups.

The walkthrough uses the seeded demo identities **Alex Morgan** (patient),
**Jamie Lee** (patient), and **Dr. Samira Patel** (provider). Appointment dates
are examples from the capture session and will change as time passes or the
database is reseeded.

## At a glance

1. A patient reviews upcoming appointments and enters a visit request.
2. The provider reviews the pending request, may reschedule it, then confirms
   it.
3. The patient sees the confirmed visit and can inspect its history or cancel.
4. Additional screens show an empty schedule, the provider's cancelled record,
   overlapping-slot rejection, and recovery from a stale edit.

## Patient journey

### 1. Review the patient dashboard

The dashboard summarizes total, confirmed, and awaiting appointments. Visit
cards show the date, type, status, time, provider, and reason. Patients can open
history for a visit or cancel one that is confirmed.

![Patient appointment dashboard with seeded visits](./docs/user-journey/01-patient-dashboard.png)

### 2. Prepare and send a visit request

The patient picks a future date and time, chooses a provider, and enters a short
reason. This screenshot shows the form filled in before it is sent.

![Patient request form filled and ready to submit](./docs/user-journey/02-patient-request-form.png)

Selecting **Send request** creates a pending appointment. The selected
provider's schedule then shows the new request alongside existing visits.

## Provider journey

### 3. Review a pending request

The provider switches to their account using **Preview as**. Their care schedule
shows assigned visits and summary counts. A pending request has a **Confirm**
action; active visits can be rescheduled.

![Provider schedule showing a patient request awaiting review](./docs/user-journey/03-provider-pending-request.png)

### 4. Reschedule a visit

Selecting **Reschedule** opens a dialog where the provider chooses a future
date and time, saves the change, or keeps the existing time.

![Provider reschedule dialog](./docs/user-journey/04-provider-reschedule-dialog.png)

After saving, the schedule displays a success notice. The change is also
recorded in the appointment history.

![Provider schedule after successfully rescheduling a visit](./docs/user-journey/05-provider-rescheduled.png)

### 5. Confirm the request

Selecting **Confirm** changes the pending appointment to confirmed. The
provider sees that a patient notification has been queued. In this demo the
background worker logs notification delivery; it does not send a real email or
text message.

![Provider schedule after confirming a patient request](./docs/user-journey/06-provider-confirmation.png)

## Patient follows up

### 6. See the confirmed visit

After returning to the patient dashboard, the patient sees the visit with a
confirmed status and the provider details. The patient can now cancel it if
needed.

![Patient dashboard showing the confirmed visit](./docs/user-journey/07-patient-confirmed-visit.png)

### 7. Inspect the appointment history

The **History** control expands an audit timeline with the action, actor, and
time recorded for each change. This example shows the original request and
provider confirmation.

![Appointment history showing request and confirmation](./docs/user-journey/08-appointment-history.png)

### 8. Cancel a confirmed visit

When the patient cancels a confirmed visit, the page displays a confirmation
notice and removes the cancelled visit from the patient's upcoming list.

![Patient dashboard after cancelling a confirmed visit](./docs/user-journey/09-patient-cancelled-visit.png)

The provider can still see the cancelled record in their schedule. It is
retained for history and audit; it is not deleted.

![Provider schedule retaining a cancelled appointment record](./docs/user-journey/11-provider-cancelled-visit.png)

## Other user-visible states

### No upcoming visits

When the selected patient has no appointments, the list shows an empty state
and the visit request form remains available.

![Patient dashboard with no scheduled visits](./docs/user-journey/10-patient-empty-state.png)

### Provider cannot confirm an overlapping slot

The API and SQLite guard prevent two confirmed appointments for the same
provider from overlapping. The first screenshot shows a pending request at an
already-booked time; the next shows the conflict notice after the provider
tries to confirm it. The request stays pending.

![Provider schedule before confirming a request at an overlapping time](./docs/user-journey/12-provider-overlap-request.png)

![Overlapping appointment confirmation rejected with a conflict notice](./docs/user-journey/13-provider-overlap-conflict.png)

### Appointment changed while the provider was viewing it

Appointment updates carry a version. If another update happens after the page
was loaded, the stale action is rejected. The UI explains that the latest
details have been reloaded so the provider can review them before trying again.

![Provider sees the stale-edit warning and refreshed appointment list](./docs/user-journey/14-provider-stale-edit-reloaded.png)

## What happens behind the screen

- The React app loads demo identities, then requests appointments scoped to the
  selected patient or provider. It refreshes the schedule every five seconds.
- A request is validated and saved as `pending`; its initial history event is
  recorded with it.
- Confirm, cancel, and reschedule actions go to the API with the version shown
  on screen. Valid changes increment that version and append an audit event.
- Confirmation adds a notification to a persistent outbox. A background worker
  claims and processes outbox entries.
- The database transaction and overlap triggers are the final guard against
  conflicting confirmed appointments. Back-to-back appointments are allowed;
  there is no additional 30-minute buffer.
- Invalid dates, incomplete or too-short reasons, unknown demo identities,
  unauthorized actions, and invalid status transitions are rejected by browser
  validation or the API rather than silently applied.

## Screenshot and data notes

These screenshots cover the main user-facing screens and important success,
empty, and conflict states. They are not screenshots of a publicly hosted
production deployment. The walkthrough ran locally against a separate,
temporary SQLite database; it did not change the project's existing
`data/appointments.db`.

The **Preview as** selector is for demonstrating patient/provider views only.
It is not authentication or production authorization. Do not use this demo for
real patient information or clinical operations.
