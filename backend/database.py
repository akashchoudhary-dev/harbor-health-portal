import json
import logging
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4


logger = logging.getLogger(__name__)

DEMO_USERS = (
    {
        "id": "patient-alex",
        "role": "patient",
        "name": "Alex Morgan",
        "email": "alex.morgan@example.com",
    },
    {
        "id": "patient-jamie",
        "role": "patient",
        "name": "Jamie Lee",
        "email": "jamie.lee@example.com",
    },
    {
        "id": "provider-riley",
        "role": "provider",
        "name": "Dr. Riley Chen",
        "email": "riley.chen@example.com",
    },
    {
        "id": "provider-samira",
        "role": "provider",
        "name": "Dr. Samira Patel",
        "email": "samira.patel@example.com",
    },
)
DEFAULT_USER_IDS = {"patient": "patient-alex", "provider": "provider-riley"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def future_iso(value: str) -> str:
    start_at = datetime.fromisoformat(value)
    if start_at.tzinfo is None or start_at.utcoffset() is None:
        raise ValueError("Appointment time must include a timezone.")
    start_at = start_at.astimezone(timezone.utc).replace(microsecond=0)
    if start_at <= utc_now():
        raise ValueError("Choose a future date and time.")
    return as_iso(start_at)


def appointment_snapshot(row: sqlite3.Row) -> dict:
    return {
        key: row[key]
        for key in (
            "id",
            "patient_id",
            "patient_name",
            "patient_email",
            "provider_id",
            "provider_name",
            "appointment_type",
            "reason",
            "start_at",
            "duration_minutes",
            "status",
            "version",
            "created_at",
            "updated_at",
        )
    }


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._in_memory = str(path) == ":memory:"
        self._connection_path = (
            f"file:patient-portal-{uuid4().hex}?mode=memory&cache=shared"
            if self._in_memory
            else self.path
        )
        self._keeper = None
        if not self._in_memory:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        else:
            self._keeper = self.connect()
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._connection_path,
            timeout=10,
            isolation_level=None,
            uri=self._in_memory,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def read_connection(self) -> Iterator[sqlite3.Connection]:
        with closing(self.connect()) as connection:
            yield connection

    def initialize(self) -> None:
        with self.read_connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS demo_users (
                    id TEXT PRIMARY KEY,
                    role TEXT NOT NULL CHECK (role IN ('patient', 'provider')),
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS appointments (
                    id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL REFERENCES demo_users(id),
                    patient_name TEXT NOT NULL,
                    patient_email TEXT NOT NULL,
                    provider_id TEXT NOT NULL REFERENCES demo_users(id),
                    provider_name TEXT NOT NULL,
                    appointment_type TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
                    status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'cancelled')),
                    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS appointment_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    appointment_id TEXT NOT NULL REFERENCES appointments(id),
                    action TEXT NOT NULL,
                    actor_role TEXT NOT NULL CHECK (actor_role IN ('patient', 'provider')),
                    actor_name TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS appointment_events_appointment_id
                    ON appointment_events(appointment_id, id);
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    appointment_id TEXT NOT NULL REFERENCES appointments(id),
                    recipient TEXT NOT NULL,
                    message TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'delivered')),
                    created_at TEXT NOT NULL,
                    delivered_at TEXT,
                    claim_token TEXT,
                    claimed_until TEXT
                );
                """
            )
        with self.transaction() as connection:
            outbox_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(notification_outbox)")
            }
            if "claim_token" not in outbox_columns:
                connection.execute("ALTER TABLE notification_outbox ADD COLUMN claim_token TEXT")
            if "claimed_until" not in outbox_columns:
                connection.execute("ALTER TABLE notification_outbox ADD COLUMN claimed_until TEXT")
            for user in DEMO_USERS:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO demo_users (id, role, name, email)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user["id"], user["role"], user["name"], user["email"]),
                )
            appointment_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(appointments)")
            }
            if "patient_id" not in appointment_columns:
                connection.execute(
                    "ALTER TABLE appointments ADD COLUMN patient_id TEXT REFERENCES demo_users(id)"
                )
            if "provider_id" not in appointment_columns:
                connection.execute(
                    "ALTER TABLE appointments ADD COLUMN provider_id TEXT REFERENCES demo_users(id)"
                )
            connection.execute(
                """
                UPDATE appointments SET patient_id = CASE patient_name
                    WHEN 'Jamie Lee' THEN 'patient-jamie'
                    ELSE 'patient-alex'
                END WHERE patient_id IS NULL
                """
            )
            connection.execute(
                """
                UPDATE appointments SET provider_id = CASE provider_name
                    WHEN 'Dr. Samira Patel' THEN 'provider-samira'
                    ELSE 'provider-riley'
                END WHERE provider_id IS NULL
                """
            )
            connection.execute("DROP TRIGGER IF EXISTS no_confirmed_provider_overlap_insert")
            connection.execute("DROP TRIGGER IF EXISTS no_confirmed_provider_overlap_update")
            connection.execute(
                """
                CREATE TRIGGER no_confirmed_provider_overlap_insert
                BEFORE INSERT ON appointments
                WHEN NEW.status = 'confirmed'
                BEGIN
                    SELECT RAISE(ABORT, 'confirmed appointment overlap')
                    WHERE EXISTS (
                        SELECT 1 FROM appointments existing
                        WHERE existing.provider_id = NEW.provider_id
                          AND existing.status = 'confirmed'
                          AND julianday(existing.start_at) <
                              julianday(NEW.start_at, '+' || NEW.duration_minutes || ' minutes')
                          AND julianday(NEW.start_at) <
                              julianday(existing.start_at, '+' || existing.duration_minutes || ' minutes')
                    );
                END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER no_confirmed_provider_overlap_update
                BEFORE UPDATE OF provider_id, start_at, duration_minutes, status
                ON appointments
                WHEN NEW.status = 'confirmed'
                BEGIN
                    SELECT RAISE(ABORT, 'confirmed appointment overlap')
                    WHERE EXISTS (
                        SELECT 1 FROM appointments existing
                        WHERE existing.id != NEW.id
                          AND existing.provider_id = NEW.provider_id
                          AND existing.status = 'confirmed'
                          AND julianday(existing.start_at) <
                              julianday(NEW.start_at, '+' || NEW.duration_minutes || ' minutes')
                          AND julianday(NEW.start_at) <
                              julianday(existing.start_at, '+' || existing.duration_minutes || ' minutes')
                    );
                END
                """
            )
            self._seed(connection)

    def _seed(self, connection: sqlite3.Connection) -> None:
        now = utc_now().replace(second=0, microsecond=0)
        seed = (
            (
                "patient-alex",
                "provider-riley",
                "confirmed",
                2,
                9,
                "Annual check-up",
                "Routine wellness visit",
            ),
            (
                "patient-alex",
                "provider-riley",
                "pending",
                4,
                11,
                "Follow-up",
                "Review recent test results",
            ),
            (
                "patient-alex",
                "provider-samira",
                "confirmed",
                6,
                14,
                "Consultation",
                "Discuss recurring headaches",
            ),
            (
                "patient-jamie",
                "provider-samira",
                "confirmed",
                3,
                10,
                "New patient visit",
                "Discuss care plan and medical history",
            ),
            (
                "patient-jamie",
                "provider-riley",
                "pending",
                8,
                15,
                "Wellness visit",
                "Preventive care consultation",
            ),
        )
        users = {user["id"]: user for user in DEMO_USERS}
        for patient_id, provider_id, status, day, hour, appointment_type, reason in seed:
            existing = connection.execute(
                """
                SELECT 1 FROM appointments
                WHERE patient_id = ? AND provider_id = ? AND appointment_type = ?
                LIMIT 1
                """,
                (patient_id, provider_id, appointment_type),
            ).fetchone()
            if existing is not None:
                continue
            start = (now + timedelta(days=day)).replace(hour=hour, minute=0)
            patient = users[patient_id]
            provider = users[provider_id]
            self._insert_appointment(
                connection,
                {
                    "id": str(uuid4()),
                    "patient_id": patient_id,
                    "patient_name": patient["name"],
                    "patient_email": patient["email"],
                    "provider_id": provider_id,
                    "provider_name": provider["name"],
                    "appointment_type": appointment_type,
                    "reason": reason,
                    "start_at": as_iso(start),
                    "duration_minutes": 30,
                    "status": status,
                    "version": 1,
                    "created_at": as_iso(now),
                    "updated_at": as_iso(now),
                },
                actor_role="provider" if status == "confirmed" else "patient",
                actor_name=provider["name"] if status == "confirmed" else patient["name"],
                action="seeded",
                before=None,
            )

    def _insert_appointment(
        self,
        connection: sqlite3.Connection,
        appointment: dict,
        actor_role: str,
        actor_name: str,
        action: str,
        before: dict | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO appointments (
                id, patient_id, patient_name, patient_email, provider_id, provider_name, appointment_type,
                reason, start_at, duration_minutes, status, version, created_at, updated_at
            ) VALUES (
                :id, :patient_id, :patient_name, :patient_email, :provider_id, :provider_name, :appointment_type,
                :reason, :start_at, :duration_minutes, :status, :version, :created_at, :updated_at
            )
            """,
            appointment,
        )
        self._event(connection, appointment, action, actor_role, actor_name, before)

    def _event(
        self,
        connection: sqlite3.Connection,
        appointment: dict,
        action: str,
        actor_role: str,
        actor_name: str,
        before: dict | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO appointment_events (
                appointment_id, action, actor_role, actor_name, occurred_at,
                before_json, after_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                appointment["id"],
                action,
                actor_role,
                actor_name,
                appointment["updated_at"],
                json.dumps(before) if before is not None else None,
                json.dumps(appointment),
            ),
        )

    def demo_users(self) -> dict:
        with self.read_connection() as connection:
            rows = connection.execute(
                "SELECT id, role, name FROM demo_users ORDER BY role, name"
            ).fetchall()
        return {
            role: [dict(row) for row in rows if row["role"] == role]
            for role in ("patient", "provider")
        }

    def list_appointments(self, role: str, user_id: str | None = None) -> list[dict]:
        user_id = user_id or DEFAULT_USER_IDS.get(role)
        with self.read_connection() as connection:
            user = connection.execute(
                "SELECT role FROM demo_users WHERE id = ?", (user_id,)
            ).fetchone()
            if user is None or user["role"] != role:
                raise LookupError("Demo user not found")
            rows = connection.execute(
                """
                SELECT * FROM appointments
                WHERE (? = 'provider' AND provider_id = ?)
                   OR (? = 'patient' AND patient_id = ?)
                ORDER BY start_at, created_at
                """,
                (role, user_id, role, user_id),
            ).fetchall()
        return [appointment_snapshot(row) for row in rows]

    def create_appointment(
        self,
        start_at: str,
        reason: str,
        patient_id: str = "patient-alex",
        provider_id: str = "provider-riley",
    ) -> dict:
        start_at = future_iso(start_at)
        with self.transaction() as connection:
            patient = self._require_user(connection, patient_id, "patient")
            provider = self._require_user(connection, provider_id, "provider")
            now = as_iso(utc_now())
            appointment = {
                "id": str(uuid4()),
                "patient_id": patient_id,
                "patient_name": patient["name"],
                "patient_email": patient["email"],
                "provider_id": provider_id,
                "provider_name": provider["name"],
                "appointment_type": "Requested visit",
                "reason": reason,
                "start_at": future_iso(start_at),
                "duration_minutes": 30,
                "status": "pending",
                "version": 1,
                "created_at": now,
                "updated_at": now,
            }
            self._insert_appointment(
                connection,
                appointment,
                "patient",
                patient["name"],
                "requested",
                None,
            )
        return appointment

    def update_appointment(
        self,
        appointment_id: str,
        role: str,
        action: str,
        expected_version: int,
        start_at: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        user_id = user_id or DEFAULT_USER_IDS.get(role)
        with self.transaction() as connection:
            user = self._require_user(connection, user_id, role)
            row = connection.execute(
                "SELECT * FROM appointments WHERE id = ?", (appointment_id,)
            ).fetchone()
            if row is None:
                raise LookupError("Appointment not found")
            current = appointment_snapshot(row)
            if role == "patient" and current["patient_id"] != user_id:
                raise LookupError("Appointment not found")
            if role == "provider" and current["provider_id"] != user_id:
                raise LookupError("Appointment not found")
            if current["version"] != expected_version:
                raise StaleAppointmentError(current)
            if action == "cancel":
                if role != "patient" or current["status"] != "confirmed":
                    raise InvalidTransitionError(
                        "Only patients can cancel confirmed appointments."
                    )
                changes = {"status": "cancelled"}
                event_action = "cancelled"
            elif action == "confirm":
                if role != "provider" or current["status"] != "pending":
                    raise InvalidTransitionError(
                        "Only providers can confirm pending appointments."
                    )
                changes = {"status": "confirmed"}
                event_action = "confirmed"
            elif action == "reschedule":
                if role != "provider" or current["status"] == "cancelled":
                    raise InvalidTransitionError(
                        "Only providers can reschedule active appointments."
                    )
                if start_at is None:
                    raise ValueError("A new appointment time is required.")
                changes = {"start_at": future_iso(start_at)}
                event_action = "rescheduled"
            else:
                raise ValueError("Unknown appointment action.")

            updated = {**current, **changes, "version": current["version"] + 1}
            updated["updated_at"] = as_iso(utc_now())
            try:
                connection.execute(
                    """
                    UPDATE appointments
                    SET status = :status, start_at = :start_at, version = :version,
                        updated_at = :updated_at
                    WHERE id = :id AND version = :old_version
                    """,
                    {
                        **updated,
                        "old_version": current["version"],
                    },
                )
            except sqlite3.IntegrityError as error:
                if "confirmed appointment overlap" in str(error):
                    raise AppointmentOverlapError from error
                raise

            self._event(connection, updated, event_action, role, user["name"], current)
            if action == "confirm":
                connection.execute(
                    """
                    INSERT INTO notification_outbox (
                        appointment_id, recipient, message, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        updated["id"],
                        updated["patient_email"],
                        f"Your appointment on {updated['start_at']} has been confirmed.",
                        updated["updated_at"],
                    ),
                )
            return updated

    @staticmethod
    def _require_user(
        connection: sqlite3.Connection,
        user_id: str | None,
        role: str,
    ) -> sqlite3.Row:
        user = connection.execute(
            "SELECT id, role, name, email FROM demo_users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if user is None or user["role"] != role:
            raise LookupError("Demo user not found")
        return user

    def appointment_history(
        self,
        appointment_id: str,
        role: str,
        user_id: str | None = None,
    ) -> list[dict]:
        user_id = user_id or DEFAULT_USER_IDS.get(role)
        with self.read_connection() as connection:
            user = connection.execute(
                "SELECT role FROM demo_users WHERE id = ?", (user_id,)
            ).fetchone()
            if user is None or user["role"] != role:
                raise LookupError("Appointment not found")
            appointment = connection.execute(
                """
                SELECT * FROM appointments WHERE id = ?
                  AND ((? = 'patient' AND patient_id = ?)
                    OR (? = 'provider' AND provider_id = ?))
                """,
                (appointment_id, role, user_id, role, user_id),
            ).fetchone()
            if appointment is None:
                raise LookupError("Appointment not found")
            rows = connection.execute(
                """
                SELECT action, actor_role, actor_name, occurred_at, before_json, after_json
                FROM appointment_events
                WHERE appointment_id = ? ORDER BY id
                """,
                (appointment_id,),
            ).fetchall()
        return [
            {
                **dict(row),
                "before": json.loads(row["before_json"]) if row["before_json"] else None,
                "after": json.loads(row["after_json"]),
            }
            for row in rows
        ]

    def deliver_one_notification(self) -> bool:
        claim_token = str(uuid4())
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM notification_outbox
                WHERE status = 'pending'
                  AND (claimed_until IS NULL OR claimed_until < ?)
                ORDER BY id LIMIT 1
                """,
                (as_iso(now),),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                """
                UPDATE notification_outbox
                SET claim_token = ?, claimed_until = ?
                WHERE id = ? AND status = 'pending'
                """,
                (
                    claim_token,
                    as_iso(now + timedelta(minutes=5)),
                    row["id"],
                ),
            )
            notification = dict(row)

        try:
            logger.info(
                "Would send notification to %s: %s",
                notification["recipient"],
                notification["message"],
            )
        except Exception:
            with self.transaction() as connection:
                connection.execute(
                    """
                    UPDATE notification_outbox
                    SET claim_token = NULL, claimed_until = NULL
                    WHERE id = ? AND claim_token = ?
                    """,
                    (notification["id"], claim_token),
                )
            raise

        with self.transaction() as connection:
            result = connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'delivered', delivered_at = ?,
                    claim_token = NULL, claimed_until = NULL
                WHERE id = ? AND status = 'pending' AND claim_token = ?
                """,
                (as_iso(utc_now()), notification["id"], claim_token),
            )
            if result.rowcount != 1:
                raise RuntimeError("Notification claim was lost before delivery completed.")
        return True


class StaleAppointmentError(Exception):
    def __init__(self, current: dict):
        self.current = current


class InvalidTransitionError(Exception):
    pass


class AppointmentOverlapError(Exception):
    pass


def configured_database() -> Database:
    return Database(os.getenv("DATABASE_PATH", "data/appointments.db"))
