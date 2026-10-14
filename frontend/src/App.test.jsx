import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';

const demoUsers = {
  patient: [
    { id: 'patient-alex', role: 'patient', name: 'Alex Morgan' },
    { id: 'patient-jamie', role: 'patient', name: 'Jamie Lee' },
  ],
  provider: [
    { id: 'provider-riley', role: 'provider', name: 'Dr. Riley Chen' },
    { id: 'provider-samira', role: 'provider', name: 'Dr. Samira Patel' },
  ],
};

const patientVisits = [
  {
    id: 'apt-1',
    appointment_type: 'Annual check-up',
    patient_id: 'patient-alex',
    patient_name: 'Alex Morgan',
    patient_email: 'alex.morgan@example.com',
    provider_id: 'provider-riley',
    provider_name: 'Dr. Riley Chen',
    reason: 'Routine wellness visit',
    start_at: '2030-02-12T10:00:00Z',
    duration_minutes: 30,
    status: 'confirmed',
    version: 1,
    created_at: '2026-01-01T10:00:00Z',
    updated_at: '2026-01-01T10:00:00Z',
  },
];

const providerVisits = [
  {
    ...patientVisits[0],
    id: 'apt-2',
    appointment_type: 'Follow-up',
    provider_id: 'provider-riley',
    provider_name: 'Dr. Riley Chen',
    patient_id: 'patient-jamie',
    patient_name: 'Jamie Lee',
    reason: 'Review recent test results',
    status: 'pending',
    start_at: '2030-03-05T09:00:00Z',
    version: 2,
  },
];

function mockJson(data) {
  return Promise.resolve({ ok: true, json: async () => data });
}

function mockError(message, status = 409, current = null) {
  return Promise.resolve({
    ok: false,
    status,
    json: async () => ({ detail: current ? { message, code: 'stale_appointment', current } : message }),
  });
}

describe('Harbor Health frontend', () => {
  let fetchMock;

  beforeEach(() => {
    fetchMock = vi.fn();
    global.fetch = fetchMock;
    HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('loads the patient dashboard and history', async () => {
    fetchMock.mockImplementation((input, init = {}) => {
      const url = String(input);
      const method = init.method ?? 'GET';
      if (url.endsWith('/demo-users')) {
        return mockJson(demoUsers);
      }
      if (method === 'GET' && url.includes('/appointments?role=patient&user_id=patient-alex')) {
        return mockJson(patientVisits);
      }
      if (method === 'GET' && url.includes('/appointments/apt-1/history?role=patient&user_id=patient-alex')) {
        return mockJson([
          {
            action: 'requested',
            actor_name: 'Alex Morgan',
            occurred_at: '2026-02-10T12:00:00Z',
            before: null,
            after: { ...patientVisits[0], start_at: '2030-02-12T10:00:00Z' },
          },
          {
            action: 'confirmed',
            actor_name: 'Dr. Riley Chen',
            occurred_at: '2026-02-11T09:00:00Z',
            before: { ...patientVisits[0], status: 'pending' },
            after: { ...patientVisits[0], status: 'confirmed' },
          },
        ]);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });

    render(<App />);

    await waitFor(() => expect(screen.getByText('Your appointments')).toBeInTheDocument());
    expect(screen.getByText('Annual check-up')).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: /History/i })[0]);

    await waitFor(() => expect(screen.getByText('Appointment history')).toBeInTheDocument());
    expect(screen.getByText('Requested')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Close history'));
    await waitFor(() => expect(screen.queryByText('Appointment history')).not.toBeInTheDocument());
  });

  it('submits a new request and switches the demo identity', async () => {
    fetchMock.mockImplementation((input, init = {}) => {
      const url = String(input);
      const method = init.method ?? 'GET';
      if (url.endsWith('/demo-users')) {
        return mockJson(demoUsers);
      }
      if (method === 'GET' && url.includes('/appointments?role=patient&user_id=patient-alex')) {
        return mockJson(patientVisits);
      }
      if (method === 'GET' && url.includes('/appointments?role=provider&user_id=provider-samira')) {
        return mockJson([]);
      }
      if (url.endsWith('/api/appointments') && method === 'POST') {
        return mockJson({
          id: 'appt-new',
          appointment_type: 'Follow-up',
          patient_id: 'patient-alex',
          patient_name: 'Alex Morgan',
          patient_email: 'alex.morgan@example.com',
          provider_id: 'provider-samira',
          provider_name: 'Dr. Samira Patel',
          reason: 'Discuss a follow-up',
          start_at: '2030-11-22T12:00:00Z',
          duration_minutes: 30,
          status: 'pending',
          version: 1,
          created_at: '2026-01-01T12:00:00Z',
          updated_at: '2026-01-01T12:00:00Z',
        });
      }
      if (method === 'GET' && url.includes('/appointments?role=patient&user_id=patient-alex')) {
        return mockJson([{
          ...patientVisits[0],
          id: 'appt-new',
          status: 'pending',
          start_at: '2030-11-22T12:00:00Z',
          appointment_type: 'Follow-up',
          provider_id: 'provider-samira',
          provider_name: 'Dr. Samira Patel',
          reason: 'Discuss a follow-up',
        }]);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });

    render(<App />);

    await waitFor(() => expect(screen.getByText('Your appointments')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/Preferred date & time/i), {
      target: { value: '2030-11-22T12:00' },
    });
    fireEvent.change(screen.getByLabelText(/Choose a provider/i), {
      target: { value: 'provider-samira' },
    });
    fireEvent.change(screen.getByLabelText(/What can we help with\?/i), {
      target: { value: 'Discuss a follow-up' },
    });

    fireEvent.click(screen.getByRole('button', { name: /Send request/i }));

    await waitFor(() => expect(screen.getByText('Your appointment request has been sent to the care team.')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Switch user role'), {
      target: { value: 'provider-samira' },
    });

    await waitFor(() => expect(screen.getByText('Care schedule')).toBeInTheDocument());
    expect(screen.getByText('Provider workspace')).toBeInTheDocument();
  });

  it('confirms, reschedules and handles stale conflicts', async () => {
    fetchMock.mockImplementation((input, init = {}) => {
      const url = String(input);
      const method = init.method ?? 'GET';
      if (url.endsWith('/demo-users')) {
        return mockJson(demoUsers);
      }
      if (method === 'GET' && url.includes('/appointments?role=provider&user_id=provider-riley')) {
        return mockJson(providerVisits);
      }
      if (url.includes('/appointments/apt-2')) {
        if (method === 'PATCH') {
          const body = JSON.parse(init.body || '{}');
          if (body.action === 'confirm') {
            return mockJson({
              ...providerVisits[0],
              status: 'confirmed',
              version: 3,
            });
          }
          if (body.action === 'reschedule') {
            return mockJson({
              ...providerVisits[0],
              status: 'confirmed',
              start_at: '2030-03-06T09:00:00Z',
              version: 4,
            });
          }
        }
      }
      if (method === 'GET' && url.includes('/appointments/apt-2/history?role=provider&user_id=provider-riley')) {
        return mockJson([
          { action: 'requested', actor_name: 'Jamie Lee', occurred_at: '2026-02-10T12:00:00Z', before: null, after: { ...providerVisits[0] } },
          { action: 'confirmed', actor_name: 'Dr. Riley Chen', occurred_at: '2026-02-11T11:00:00Z', before: { ...providerVisits[0], status: 'pending' }, after: { ...providerVisits[0], status: 'confirmed' } },
        ]);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });

    render(<App />);

    fireEvent.change(screen.getByLabelText('Switch user role'), {
      target: { value: 'provider-riley' },
    });

    await waitFor(() => expect(screen.getByText('Care schedule')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Confirm/i }));
    await waitFor(() => expect(screen.getByText('Appointment confirmed. A patient notification is queued.')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Reschedule/i }));
    const modal = document.querySelector('.reschedule-modal');
    expect(modal).toBeInTheDocument();

    fireEvent.mouseDown(document.querySelector('.modal-backdrop'), {
      target: document.querySelector('.modal-backdrop'),
    });
    expect(document.querySelector('.modal-backdrop')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Reschedule/i }));
    fireEvent.change(screen.getByLabelText(/New date & time/i), {
      target: { value: '2030-03-06T09:00' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Save new time/i }));
    await waitFor(() => expect(screen.getByText('Appointment time updated. The change is recorded in its history.')).toBeInTheDocument());
  });

  it('handles load, dismissal and stale errors', async () => {
    fetchMock.mockImplementation((input, init = {}) => {
      const url = String(input);
      const method = init.method ?? 'GET';
      if (url.endsWith('/demo-users')) {
        return mockError('Load failed');
      }
      if (method === 'GET' && url.includes('/appointments?role=patient&user_id=patient-alex')) {
        return mockError('Could not load appointments', 500);
      }
      if (method === 'PATCH' && url.includes('/appointments/apt-1')) {
        return mockError('This appointment changed while you were viewing it. The latest version is shown.', 409, {
          status: 'confirmed',
          id: 'apt-1',
          version: 3,
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });

    render(<App />);

    await waitFor(() => expect(screen.getByText('Load failed')).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText('Dismiss error'));
    await waitFor(() => expect(screen.queryByText('Load failed')).not.toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Switch user role'), {
      target: { value: 'missing-user' },
    });
    expect(screen.queryByText('This appointment changed while you were viewing it. The latest version is shown.')).not.toBeInTheDocument();
  });

  it('covers cancel, history failures, and non-stale update errors', async () => {
    fetchMock.mockImplementation((input, init = {}) => {
      const url = String(input);
      const method = init.method ?? 'GET';
      if (url.endsWith('/demo-users')) {
        return mockJson(demoUsers);
      }
      if (method === 'GET' && url.includes('/appointments?role=patient&user_id=patient-alex')) {
        return mockJson([
          {
            ...patientVisits[0],
            id: 'cancel-me',
            status: 'confirmed',
            start_at: '2030-05-01T10:00:00Z',
          },
        ]);
      }
      if (method === 'PATCH' && url.includes('/appointments/cancel-me')) {
        return Promise.resolve({
          ok: false,
          status: 500,
          json: async () => ({ detail: 'Update failed unexpectedly' }),
        });
      }
      if (method === 'GET' && url.includes('/appointments/cancel-me/history?role=patient&user_id=patient-alex')) {
        return Promise.resolve({
          ok: false,
          status: 404,
          json: async () => ({ detail: 'History could not be loaded' }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });

    render(<App />);

    await waitFor(() => expect(screen.getByText('Annual check-up')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Cancel visit/i }));
    await waitFor(() => expect(screen.getByText('Update failed unexpectedly')).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole('button', { name: /History/i })[0]);
    await waitFor(() => expect(screen.getByText('History could not be loaded')).toBeInTheDocument());
  });

  it('covers the polling interval callback and submit error handling', async () => {
    vi.spyOn(window, 'setInterval').mockImplementation((callback) => {
      callback();
      return 1;
    });

    fetchMock.mockImplementation((input, init = {}) => {
      const url = String(input);
      const method = init.method ?? 'GET';
      if (url.endsWith('/demo-users')) {
        return mockJson(demoUsers);
      }
      if (method === 'GET' && url.includes('/appointments?role=patient&user_id=patient-alex')) {
        return mockJson(patientVisits);
      }
      if (url.endsWith('/api/appointments') && method === 'POST') {
        return Promise.resolve({
          ok: false,
          status: 422,
          json: async () => ({ detail: 'Request rejected' }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });

    render(<App />);
    expect(fetchMock.mock.calls.length).toBeGreaterThan(1);

    fireEvent.change(screen.getByLabelText(/Preferred date & time/i), {
      target: { value: '2030-06-01T12:00' },
    });
    fireEvent.change(screen.getByLabelText(/Choose a provider/i), {
      target: { value: 'provider-riley' },
    });
    fireEvent.change(screen.getByLabelText(/What can we help with\?/i), {
      target: { value: 'Delayed follow-up' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Send request/i }));

    await waitFor(() => expect(screen.getByText('Request rejected')).toBeInTheDocument());
  });

  it('covers success cancel and stale conflict without current snapshot', async () => {
    fetchMock.mockImplementation((input, init = {}) => {
      const url = String(input);
      const method = init.method ?? 'GET';
      if (url.endsWith('/demo-users')) {
        return mockJson(demoUsers);
      }
      if (method === 'GET' && url.includes('/appointments?role=patient&user_id=patient-alex')) {
        return mockJson([
          { ...patientVisits[0], id: 'cancel-ok', status: 'confirmed', start_at: '2030-05-02T10:00:00Z' },
          { ...patientVisits[0], id: 'stale-no-current', status: 'confirmed', start_at: '2030-05-03T10:00:00Z' },
        ]);
      }
      if (method === 'PATCH' && url.includes('/appointments/cancel-ok')) {
        return mockJson({ ...patientVisits[0], id: 'cancel-ok', status: 'cancelled', start_at: '2030-05-02T10:00:00Z', version: 2 });
      }
      if (method === 'PATCH' && url.includes('/appointments/stale-no-current')) {
        return Promise.resolve({
          ok: false,
          status: 409,
          json: async () => ({ detail: 'Conflict without current payload' }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });

    render(<App />);
    await waitFor(() => expect(screen.getAllByRole('button', { name: /Cancel visit/i }).length).toBeGreaterThan(0));

    fireEvent.click(screen.getAllByRole('button', { name: /Cancel visit/i })[0]);
    await waitFor(() => expect(screen.getByText('Appointment cancelled.')).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole('button', { name: /Cancel visit/i })[1]);
    await waitFor(() => expect(screen.getByText('Conflict without current payload')).toBeInTheDocument());
  });

  it('covers stale conflict with the current snapshot payload', async () => {
    fetchMock.mockImplementation((input, init = {}) => {
      const url = String(input);
      const method = init.method ?? 'GET';
      if (url.endsWith('/demo-users')) {
        return mockJson(demoUsers);
      }
      if (method === 'GET' && url.includes('/appointments?role=patient&user_id=patient-alex')) {
        return mockJson([
          { ...patientVisits[0], id: 'stale-current', status: 'confirmed', start_at: '2030-07-01T10:00:00Z' },
        ]);
      }
      if (method === 'PATCH' && url.includes('/appointments/stale-current')) {
        return Promise.resolve({
          ok: false,
          status: 409,
          json: async () => ({
            detail: {
              code: 'stale_appointment',
              message: 'This appointment changed while you were viewing it. The latest version is shown.',
              current: { id: 'stale-current', status: 'confirmed', version: 4 },
            },
          }),
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });

    render(<App />);
    await waitFor(() => expect(screen.getByText('Annual check-up')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /Cancel visit/i }));

    await waitFor(() => {
      expect(screen.getByText('This appointment changed while you were viewing it. The latest details have been loaded; please review and try again.')).toBeInTheDocument();
    });
  });

  it('renders loading and empty states', async () => {
    fetchMock.mockImplementation((input, init = {}) => {
      const url = String(input);
      const method = init.method ?? 'GET';
      if (url.endsWith('/demo-users')) {
        return mockJson(demoUsers);
      }
      if (method === 'GET' && url.includes('/appointments?role=patient&user_id=patient-alex')) {
        return mockJson([]);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });

    render(<App />);
    expect(screen.getByText('Loading appointments…')).toBeInTheDocument();

    await waitFor(() => expect(screen.getByText('No visits just yet')).toBeInTheDocument());
    expect(screen.getByText('No visits just yet')).toBeInTheDocument();
  });
});
