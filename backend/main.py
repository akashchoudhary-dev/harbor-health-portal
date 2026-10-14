import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from backend.database import (
    AppointmentOverlapError,
    Database,
    InvalidTransitionError,
    StaleAppointmentError,
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RequestBody(BaseModel):
    start_at: datetime
    reason: str = Field(min_length=3, max_length=500)
    patient_id: str = "patient-alex"
    provider_id: str = "provider-riley"

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Reason must be at least 3 characters.")
        return value

    @field_validator("start_at")
    @classmethod
    def normalize_start_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Appointment time must include a timezone.")
        return value.astimezone(timezone.utc).replace(microsecond=0)


class UpdateBody(BaseModel):
    role: str
    action: str
    expected_version: int = Field(ge=1)
    user_id: str | None = None
    start_at: datetime | None = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        if value not in {"patient", "provider"}:
            raise ValueError("Role must be patient or provider.")
        return value

    @field_validator("action")
    @classmethod
    def validate_action(cls, value: str) -> str:
        if value not in {"confirm", "cancel", "reschedule"}:
            raise ValueError("Unknown appointment action.")
        return value

    @field_validator("start_at")
    @classmethod
    def normalize_start_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Appointment time must include a timezone.")
        return value.astimezone(timezone.utc).replace(microsecond=0)


async def notification_worker(database: Database) -> None:
    while True:
        try:
            delivered = await asyncio.to_thread(database.deliver_one_notification)
        except Exception:
            logger.exception("Notification outbox delivery failed; it will be retried.")
            delivered = False
        if not delivered:
            await asyncio.sleep(1)


def create_app(database_path: str | Path | None = None) -> FastAPI:
    database = Database(database_path or os.getenv("DATABASE_PATH", "data/appointments.db"))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        worker = asyncio.create_task(notification_worker(database))
        try:
            yield
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="Patient Portal API", version="1.0.0", lifespan=lifespan)
    app.state.database = database
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/demo-users")
    def demo_users() -> dict:
        return database.demo_users()

    @app.get("/api/appointments")
    def list_appointments(
        role: str = Query(pattern="^(patient|provider)$"),
        user_id: str | None = None,
    ) -> list[dict]:
        try:
            return database.list_appointments(role, user_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/appointments", status_code=201)
    def request_appointment(body: RequestBody) -> dict:
        if body.start_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=422, detail="Choose a future date and time.")
        try:
            return database.create_appointment(
                body.start_at.isoformat(),
                body.reason.strip(),
                body.patient_id,
                body.provider_id,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.patch("/api/appointments/{appointment_id}")
    def update_appointment(
        appointment_id: str,
        body: UpdateBody,
    ) -> dict:
        if body.action == "reschedule" and body.start_at is not None:
            if body.start_at <= datetime.now(timezone.utc):
                raise HTTPException(status_code=422, detail="Choose a future date and time.")
        try:
            appointment = database.update_appointment(
                appointment_id,
                body.role,
                body.action,
                body.expected_version,
                body.start_at.isoformat() if body.start_at else None,
                body.user_id,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except StaleAppointmentError as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "stale_appointment",
                    "message": "This appointment changed while you were viewing it. The latest version is shown.",
                    "current": error.current,
                },
            ) from error
        except InvalidTransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except AppointmentOverlapError as error:
            raise HTTPException(
                status_code=409,
                detail="This time overlaps another confirmed appointment for this provider.",
            ) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return appointment

    @app.get("/api/appointments/{appointment_id}/history")
    def appointment_history(
        appointment_id: str,
        role: str = Query(pattern="^(patient|provider)$"),
        user_id: str | None = None,
    ) -> list[dict]:
        try:
            return database.appointment_history(appointment_id, role, user_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return app


app = create_app()
