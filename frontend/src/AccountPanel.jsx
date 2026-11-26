import { useEffect, useState } from 'react';

async function accountRequest(action, options = {}) {
  const response = await fetch(`/api/account/${action}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Jarvis-Account': '1',
    },
  });
  if (response.status === 204) return { email: null };
  const data = await response.json();
  if (!response.ok) {
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Check your email and password.');
  }
  return data;
}

export default function AccountPanel({ onBack }) {
  const [email, setEmail] = useState(null);
  const [registration, setRegistration] = useState(false);
  const [pending, setPending] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    async function loadSession() {
      try {
        const session = await accountRequest('session', { signal: controller.signal });
        setEmail(session.email);
      } catch {
        if (!controller.signal.aborted) setError('Could not check your session. Check that FastAPI is running.');
      } finally {
        if (!controller.signal.aborted) setPending(false);
      }
    }
    loadSession();
    return () => controller.abort();
  }, []);

  async function submit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const action = email ? 'logout' : registration ? 'register' : 'login';
    const body = email ? undefined : JSON.stringify(Object.fromEntries(new FormData(form)));
    setPending(true);
    setError('');
    try {
      const session = await accountRequest(action, { method: 'POST', body });
      form.reset();
      setEmail(session.email);
      setRegistration(false);
    } catch (error) {
      setError(error instanceof SyntaxError || error instanceof TypeError
        ? 'Could not reach the account service. Check that FastAPI is running.'
        : error.message);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="menu">
      <form className="account-form" onSubmit={submit}>
        {email ? (
          <>
            <p>Signed in as <strong>{email}</strong></p>
            <button disabled={pending}>Log out</button>
          </>
        ) : (
          <>
            <label>Email<input type="email" name="email" autoComplete="username" required /></label>
            <label>
              Password
              <input type="password" name="password" autoComplete={registration ? 'new-password' : 'current-password'}
                minLength={registration ? 15 : undefined} maxLength={128} required />
            </label>
            {registration && <p>Use at least 15 characters.</p>}
            <button disabled={pending}>{registration ? 'Create account' : 'Log in'}</button>
          </>
        )}
      </form>
      {pending && <p role="status">Contacting account service…</p>}
      {error && <p role="alert">{error}</p>}
      {!email && (
        <button type="button" disabled={pending} onClick={() => {
          setRegistration(!registration);
          setError('');
        }}>
          {registration ? 'Back to log in' : 'Create an account'}
        </button>
      )}
      <button type="button" onClick={onBack}>Back to settings</button>
    </div>
  );
}
