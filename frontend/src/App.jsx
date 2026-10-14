import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowRight,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock3,
  FileClock,
  Heart,
  History,
  Plus,
  RefreshCw,
  ShieldCheck,
  Stethoscope,
  X,
} from "lucide-react";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000/api";
const initialUsers = {
  patient: [
    { id: "patient-alex", role: "patient", name: "Alex Morgan" },
    { id: "patient-jamie", role: "patient", name: "Jamie Lee" },
  ],
  provider: [
    { id: "provider-riley", role: "provider", name: "Dr. Riley Chen" },
    { id: "provider-samira", role: "provider", name: "Dr. Samira Patel" },
  ],
};

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    const error = new Error(
      typeof detail === "string" ? detail : detail?.message || "Something went wrong.",
    );
    error.status = response.status;
    error.current = detail?.current;
    throw error;
  }
  return payload;
}

function localInputValue(isoDate) {
  const date = new Date(isoDate);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function formatDate(isoDate) {
  return new Intl.DateTimeFormat("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  }).format(new Date(isoDate));
}

function formatTime(isoDate) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(isoDate));
}

function Status({ status }) {
  return (
    <span className={`status status-${status}`}>
      <span className="status-dot" />
      {status}
    </span>
  );
}

function initials(name) {
  return name
    .replace(/^Dr\.\s*/, "")
    .split(/\s+/)
    .map((part) => part[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

function App() {
  const [role, setRole] = useState("patient");
  const [userId, setUserId] = useState("patient-alex");
  const [demoUsers, setDemoUsers] = useState(initialUsers);
  const [requestedProviderId, setRequestedProviderId] = useState("provider-riley");
  const [appointments, setAppointments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reason, setReason] = useState("");
  const [requestedAt, setRequestedAt] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [reschedule, setReschedule] = useState(null);
  const [historyId, setHistoryId] = useState(null);
  const [history, setHistory] = useState([]);
  const [historyError, setHistoryError] = useState("");
  const appointmentRequest = useRef(0);
  const activeUsers = demoUsers[role] || [];
  const activeUser = activeUsers.find((user) => user.id === userId) || initialUsers[role][0];

  const loadAppointments = useCallback(async ({ silent = false } = {}) => {
    const requestId = ++appointmentRequest.current;
    if (!silent) setLoading(true);
    try {
      const results = await api(`/appointments?role=${role}&user_id=${userId}`);
      if (requestId === appointmentRequest.current) {
        setAppointments(results);
        setError("");
      }
    } catch (loadError) {
      if (requestId === appointmentRequest.current) setError(loadError.message);
    } finally {
      if (requestId === appointmentRequest.current) setLoading(false);
    }
  }, [role, userId]);

  useEffect(() => {
    api("/demo-users")
      .then(setDemoUsers)
      .catch((loadError) => setError(loadError.message));
  }, []);

  useEffect(() => {
    loadAppointments();
  }, [loadAppointments]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      loadAppointments({ silent: true });
    }, 5000);
    return () => window.clearInterval(interval);
  }, [loadAppointments]);

  async function submitRequest(event) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await api("/appointments", {
        method: "POST",
        body: JSON.stringify({
          start_at: new Date(requestedAt).toISOString(),
          reason: reason.trim(),
          patient_id: userId,
          provider_id: requestedProviderId,
        }),
      });
      setReason("");
      setRequestedAt("");
      setNotice("Your appointment request has been sent to the care team.");
      await loadAppointments();
    } catch (submitError) {
      setError(submitError.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function updateAppointment(appointment, action, startAt, expectedVersion) {
    setError("");
    setNotice("");
    try {
      await api(`/appointments/${appointment.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          role,
          user_id: userId,
          action,
          expected_version: expectedVersion ?? appointment.version,
          ...(startAt ? { start_at: new Date(startAt).toISOString() } : {}),
        }),
      });
      setReschedule(null);
      setNotice(
        action === "confirm"
          ? "Appointment confirmed. A patient notification is queued."
          : action === "cancel"
            ? "Appointment cancelled."
            : "Appointment time updated. The change is recorded in its history.",
      );
      await loadAppointments();
    } catch (updateError) {
      if (updateError.status === 409) {
        setReschedule(null);
        setNotice(
          updateError.current
            ? "This appointment changed while you were viewing it. The latest details have been loaded; please review and try again."
            : updateError.message,
        );
        await loadAppointments();
      } else {
        setError(updateError.message);
      }
    }
  }

  async function showHistory(appointment) {
    setHistoryId(appointment.id);
    setHistoryError("");
    try {
      setHistory(
        await api(`/appointments/${appointment.id}/history?role=${role}&user_id=${userId}`),
      );
    } catch (historyLoadError) {
      setHistoryError(historyLoadError.message);
      setHistory([]);
    }
  }

  const visibleAppointments = role === "patient"
    ? appointments.filter(
        (appointment) =>
          appointment.status !== "cancelled" &&
          new Date(appointment.start_at).getTime() > Date.now(),
      )
    : appointments;
  const pendingCount = visibleAppointments.filter((item) => item.status === "pending").length;
  const confirmedCount = visibleAppointments.filter((item) => item.status === "confirmed").length;
  const shownAppointments = [...visibleAppointments].sort(
    (first, second) => new Date(first.start_at) - new Date(second.start_at),
  );

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#" aria-label="Harbor Health home">
          <span className="brand-mark"><Activity size={21} strokeWidth={2.4} /></span>
          <span>harbor<span className="brand-light">health</span></span>
        </a>
        <div className="workspace-label">WORKSPACE</div>
        <button className="nav-link active"><CalendarDays size={18} /> Appointments</button>
        <div className="sidebar-bottom">
          <div className="help-card">
            <div className="help-icon"><Heart size={18} /></div>
            <strong>Here for your health</strong>
            <p>Your care journey, all in one place.</p>
          </div>
          <div className="user-mini">
            <div className="avatar avatar-small">{initials(activeUser.name)}</div>
            <div><strong>{activeUser.name}</strong><span>{role === "patient" ? "Patient account" : "Care provider"}</span></div>
            <ChevronDown size={16} className="muted-icon" />
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb">Workspace <span>/</span> <strong>Appointments</strong></div>
          <label className="role-switch">
            <span>Preview as</span>
            <select
              aria-label="Switch user role"
              value={userId}
              onChange={(event) => {
                const selected = [...demoUsers.patient, ...demoUsers.provider].find(
                  (user) => user.id === event.target.value,
                );
                if (!selected) return;
                setRole(selected.role);
                setUserId(selected.id);
                setNotice("");
                setError("");
                setHistoryId(null);
              }}
            >
              <optgroup label="Patients">
                {demoUsers.patient.map((user) => <option key={user.id} value={user.id}>{user.name}</option>)}
              </optgroup>
              <optgroup label="Providers">
                {demoUsers.provider.map((user) => <option key={user.id} value={user.id}>{user.name}</option>)}
              </optgroup>
            </select>
            <ChevronDown size={14} />
          </label>
        </header>

        <div className="page-wrap">
          <section className="welcome-row">
            <div>
              <div className="eyebrow"><span className="eyebrow-line" /> YOUR CARE, IN GOOD HANDS</div>
              <h1>{role === "patient" ? "Your appointments" : "Care schedule"}</h1>
              <p className="page-subtitle">
                {role === "patient"
                  ? "Keep track of your visits and stay connected with your care team."
                  : "Review requests and keep your patients' care moving forward."}
              </p>
            </div>
            <div className="welcome-decoration"><div className="decoration-circle"><Stethoscope size={26} /></div><span className="decoration-spark spark-one" /><span className="decoration-spark spark-two" /></div>
          </section>

          <section className="summary-grid" aria-label="Appointment summary">
            <div className="summary-card">
              <div className="summary-icon icon-mint"><CalendarDays size={19} /></div>
              <div><span className="summary-label">TOTAL VISITS</span><strong>{visibleAppointments.length}</strong></div>
              <span className="summary-foot">on your schedule</span>
            </div>
            <div className="summary-card">
              <div className="summary-icon icon-blue"><CheckCircle2 size={19} /></div>
              <div><span className="summary-label">CONFIRMED</span><strong>{confirmedCount}</strong></div>
              <span className="summary-foot">ready for you</span>
            </div>
            <div className="summary-card">
              <div className="summary-icon icon-amber"><Clock3 size={19} /></div>
              <div><span className="summary-label">AWAITING</span><strong>{pendingCount}</strong></div>
              <span className="summary-foot">pending review</span>
            </div>
          </section>

          <section className="appointments-section">
            <div className="section-heading">
              <div><h2>{role === "patient" ? "Upcoming visits" : "Appointment requests"}</h2><p>{role === "patient" ? "Your visits with the care team" : `Appointments assigned to ${activeUser.name}`}</p></div>
              {role === "patient" && <button className="primary-button" onClick={() => document.getElementById("request-form").scrollIntoView({ behavior: "smooth" })}><Plus size={17} /> Request a visit</button>}
            </div>

            {notice && <div className="notice" role="status"><CheckCircle2 size={17} />{notice}<button aria-label="Dismiss notice" onClick={() => setNotice("")}><X size={15} /></button></div>}
            {error && <div className="alert" role="alert">{error}<button aria-label="Dismiss error" onClick={() => setError("")}><X size={15} /></button></div>}

            {loading ? (
              <div className="loading-state"><span className="spinner" /> Loading appointments…</div>
            ) : shownAppointments.length === 0 ? (
              <div className="empty-state"><div className="empty-icon"><CalendarDays size={23} /></div><h3>No visits just yet</h3><p>When appointments are scheduled, they’ll show up here.</p></div>
            ) : (
              <div className="appointment-list">
                {shownAppointments.map((appointment) => (
                  <article className="appointment-card" key={appointment.id}>
                    <div className="date-tile"><span>{new Intl.DateTimeFormat("en-US", { month: "short" }).format(new Date(appointment.start_at)).toUpperCase()}</span><strong>{new Date(appointment.start_at).getDate()}</strong><small>{new Intl.DateTimeFormat("en-US", { weekday: "short" }).format(new Date(appointment.start_at))}</small></div>
                    <div className="appointment-info">
                      <div className="appointment-title-row"><h3>{appointment.appointment_type}</h3><Status status={appointment.status} /></div>
                      <div className="visit-meta"><span><Clock3 size={14} />{formatTime(appointment.start_at)}</span><span><Stethoscope size={14} />{role === "patient" ? appointment.provider_name : appointment.patient_name}</span></div>
                      <p className="reason"><span>Reason</span>{appointment.reason}</p>
                      {historyId === appointment.id && (
                        <div className="history-panel">
                          <div className="history-heading"><strong><FileClock size={15} /> Appointment history</strong><button onClick={() => setHistoryId(null)} aria-label="Close history"><X size={15} /></button></div>
                          {historyError ? <p className="history-error">{historyError}</p> : history.length === 0 ? <p>Loading history…</p> : history.map((event, index) => (
                            <div className="history-event" key={`${event.occurred_at}-${index}`}>
                              <span className="history-mark" />
                              <div><strong>{event.action[0].toUpperCase() + event.action.slice(1)}</strong><span>{event.actor_name} · {formatDate(event.occurred_at)} at {formatTime(event.occurred_at)}</span>
                                {event.before && event.after.start_at !== event.before.start_at && <small>Was {formatDate(event.before.start_at)} at {formatTime(event.before.start_at)} → now {formatDate(event.after.start_at)} at {formatTime(event.after.start_at)}</small>}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="card-actions">
                      <button className="text-button history-button" onClick={() => showHistory(appointment)}><History size={15} /> History</button>
                      {role === "patient" && appointment.status === "confirmed" && <button className="subtle-button cancel-button" onClick={() => updateAppointment(appointment, "cancel")}>Cancel visit</button>}
                      {role === "provider" && appointment.status === "pending" && <button className="subtle-button confirm-button" onClick={() => updateAppointment(appointment, "confirm")}><Check size={15} /> Confirm</button>}
                      {role === "provider" && appointment.status !== "cancelled" && <button className="text-button" onClick={() => setReschedule({ id: appointment.id, version: appointment.version, value: localInputValue(appointment.start_at) })}><RefreshCw size={14} /> Reschedule</button>}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>

          {role === "patient" ? (
            <section className="request-panel" id="request-form">
              <div className="request-copy">
                <div className="request-icon"><CalendarDays size={20} /></div>
                <div><h2>Need a visit?</h2><p>Tell us what works for you. Our care team will review your request and get back to you.</p></div>
                <ArrowRight size={19} className="request-arrow" />
              </div>
              <form onSubmit={submitRequest} className="request-form">
                <label>Preferred date &amp; time<input required type="datetime-local" min={new Date(Date.now() + 60_000).toISOString().slice(0, 16)} value={requestedAt} onChange={(event) => setRequestedAt(event.target.value)} /></label>
                <label>Choose a provider<select required value={requestedProviderId} onChange={(event) => setRequestedProviderId(event.target.value)}>{demoUsers.provider.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</select></label>
                <label>What can we help with?<input required minLength={3} maxLength={500} placeholder="A brief reason for your visit" value={reason} onChange={(event) => setReason(event.target.value)} /></label>
                <button className="primary-button submit-button" type="submit" disabled={submitting}><Plus size={16} />{submitting ? "Sending…" : "Send request"}</button>
              </form>
              <div className="privacy-note"><ShieldCheck size={15} /> Your health information is handled with care.</div>
            </section>
          ) : (
            <div className="provider-note"><ShieldCheck size={18} /><span><strong>Provider workspace</strong> · Confirming a visit notifies the patient. Every change is saved in the appointment history.</span></div>
          )}
          <footer>© 2026 Harbor Health <span>·</span> Your health, your harbor.</footer>
        </div>
      </main>

      {reschedule && (
        <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setReschedule(null); }}>
          <form className="reschedule-modal" onSubmit={(event) => { event.preventDefault(); const appointment = appointments.find((item) => item.id === reschedule.id); if (appointment) updateAppointment(appointment, "reschedule", reschedule.value, reschedule.version); }}>
            <button type="button" className="modal-close" aria-label="Close" onClick={() => setReschedule(null)}><X size={18} /></button>
            <div className="request-icon"><RefreshCw size={19} /></div><h2>Reschedule visit</h2><p>Choose a new date and time. The patient will see the updated appointment and its history will be preserved.</p>
            <label>New date &amp; time<input required type="datetime-local" min={new Date(Date.now() + 60_000).toISOString().slice(0, 16)} value={reschedule.value} onChange={(event) => setReschedule({ ...reschedule, value: event.target.value })} /></label>
            <div className="modal-actions"><button className="text-button" type="button" onClick={() => setReschedule(null)}>Keep current time</button><button className="primary-button" type="submit">Save new time</button></div>
          </form>
        </div>
      )}
    </div>
  );
}

export default App;
