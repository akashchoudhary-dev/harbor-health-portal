import asyncio
import tempfile
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from backend.database import Database, configured_database, future_iso
from backend.main import create_app, notification_worker


class AppointmentApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "appointments.db"
        self.client = TestClient(create_app(self.database_path))

    def tearDown(self):
        self.client.close()
        self.tempdir.cleanup()

    @staticmethod
    def future_time(days: int = 10, hour: int = 10) -> str:
        value = (datetime.now(timezone.utc) + timedelta(days=days)).replace(
            hour=hour, minute=0, second=0, microsecond=0
        )
        return value.isoformat()

    def request_appointment(self, when: str, **identity) -> dict:
        response = self.client.post(
            "/api/appointments",
            json={"start_at": when, "reason": "Discuss a follow-up", **identity},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def update(self, appointment: dict, role: str, action: str, **extra):
        return self.client.patch(
            f"/api/appointments/{appointment['id']}",
            json={
                "role": role,
                "action": action,
                "expected_version": appointment["version"],
                **extra,
            },
        )

    def test_database_rejects_unknown_action_and_missing_reschedule_time(self):
        database = self.client.app.state.database
        appointment = self.request_appointment(self.future_time(days=17, hour=13))
        with self.assertRaisesRegex(ValueError, "Unknown appointment action"):
            database.update_appointment(
                appointment["id"],
                "provider",
                "delete",
                appointment["version"],
            )
        with self.assertRaisesRegex(ValueError, "new appointment time is required"):
            database.update_appointment(
                appointment["id"],
                "provider",
                "reschedule",
                appointment["version"],
            )

    def test_requests_are_persistent_pending_and_audited(self):
        appointment = self.request_appointment(self.future_time())
        self.assertEqual(appointment["status"], "pending")
        self.assertIn(
            appointment["id"],
            [item["id"] for item in self.client.get("/api/appointments?role=patient").json()],
        )
        history = self.client.get(
            f"/api/appointments/{appointment['id']}/history?role=patient"
        ).json()
        self.assertEqual(history[0]["action"], "requested")
        self.assertIsNone(history[0]["before"])

    def test_health_demo_users_and_request_validation(self):
        with TestClient(create_app(self.database_path)) as client:
            self.assertEqual(client.get("/api/health").json(), {"status": "ok"})
            users = client.get("/api/demo-users").json()
            self.assertEqual(len(users["patient"]), 2)
            self.assertEqual(len(users["provider"]), 2)

            self.assertEqual(
                client.get("/api/appointments?role=unknown").status_code, 422
            )
            self.assertEqual(
                client.get(
                    "/api/appointments?role=provider&user_id=patient-alex"
                ).status_code,
                404,
            )
            self.assertEqual(
                client.get(
                    "/api/appointments?role=patient&user_id=does-not-exist"
                ).status_code,
                404,
            )
            self.assertEqual(
                client.post(
                    "/api/appointments",
                    json={
                        "start_at": self.future_time(days=11),
                        "reason": "   ",
                    },
                ).status_code,
                422,
            )
            self.assertEqual(
                client.post(
                    "/api/appointments",
                    json={
                        "start_at": self.future_time(days=11),
                        "reason": "Valid reason",
                        "patient_id": "provider-riley",
                    },
                ).status_code,
                404,
            )
            self.assertEqual(
                client.post(
                    "/api/appointments",
                    json={
                        "start_at": self.future_time(days=11),
                        "reason": "Valid reason",
                        "provider_id": "missing-provider",
                    },
                ).status_code,
                404,
            )

    def test_api_rejects_invalid_update_shapes_and_unknown_appointments(self):
        appointment = self.request_appointment(self.future_time(days=13))
        url = f"/api/appointments/{appointment['id']}"
        payload = {
            "role": "provider",
            "action": "confirm",
            "expected_version": appointment["version"],
        }
        invalid_payloads = (
            {**payload, "role": "administrator"},
            {**payload, "action": "delete"},
            {**payload, "expected_version": 0},
        )
        for invalid in invalid_payloads:
            with self.subTest(payload=invalid):
                self.assertEqual(
                    self.client.patch(url, json=invalid).status_code, 422
                )

        self.assertEqual(
            self.client.patch(
                url,
                json={
                    "role": "provider",
                    "action": "reschedule",
                    "expected_version": appointment["version"],
                },
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.patch(
                url,
                json={
                    "role": "provider",
                    "action": "reschedule",
                    "expected_version": appointment["version"],
                    "start_at": None,
                },
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.patch(
                "/api/appointments/missing-id",
                json=payload,
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                f"/api/appointments/{appointment['id']}/history?role=other"
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.get(
                "/api/appointments/missing-id/history?role=provider"
            ).status_code,
            404,
        )

    def test_times_are_normalized_to_utc_and_second_precision(self):
        local_time = datetime.now(timezone.utc) + timedelta(days=14)
        local_time = local_time.replace(hour=11, minute=15, second=27, microsecond=456)
        requested = local_time.replace(tzinfo=timezone(timedelta(hours=2)))
        appointment = self.request_appointment(requested.isoformat())
        expected = requested.astimezone(timezone.utc).replace(microsecond=0)
        self.assertEqual(
            datetime.fromisoformat(appointment["start_at"]),
            expected,
        )

    def test_database_rejects_naive_and_past_times_independently_of_api(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            future_iso(self.future_time(days=15).replace("+00:00", ""))
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "future"):
            self.client.app.state.database.create_appointment(
                past,
                "Direct database request",
            )

    def test_database_configuration_uses_environment_path(self):
        configured_path = Path(self.tempdir.name) / "configured.db"
        with patch.dict("os.environ", {"DATABASE_PATH": str(configured_path)}):
            database = configured_database()
        self.assertTrue(configured_path.exists())
        self.assertEqual(len(database.list_appointments("patient")), 3)

    def test_in_memory_database_uses_shared_connection_state(self):
        database = Database(":memory:")
        appointment = database.create_appointment(
            self.future_time(days=17),
            "In memory visit",
        )
        self.assertIn(
            appointment["id"],
            [item["id"] for item in database.list_appointments("patient")],
        )

    def test_request_handler_maps_database_validation_error_to_422(self):
        with patch.object(
            self.client.app.state.database,
            "create_appointment",
            side_effect=ValueError("late validation failed"),
        ):
            response = self.client.post(
                "/api/appointments",
                json={
                    "start_at": self.future_time(days=16),
                    "reason": "Valid reason",
                },
            )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "late validation failed")

    def test_stale_mutation_is_rejected_and_preserves_history(self):
        appointment = self.request_appointment(self.future_time())
        confirmed = self.update(appointment, "provider", "confirm").json()
        stale_cancel = self.update(appointment, "patient", "cancel")
        self.assertEqual(stale_cancel.status_code, 409)
        self.assertEqual(stale_cancel.json()["detail"]["code"], "stale_appointment")
        self.assertEqual(stale_cancel.json()["detail"]["current"]["status"], "confirmed")

        rescheduled = self.update(
            confirmed,
            "provider",
            "reschedule",
            start_at=self.future_time(days=12, hour=12),
        ).json()
        cancelled = self.update(rescheduled, "patient", "cancel")
        self.assertEqual(cancelled.status_code, 200)
        history = self.client.get(
            f"/api/appointments/{appointment['id']}/history?role=patient"
        ).json()
        self.assertEqual(
            [event["action"] for event in history],
            ["requested", "confirmed", "rescheduled", "cancelled"],
        )
        self.assertNotEqual(history[2]["before"]["start_at"], history[2]["after"]["start_at"])

    def test_database_prevents_overlapping_confirmations(self):
        first = self.request_appointment(self.future_time(days=15, hour=10))
        second = self.request_appointment(self.future_time(days=15, hour=10))
        result = self.update(first, "provider", "confirm")
        self.assertEqual(result.status_code, 200, result.text)
        rejected = self.update(second, "provider", "confirm")
        self.assertEqual(rejected.status_code, 409)
        self.assertIn("overlaps", rejected.json()["detail"])

    def test_demo_patients_and_providers_have_separate_scoped_schedules(self):
        identities = self.client.get("/api/demo-users").json()
        self.assertEqual(len(identities["patient"]), 2)
        self.assertEqual(len(identities["provider"]), 2)

        jamie_visit = self.request_appointment(
            self.future_time(days=25),
            patient_id="patient-jamie",
            provider_id="provider-samira",
        )
        self.assertEqual(jamie_visit["patient_name"], "Jamie Lee")
        self.assertEqual(jamie_visit["provider_name"], "Dr. Samira Patel")

        alex_visits = self.client.get(
            "/api/appointments?role=patient&user_id=patient-alex"
        ).json()
        jamie_visits = self.client.get(
            "/api/appointments?role=patient&user_id=patient-jamie"
        ).json()
        riley_visits = self.client.get(
            "/api/appointments?role=provider&user_id=provider-riley"
        ).json()
        samira_visits = self.client.get(
            "/api/appointments?role=provider&user_id=provider-samira"
        ).json()
        self.assertNotIn(jamie_visit["id"], [row["id"] for row in alex_visits])
        self.assertIn(jamie_visit["id"], [row["id"] for row in jamie_visits])
        self.assertNotIn(jamie_visit["id"], [row["id"] for row in riley_visits])
        self.assertIn(jamie_visit["id"], [row["id"] for row in samira_visits])

        self.assertEqual(
            self.update(
                jamie_visit,
                "provider",
                "confirm",
                user_id="provider-riley",
            ).status_code,
            404,
        )
        confirmed = self.update(
            jamie_visit,
            "provider",
            "confirm",
            user_id="provider-samira",
        ).json()
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(
            self.client.get(
                f"/api/appointments/{jamie_visit['id']}/history?role=patient&user_id=patient-alex"
            ).status_code,
            404,
        )
        history = self.client.get(
            f"/api/appointments/{jamie_visit['id']}/history?role=provider&user_id=provider-samira"
        ).json()
        self.assertEqual(history[-1]["actor_name"], "Dr. Samira Patel")

    def test_existing_single_patient_database_migrates_and_keeps_appointments(self):
        legacy_path = Path(self.tempdir.name) / "legacy.db"
        start = self.future_time(days=24)
        with sqlite3.connect(legacy_path) as connection:
            connection.executescript(
                """
                CREATE TABLE appointments (
                    id TEXT PRIMARY KEY, patient_name TEXT NOT NULL,
                    patient_email TEXT NOT NULL, provider_name TEXT NOT NULL,
                    appointment_type TEXT NOT NULL, reason TEXT NOT NULL,
                    start_at TEXT NOT NULL, duration_minutes INTEGER NOT NULL,
                    status TEXT NOT NULL, version INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE appointment_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    appointment_id TEXT NOT NULL REFERENCES appointments(id),
                    action TEXT NOT NULL, actor_role TEXT NOT NULL,
                    actor_name TEXT NOT NULL, occurred_at TEXT NOT NULL,
                    before_json TEXT, after_json TEXT NOT NULL
                );
                CREATE TABLE notification_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    appointment_id TEXT NOT NULL REFERENCES appointments(id),
                    recipient TEXT NOT NULL, message TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL,
                    delivered_at TEXT
                );
                """
            )
            connection.execute(
                """
                INSERT INTO appointments VALUES (
                    'legacy-appointment', 'Alex Morgan', 'alex.morgan@example.com',
                    'Dr. Riley Chen', 'Legacy visit', 'Keep existing data',
                    ?, 30, 'confirmed', 1, ?, ?
                )
                """,
                (start, start, start),
            )

        migrated = Database(legacy_path)
        legacy_appointment = next(
            item
            for item in migrated.list_appointments("patient", "patient-alex")
            if item["id"] == "legacy-appointment"
        )
        self.assertEqual(legacy_appointment["patient_id"], "patient-alex")
        self.assertEqual(legacy_appointment["provider_id"], "provider-riley")
        self.assertTrue(
            migrated.list_appointments("patient", "patient-jamie"),
            "New demo identity should receive seed appointments after migration.",
        )
        migrated_again = Database(legacy_path)
        self.assertEqual(
            len(migrated.list_appointments("patient", "patient-jamie")),
            len(migrated_again.list_appointments("patient", "patient-jamie")),
        )

    def test_overlapping_confirmations_for_different_providers_are_allowed(self):
        appointment_riley = self.request_appointment(
            self.future_time(days=26),
            patient_id="patient-alex",
            provider_id="provider-riley",
        )
        appointment_samira = self.request_appointment(
            self.future_time(days=26),
            patient_id="patient-jamie",
            provider_id="provider-samira",
        )
        self.assertEqual(
            self.update(appointment_riley, "provider", "confirm").status_code,
            200,
        )
        result = self.update(
            appointment_samira,
            "provider",
            "confirm",
            user_id="provider-samira",
        )
        self.assertEqual(result.status_code, 200, result.text)

    def test_database_insert_trigger_rejects_direct_overlapping_confirmation(self):
        database = self.client.app.state.database
        existing = next(
            appointment
            for appointment in database.list_appointments(
                "provider", "provider-riley"
            )
            if appointment["status"] == "confirmed"
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "confirmed appointment overlap"):
            with database.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO appointments (
                        id, patient_id, patient_name, patient_email, provider_id,
                        provider_name, appointment_type, reason, start_at,
                        duration_minutes, status, version, created_at, updated_at
                    ) VALUES (
                        'direct-overlap', 'patient-jamie', 'Jamie Lee',
                        'jamie.lee@example.com', ?, ?, 'Direct insert',
                        'Exercise database trigger', ?, 30, 'confirmed', 1, ?, ?
                    )
                    """,
                    (
                        existing["provider_id"],
                        existing["provider_name"],
                        existing["start_at"],
                        existing["created_at"],
                        existing["updated_at"],
                    ),
                )

    def test_non_overlap_database_constraint_error_is_not_misreported(self):
        database = self.client.app.state.database
        appointment = self.request_appointment(self.future_time(days=28))
        with database.transaction() as connection:
            connection.execute(
                f"""
                CREATE TRIGGER simulated_failure
                BEFORE UPDATE OF status ON appointments
                WHEN NEW.id = '{appointment['id']}'
                BEGIN
                    SELECT RAISE(ABORT, 'simulated unrelated constraint failure');
                END
                """
            )

        with self.assertRaisesRegex(
            sqlite3.IntegrityError, "simulated unrelated constraint failure"
        ):
            database.update_appointment(
                appointment["id"],
                "provider",
                "confirm",
                appointment["version"],
            )
        current = next(
            row
            for row in database.list_appointments("patient")
            if row["id"] == appointment["id"]
        )
        self.assertEqual(current["status"], "pending")

    def test_audit_write_failure_rolls_back_appointment_and_notification(self):
        database = self.client.app.state.database
        appointment = self.request_appointment(self.future_time(days=28, hour=14))
        with patch.object(database, "_event", side_effect=sqlite3.IntegrityError("audit failed")):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "audit failed"):
                database.update_appointment(
                    appointment["id"],
                    "provider",
                    "confirm",
                    appointment["version"],
                )
        current = next(
            row
            for row in database.list_appointments("patient")
            if row["id"] == appointment["id"]
        )
        self.assertEqual(current["status"], "pending")
        with database.read_connection() as connection:
            queued = connection.execute(
                "SELECT COUNT(*) FROM notification_outbox WHERE appointment_id = ?",
                (appointment["id"],),
            ).fetchone()[0]
        self.assertEqual(queued, 0)

    def test_partial_overlap_is_rejected_but_back_to_back_is_allowed(self):
        start = datetime.fromisoformat(self.future_time(days=27, hour=10))
        first = self.request_appointment(start.isoformat())
        partial_overlap = self.request_appointment(
            (start + timedelta(minutes=15)).isoformat()
        )
        self.assertEqual(self.update(first, "provider", "confirm").status_code, 200)
        rejected = self.update(partial_overlap, "provider", "confirm")
        self.assertEqual(rejected.status_code, 409)

        later_start = start.replace(hour=13)
        before = self.request_appointment(later_start.isoformat())
        back_to_back = self.request_appointment(
            (later_start + timedelta(minutes=30)).isoformat()
        )
        self.assertEqual(self.update(before, "provider", "confirm").status_code, 200)
        self.assertEqual(self.update(back_to_back, "provider", "confirm").status_code, 200)

    def test_rescheduling_into_a_busy_slot_is_rejected_without_changing_appointment(self):
        start = datetime.fromisoformat(self.future_time(days=29, hour=10))
        moving = self.request_appointment(start.isoformat())
        busy_time = start.replace(hour=12)
        busy = self.request_appointment(busy_time.isoformat())
        moving = self.update(moving, "provider", "confirm").json()
        self.assertEqual(self.update(busy, "provider", "confirm").status_code, 200)

        rejected = self.update(
            moving,
            "provider",
            "reschedule",
            start_at=busy_time.isoformat(),
        )
        self.assertEqual(rejected.status_code, 409)
        current = next(
            item
            for item in self.client.get("/api/appointments?role=provider").json()
            if item["id"] == moving["id"]
        )
        self.assertEqual(current["start_at"], start.isoformat())
        self.assertEqual(current["version"], moving["version"])

    def test_simultaneous_confirmations_allow_only_one_overlapping_visit(self):
        first = self.request_appointment(self.future_time(days=18, hour=10))
        second = self.request_appointment(self.future_time(days=18, hour=10))

        def confirm(appointment):
            return self.update(appointment, "provider", "confirm").status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(confirm, (first, second)))
        self.assertEqual(sorted(results), [200, 409])
        confirmed = [
            item
            for item in self.client.get("/api/appointments?role=provider").json()
            if item["start_at"] == first["start_at"] and item["status"] == "confirmed"
        ]
        self.assertEqual(len(confirmed), 1)

    def test_confirmation_queues_notification_without_waiting_for_delivery(self):
        appointment = self.request_appointment(self.future_time(days=22))
        confirmed = self.update(appointment, "provider", "confirm")
        self.assertEqual(confirmed.status_code, 200)
        database = Database(self.database_path)
        with database.connect() as connection:
            outbox = connection.execute(
                """
                SELECT status, recipient FROM notification_outbox
                WHERE appointment_id = ?
                """,
                (appointment["id"],),
            ).fetchone()
        self.assertIn(outbox["status"], {"pending", "delivered"})
        self.assertEqual(outbox["recipient"], "alex.morgan@example.com")

    def test_slow_notification_logging_does_not_hold_appointment_write_lock(self):
        database = self.client.app.state.database
        notification = self.request_appointment(self.future_time(days=32))
        appointment_to_confirm = self.request_appointment(
            self.future_time(days=33, hour=12)
        )
        self.assertEqual(
            self.update(notification, "provider", "confirm").status_code, 200
        )

        logger_started = Event()
        release_logger = Event()

        def slow_log(*_args):
            logger_started.set()
            if not release_logger.wait(timeout=5):
                raise TimeoutError("Test did not release slow notification logger.")

        executor = ThreadPoolExecutor(max_workers=2)
        try:
            with patch("backend.database.logger.info", side_effect=slow_log):
                delivery = executor.submit(database.deliver_one_notification)
                self.assertTrue(logger_started.wait(timeout=2))
                confirmation = executor.submit(
                    self.update, appointment_to_confirm, "provider", "confirm"
                )
                self.assertEqual(confirmation.result(timeout=1).status_code, 200)
                self.assertFalse(delivery.done())
        finally:
            release_logger.set()
            executor.shutdown(wait=True)

    def test_notification_logging_failure_releases_outbox_claim_for_retry(self):
        database = self.client.app.state.database
        appointment = self.request_appointment(self.future_time(days=34))
        self.assertEqual(self.update(appointment, "provider", "confirm").status_code, 200)

        with patch("backend.database.logger.info", side_effect=OSError("log unavailable")):
            with self.assertRaisesRegex(OSError, "log unavailable"):
                database.deliver_one_notification()

        with database.read_connection() as connection:
            notification = connection.execute(
                """
                SELECT status, claim_token, claimed_until FROM notification_outbox
                WHERE appointment_id = ?
                """,
                (appointment["id"],),
            ).fetchone()
        self.assertEqual(notification["status"], "pending")
        self.assertIsNone(notification["claim_token"])
        self.assertIsNone(notification["claimed_until"])

        with patch("backend.database.logger.info"):
            self.assertTrue(database.deliver_one_notification())

    def test_empty_and_expired_notification_claims(self):
        database = self.client.app.state.database
        self.assertFalse(database.deliver_one_notification())
        appointment = self.request_appointment(self.future_time(days=35))
        self.assertEqual(self.update(appointment, "provider", "confirm").status_code, 200)
        with database.transaction() as connection:
            connection.execute(
                """
                UPDATE notification_outbox
                SET claim_token = 'abandoned', claimed_until = '2000-01-01T00:00:00+00:00'
                WHERE appointment_id = ?
                """,
                (appointment["id"],),
            )
        with patch("backend.database.logger.info"):
            self.assertTrue(database.deliver_one_notification())
        self.assertFalse(database.deliver_one_notification())

    def test_multiple_workers_do_not_deliver_the_same_claim(self):
        database = self.client.app.state.database
        appointment = self.request_appointment(self.future_time(days=35, hour=13))
        self.assertEqual(self.update(appointment, "provider", "confirm").status_code, 200)
        logger_started = Event()
        release_logger = Event()

        def slow_log(*_args):
            logger_started.set()
            if not release_logger.wait(timeout=5):
                raise TimeoutError("Test did not release slow notification logger.")

        executor = ThreadPoolExecutor(max_workers=2)
        try:
            with patch("backend.database.logger.info", side_effect=slow_log):
                first_delivery = executor.submit(database.deliver_one_notification)
                self.assertTrue(logger_started.wait(timeout=2))
                self.assertFalse(database.deliver_one_notification())
                release_logger.set()
                self.assertTrue(first_delivery.result(timeout=2))
        finally:
            release_logger.set()
            executor.shutdown(wait=True)

    def test_lost_notification_claim_is_reported_not_marked_delivered(self):
        database = self.client.app.state.database
        appointment = self.request_appointment(self.future_time(days=35, hour=16))
        self.assertEqual(self.update(appointment, "provider", "confirm").status_code, 200)

        def replace_claim(*_args):
            with database.transaction() as connection:
                connection.execute(
                    """
                    UPDATE notification_outbox SET claim_token = 'reclaimed'
                    WHERE appointment_id = ?
                    """,
                    (appointment["id"],),
                )

        with patch("backend.database.logger.info", side_effect=replace_claim):
            with self.assertRaisesRegex(RuntimeError, "claim was lost"):
                database.deliver_one_notification()
        with database.read_connection() as connection:
            notification = connection.execute(
                "SELECT status, claim_token FROM notification_outbox WHERE appointment_id = ?",
                (appointment["id"],),
            ).fetchone()
        self.assertEqual(notification["status"], "pending")
        self.assertEqual(notification["claim_token"], "reclaimed")

    def test_user_scoping_rejects_wrong_role_for_history_and_mutations(self):
        appointment = self.request_appointment(self.future_time(days=35, hour=17))
        self.assertEqual(
            self.client.get(
                f"/api/appointments/{appointment['id']}/history"
                "?role=patient&user_id=provider-riley"
            ).status_code,
            404,
        )
        self.assertEqual(
            self.update(
                appointment,
                "provider",
                "confirm",
                user_id="patient-alex",
            ).status_code,
            404,
        )

    def test_invalid_transitions_are_rejected(self):
        appointment = self.request_appointment(self.future_time(days=36))
        self.assertEqual(self.update(appointment, "patient", "cancel").status_code, 409)
        self.assertEqual(self.update(appointment, "patient", "confirm").status_code, 409)
        self.assertEqual(self.update(appointment, "provider", "cancel").status_code, 409)
        rescheduled = self.update(
            appointment,
            "provider",
            "reschedule",
            start_at=self.future_time(days=37),
        )
        self.assertEqual(rescheduled.status_code, 200)

        confirmed = self.update(rescheduled.json(), "provider", "confirm").json()
        self.assertEqual(self.update(confirmed, "provider", "confirm").status_code, 409)
        cancelled = self.update(confirmed, "patient", "cancel").json()
        self.assertEqual(self.update(cancelled, "provider", "confirm").status_code, 409)
        self.assertEqual(
            self.update(
                cancelled,
                "provider",
                "reschedule",
                start_at=self.future_time(days=38),
            ).status_code,
            409,
        )
        self.assertEqual(self.update(cancelled, "patient", "cancel").status_code, 409)

    def test_past_and_timezone_less_times_are_rejected(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.assertEqual(
            self.client.post(
                "/api/appointments",
                json={"start_at": past, "reason": "Past visit"},
            ).status_code,
            422,
        )
        timezone_less = self.future_time(days=39).replace("+00:00", "")
        self.assertEqual(
            self.client.post(
                "/api/appointments",
                json={"start_at": timezone_less, "reason": "No timezone"},
            ).status_code,
            422,
        )

        appointment = self.request_appointment(self.future_time(days=40))
        confirmed = self.update(appointment, "provider", "confirm").json()
        self.assertEqual(
            self.update(
                confirmed,
                "provider",
                "reschedule",
                start_at=past,
            ).status_code,
            422,
        )
        self.assertEqual(
            self.update(
                confirmed,
                "provider",
                "reschedule",
                start_at=timezone_less,
            ).status_code,
            422,
        )

    def test_patient_and_provider_scoping_is_enforced(self):
        database = self.client.app.state.database
        other_provider_appointment = self.request_appointment(self.future_time(days=42))
        with database.transaction() as connection:
            connection.execute(
                """
                UPDATE appointments
                SET provider_id = ?, provider_name = ?
                WHERE id = ?
                """,
                (
                    "provider-samira",
                    "Dr. Samira Patel",
                    other_provider_appointment["id"],
                ),
            )
        provider_list = self.client.get("/api/appointments?role=provider").json()
        self.assertNotIn(
            other_provider_appointment["id"], [item["id"] for item in provider_list]
        )
        self.assertEqual(
            self.update(other_provider_appointment, "provider", "confirm").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                f"/api/appointments/{other_provider_appointment['id']}/history?role=provider"
            ).status_code,
            404,
        )

        other_patient_appointment = self.request_appointment(self.future_time(days=43))
        with database.transaction() as connection:
            connection.execute(
                """
                UPDATE appointments SET patient_id = ?, patient_name = ?
                WHERE id = ?
                """,
                ("patient-jamie", "Jamie Lee", other_patient_appointment["id"]),
            )
        patient_list = self.client.get("/api/appointments?role=patient").json()
        self.assertNotIn(
            other_patient_appointment["id"], [item["id"] for item in patient_list]
        )
        self.assertEqual(
            self.update(other_patient_appointment, "patient", "cancel").status_code,
            404,
        )


class NotificationWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_logs_database_failure_and_retries(self):
        class FailingDatabase:
            calls = 0

            def deliver_one_notification(self):
                self.calls += 1
                if self.calls == 1:
                    raise OSError("temporary database failure")
                raise asyncio.CancelledError

        database = FailingDatabase()
        with (
            patch("backend.main.logger.exception") as log_exception,
            patch("backend.main.asyncio.sleep", new=AsyncMock()) as sleep,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await notification_worker(database)
        self.assertEqual(database.calls, 2)
        log_exception.assert_called_once()
        sleep.assert_awaited_once_with(1)

    async def test_worker_polls_again_immediately_after_successful_delivery(self):
        class SequentialDatabase:
            outcomes = iter((False, True, asyncio.CancelledError()))
            calls = 0

            def deliver_one_notification(self):
                self.calls += 1
                outcome = next(self.outcomes)
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome

        database = SequentialDatabase()
        with patch(
            "backend.main.asyncio.sleep", new=AsyncMock()
        ) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await notification_worker(database)
        self.assertEqual(database.calls, 3)
        sleep.assert_awaited_once_with(1)


if __name__ == "__main__":
    unittest.main()
