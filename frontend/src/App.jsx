
import { useState, useCallback, useRef, useEffect } from "react";
import {
  Bot,
  Power,
  Mic,
  MicOff,
  AudioLines,
  Brain,
  Send,
  Plus,
  Volume2,
  VolumeX,
  Sparkles
} from "lucide-react";

import { useJarvisVoice } from "./useJarvisVoice";
import "./App.css";

const API_URL = "http://127.0.0.1:8000";

const welcome = {
  role: "assistant",
  content: "JARVIS initialized. Enable hands-free mode and say Hey Jarvis.",
};

export default function App() {
  const [messages, setMessages] = useState([welcome]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [muted, setMuted] = useState(false);

  const messagesEndRef = useRef(null);
  const busyRef = useRef(false);

  const sendToJarvis = useCallback(async (message) => {
    const cleaned = message.trim();

    if (!cleaned || busyRef.current) return null;

    busyRef.current = true;
    setLoading(true);

    setMessages((prev) => [
      ...prev,
      { role: "user", content: cleaned },
    ]);

    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ message: cleaned }),
      });

      if (!response.ok) {
        throw new Error(`Backend error: ${response.status}`);
      }

      const data = await response.json();
      const reply = data.reply || "I couldn't generate a response.";

      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: reply },
      ]);

      return reply;
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `Connection error: ${error.message}`,
        },
      ]);

      return null;
    } finally {
      busyRef.current = false;
      setLoading(false);
    }
  }, []);

  const mutedRef = useRef(false);

  const handleVoiceCommand = useCallback(async (command) => {
    return await sendToJarvis(command);
  }, [sendToJarvis]);

  const {
    status,
    transcript,
    error,
    start,
    stop,
    speak,
  } = useJarvisVoice(handleVoiceCommand);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      behavior: "smooth",
    });
  }, [messages, loading]);

  const toggleMute = () => {
    mutedRef.current = !mutedRef.current;
    setMuted(mutedRef.current);

    if (mutedRef.current) {
      window.speechSynthesis?.cancel();
    }
  };

  const handleTextSubmit = async (e) => {
    e.preventDefault();

    const message = input.trim();
    if (!message || loading) return;

    setInput("");

    const reply = await sendToJarvis(message);

    if (reply && !mutedRef.current && status !== "off") {
      await speak(reply);
    }
  };

  const newChat = () => {
    stop();
    setMessages([welcome]);
    setInput("");
  };

  const voiceOn = status !== "off";

  const statusText = {
    off: "Voice Offline",
    standby: 'Waiting for "Hey Jarvis"',
    active: "Listening to you",
    thinking: "Processing command",
    speaking: "JARVIS is speaking",
  };

  const statusIcon = {
    off: <MicOff size={23} />,
    standby: <Mic size={23} />,
    active: <AudioLines size={23} />,
    thinking: <Brain size={23} />,
    speaking: <Volume2 size={23} />,
  };

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <Bot size={29} />
          <span>JARVIS</span>
        </div>

        <button className="new-chat" onClick={newChat}>
          <Plus size={18} />
          New Chat
        </button>

        <div className="sidebar-bottom">
          <Sparkles size={18} />
          <span>Hybrid Voice Assistant</span>
        </div>
      </aside>

      <main className="main">
        <header className="header">
          <div>
            <h2>J.A.R.V.I.S</h2>
            <p>Intelligent Voice Assistant</p>
          </div>

          <div className="header-actions">
            <button
              className="voice-toggle"
              onClick={toggleMute}
              title={muted ? "Unmute" : "Mute"}
            >
              {muted ? <VolumeX size={20} /> : <Volume2 size={20} />}
            </button>

            <span className={`status-indicator ${voiceOn ? "online" : ""}`}>
              ● {voiceOn ? "Voice Enabled" : "Voice Disabled"}
            </span>
          </div>
        </header>

        <section className="voice-dashboard">
          <div className={`jarvis-orb ${status}`}>
            <div className="orb-ring ring-one" />
            <div className="orb-ring ring-two" />
            <div className="orb-ring ring-three" />
            <div className="orb-center">
              {statusIcon[status]}
            </div>
          </div>

          <h1>{statusText[status]}</h1>

          <p className="voice-subtitle">
            {voiceOn
              ? "Speak naturally. No microphone button needed."
              : "Enable hands-free mode to begin."}
          </p>

          <div className="voice-actions">
            {!voiceOn ? (
              <button className="enable-button" onClick={start}>
                <Power size={18} />
                Enable Hands-Free
              </button>
            ) : (
              <button className="disable-button" onClick={stop}>
                <Power size={18} />
                Disable Hands-Free
              </button>
            )}
          </div>

          {transcript && (
            <div className="transcript">
              <span>LAST HEARD</span>
              <p>{transcript}</p>
            </div>
          )}

          {error && <p className="voice-error">{error}</p>}
        </section>

        <section className="messages">
          {messages.map((message, index) => (
            <div
              key={index}
              className={`message ${message.role}`}
            >
              {message.role === "assistant" && (
                <div className="avatar">
                  <Bot size={20} />
                </div>
              )}

              <div className="bubble">
                {message.content}
              </div>
            </div>
          ))}

          {loading && (
            <div className="message assistant">
              <div className="avatar">
                <Brain size={20} />
              </div>
              <div className="bubble">
                JARVIS is thinking...
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </section>

        <form className="composer" onSubmit={handleTextSubmit}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Type a message or speak to JARVIS..."
          />

          <button
            type="submit"
            disabled={!input.trim() || loading}
          >
            <Send size={20} />
          </button>
        </form>
      </main>
    </div>
  );
}
