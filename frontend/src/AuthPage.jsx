
import { useState } from 'react';
import { ArrowRight, Eye, EyeOff } from 'lucide-react';
import NeuralCore from './NeuralCore';
import './AuthPage.css';

export default function AuthPage({ onAuthenticated }) {
  const [register, setRegister] = useState(false);
  const [visible, setVisible] = useState(false);
  const [remember, setRemember] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  async function submit(event) {
    event.preventDefault();
    const data = Object.fromEntries(
      new FormData(event.currentTarget)
    );

    setPending(true);
    setError('');

    try {
      const response = await fetch(
        `/api/account/${register ? 'register' : 'login'}`,
        {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/json',
            'X-Jarvis-Account': '1'
          },
          body: JSON.stringify({
            ...data,
            remember_me: remember
          })
        }
      );

      const result = await response.json();

      if (!response.ok) {
        throw new Error(
          typeof result.detail === 'string'
            ? result.detail
            : 'Please check your credentials.'
        );
      }

      onAuthenticated(result.email);
    } catch (err) {
      setError(
        err instanceof TypeError
          ? 'Unable to connect to JARVIS. Please try again.'
          : err.message
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-brand">
        <div className="auth-logo">
          <img src="/jarvis.svg" alt="" />
          <span>JARVIS</span>
        </div>

        <div className="auth-visual">
          <NeuralCore mode="idle" />
        </div>

        <div className="auth-brand-copy">
          <span className="auth-eyebrow">
            PERSONAL INTELLIGENCE SYSTEM
          </span>
          <h1>Intelligence,<br />made personal.</h1>
          <p>
            A more natural way to interact
            with technology.
          </p>
        </div>

        <footer>© 2026 JARVIS</footer>
      </section>

      <section className="auth-content">
        <div className="auth-form-container">
          <span className="auth-eyebrow">
            YOUR WORKSPACE
          </span>

          <h2>
            {register ? 'Create your account.' : 'Welcome back.'}
          </h2>

          <p className="auth-subtitle">
            {register
              ? 'Begin your experience with JARVIS.'
              : 'Sign in to your JARVIS account.'}
          </p>

          <form onSubmit={submit}>
            <label htmlFor="auth-email">Email address</label>
            <input
              id="auth-email"
              name="email"
              type="email"
              placeholder="you@example.com"
              autoComplete="username"
              required
            />

            <label htmlFor="auth-password">Password</label>

            <div className="auth-password">
              <input
                id="auth-password"
                name="password"
                type={visible ? 'text' : 'password'}
                placeholder="Enter your password"
                autoComplete={
                  register ? 'new-password' : 'current-password'
                }
                minLength={register ? 15 : undefined}
                maxLength={128}
                required
              />

              <button
                type="button"
                aria-label={visible ? 'Hide password' : 'Show password'}
                onClick={() => setVisible(!visible)}
              >
                {visible ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>

            {register && (
              <p className="auth-hint">
                Use at least 15 characters.
              </p>
            )}

            <label className="auth-remember">
              <input
                type="checkbox"
                checked={remember}
                onChange={e => setRemember(e.target.checked)}
              />
              Keep me signed in
            </label>

            {error && (
              <p className="auth-error" role="alert">
                {error}
              </p>
            )}

            <button
              className="auth-submit"
              disabled={pending}
            >
              {pending
                ? 'Please wait...'
                : register ? 'Create account' : 'Sign in'}

              {!pending && <ArrowRight size={18} />}
            </button>
          </form>

          <div className="auth-switch">
            {register
              ? 'Already have an account?'
              : 'New to JARVIS?'}

            <button
              disabled={pending}
              onClick={() => {
                setRegister(!register);
                setError('');
                setVisible(false);
              }}
            >
              {register ? 'Sign in' : 'Create account'}
            </button>
          </div>
        </div>
      </section>
    </main>
  );
}
