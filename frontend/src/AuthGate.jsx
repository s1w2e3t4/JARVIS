import { useEffect, useState } from 'react';
import AuthPage from './AuthPage';

export default function AuthGate({ children }) {
  const [email, setEmail] = useState(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState('');

  async function checkSession(signal) {
    setChecking(true);
    setError('');

    try {
      const response = await fetch('/api/account/session', {
        credentials: 'same-origin',
        headers: { 'X-Jarvis-Account': '1' },
        cache: 'no-store',
        signal
      });

      if (!response.ok) throw new Error('Session check failed');

      const data = await response.json();
      setEmail(data.email);
    } catch (err) {
      if (err.name !== 'AbortError') {
        setError('Unable to connect to JARVIS. Check that the backend is running.');
      }
    } finally {
      if (!signal?.aborted) setChecking(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    checkSession(controller.signal);
    return () => controller.abort();
  }, []);

  async function logout() {
    const response = await fetch('/api/account/logout', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-Jarvis-Account': '1' }
    });

    if (!response.ok) throw new Error('Logout failed');
    setEmail(null);
  }

  if (checking || error) {
    return (
      <main style={{
        minHeight: '100dvh',
        display: 'grid',
        placeItems: 'center',
        background: '#090a0c',
        color: '#dce7f8'
      }}>
        <div style={{ textAlign: 'center', padding: 24 }}>
          <img src="/jarvis.svg" width="48" alt="JARVIS" />
          <p>{error || 'Restoring your session...'}</p>
          {error && (
            <button onClick={() => checkSession()}>
              Try again
            </button>
          )}
        </div>
      </main>
    );
  }

  if (!email) {
    return <AuthPage onAuthenticated={setEmail} />;
  }

  return children({ email, logout });
}