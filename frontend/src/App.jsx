
import {
  useState, useCallback, useRef, useEffect
} from 'react';
import { Settings, X, Send } from 'lucide-react';
import { useJarvisVoice } from './useJarvisVoice';
import CosmicHorizon from "./CosmicHorizon";
import AuthGate from './AuthGate';
import './App.css';

const API_URL = 'http://127.0.0.1:8000';

export default function App() {
  return (
    <AuthGate>
      {({ email, logout }) => (
        <JarvisAssistant
          key={email}
          email={email}
          logout={logout}
        />
      )}
    </AuthGate>
  );
}

function JarvisAssistant({ email, logout }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [muted, setMuted] = useState(false);
  const [panel, setPanel] = useState('');
  const [problem, setProblem] = useState('');
  const [loggingOut, setLoggingOut] = useState(false);

  const busy = useRef(false);
  const end = useRef(null);

  const send = useCallback(async (text) => {
    text = text.trim();

    if (!text || busy.current) return null;

    busy.current = true;
    setLoading(true);
    setProblem('');

    setMessages(p => [
      ...p,
      { role: 'user', content: text }
    ]);

    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ message: text })
      });

      if (!response.ok) {
        throw Error(`HTTP ${response.status}`);
      }

      const data = await response.json();

      const reply =
        data.reply || 'I could not generate a response.';

      setMessages(p => [
        ...p,
        { role: 'assistant', content: reply }
      ]);

      return reply;

    } catch (e) {
      setProblem(
        `Backend unavailable: ${e.message}. Check that FastAPI is running.`
      );
      return null;

    } finally {
      busy.current = false;
      setLoading(false);
    }
  }, []);

  const { status, error, start, stop, speak } =
    useJarvisVoice(send, muted);

  const mode =
  status === 'speaking' ? 'speaking' :
  loading || status === 'thinking' ? 'thinking' :
  status === 'active' ? 'listening' :
  'idle';

  useEffect(() => {
    end.current?.scrollIntoView({
      block: 'nearest'
    });
  }, [messages, panel]);

  useEffect(() => {
    if (!panel) return;

    const close = e => {
      if (e.key === 'Escape') setPanel('');
    };

    window.addEventListener('keydown', close);

    return () =>
      window.removeEventListener('keydown', close);
  }, [panel]);

  async function submit(e) {
    e.preventDefault();

    if (loading || !input.trim()) return;

    const text = input;
    setInput('');

    const reply = await send(text);

    if (reply && !muted && status !== 'off') {
      await speak(reply);
    }
  }

  async function handleLogout() {
    setLoggingOut(true);
    setProblem('');

    try {
      stop();
      window.speechSynthesis?.cancel();
      await logout();
    } catch {
      setProblem('Unable to log out. Please try again.');
    } finally {
      setLoggingOut(false);
    }
  }

  return (
    <main className="jarvis-shell">

      <header className="jarvis-header">
        <span className="wordmark">
          J A R V I S
        </span>

        <button
          className="icon-button"
          aria-label="Settings"
          onClick={() =>
            setPanel(p => p ? '' : 'settings')
          }
        >
          <Settings size={20} />
        </button>
      </header>

      <section className="core-stage">
        <button
          className="core-trigger"
          aria-label={
            status === 'off'
              ? 'Activate hands-free voice'
              : 'JARVIS neural core'
          }
          onClick={() => {
            if (status === 'off') start();
          }}
        >
          <CosmicHorizon mode={mode} />
        </button>

        <span className="sr-only" role="status">
          {status}
        </span>

        {(error || problem) && (
          <div className="context-help" role="alert">
            {problem || error}

            <button onClick={() => setPanel('help')}>
              Help
            </button>
          </div>
        )}
      </section>

      {panel && (
        <aside
          className="settings-panel"
          aria-label={panel}
        >
          <div className="panel-heading">
            <h2>
              {panel === 'settings' ? 'Settings' :
               panel === 'chat' ? 'Conversation' :
               panel === 'account' ? 'Account' :
               'Help'}
            </h2>

            <button
              className="icon-button"
              aria-label="Close"
              onClick={() => setPanel('')}
            >
              <X size={19} />
            </button>
          </div>

          {panel === 'settings' && (
            <div className="menu">

              <button onClick={() => setPanel('account')}>
                Account <span>›</span>
              </button>

              <button onClick={() =>
                status === 'off' ? start() : stop()
              }>
                Hands-free voice
                <span>
                  {status === 'off' ? 'Off' : 'On'}
                </span>
              </button>

              <button onClick={() => setMuted(v => !v)}>
                Voice output
                <span>{muted ? 'Muted' : 'On'}</span>
              </button>

              <button onClick={() => setPanel('chat')}>
                Conversation & text input <span>›</span>
              </button>

              <button onClick={() => setPanel('help')}>
                Help & commands <span>›</span>
              </button>

            </div>
          )}

          {panel === 'account' && (
            <div className="menu">

              <p>Signed in as</p>

              <strong style={{ overflowWrap: 'anywhere' }}>
                {email}
              </strong>

              <button
                disabled={loggingOut}
                onClick={handleLogout}
              >
                {loggingOut ? 'Signing out...' : 'Sign out'}
              </button>

              <button onClick={() => setPanel('settings')}>
                Back to settings
              </button>

            </div>
          )}

          {panel === 'help' && (
            <div className="menu">
              <p>
                Click the neural sphere once to activate
                voice and allow microphone access.
                Say “Hey Jarvis”, then speak naturally.
                Say “go to sleep” to return to standby.
              </p>

              <p>
                Voice recognition works best in a
                supported browser such as Chrome.
                You can turn off the microphone in Settings.
              </p>

              <button onClick={() => setPanel('settings')}>
                Back
              </button>
            </div>
          )}

          {panel === 'chat' && (
            <>
              <div className="chat-history">
                {messages.length === 0 && (
                  <p>No messages yet.</p>
                )}

                {messages.map((m, i) => (
                  <div
                    className={`chat-message ${m.role}`}
                    key={i}
                  >
                    <small>
                      {m.role === 'user' ? 'You' : 'JARVIS'}
                    </small>
                    <p>{m.content}</p>
                  </div>
                ))}

                <div ref={end} />
              </div>

              <form
                className="chat-form"
                onSubmit={submit}
              >
                <input
                  aria-label="Message"
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  placeholder="Message JARVIS"
                />

                <button
                  aria-label="Send"
                  disabled={loading || !input.trim()}
                >
                  <Send size={18} />
                </button>
              </form>

              <button
                className="clear-chat"
                disabled={loading}
                onClick={() => setMessages([])}
              >
                Clear visible history
              </button>
            </>
          )}
        </aside>
      )}
    </main>
  );
}
