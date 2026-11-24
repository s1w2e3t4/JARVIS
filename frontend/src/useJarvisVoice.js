
import { useCallback, useEffect, useRef, useState } from "react";

export function useJarvisVoice(onCommand, muted = false) {
  const [status, setStatus] = useState("off");
  const [transcript, setTranscript] = useState("");
  const [error, setError] = useState("");

  const recognitionRef = useRef(null);
  const enabledRef = useRef(false);
  const activeRef = useRef(false);
  const busyRef = useRef(false);
  const speakingRef = useRef(false);
  const callbackRef = useRef(onCommand);
  const restartRef = useRef(null);
  const statusRef = useRef("off");

  callbackRef.current = onCommand;
  const mutedRef = useRef(muted);
  mutedRef.current = muted;

  const updateStatus = useCallback((value) => {
    statusRef.current = value;
    setStatus(value);
  }, []);

  const scheduleRestart = useCallback(() => {
    clearTimeout(restartRef.current);

    if (!enabledRef.current || busyRef.current || speakingRef.current) {
      return;
    }

    restartRef.current = setTimeout(() => {
      if (!enabledRef.current || busyRef.current || speakingRef.current) {
        return;
      }

      try {
        recognitionRef.current?.start();
      } catch {
        // Ignore duplicate starts.
      }
    }, 500);
  }, []);

  useEffect(() => {
    const SpeechRecognition =
      window.SpeechRecognition || window.webkitSpeechRecognition;

    if (!SpeechRecognition) {
      setError("Speech recognition requires a supported browser.");
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.lang = "en-IN";
    recognition.continuous = false;
    recognition.interimResults = false;

    recognitionRef.current = recognition;

    recognition.onresult = async (event) => {
      const text = event.results[event.resultIndex][0].transcript.trim();
      if (!text) return;

      setTranscript(text);

      const normalized = text.toLowerCase();

      if (!activeRef.current) {
        const match = normalized.match(/\b(?:hey\s+)?jarvis\b[\s,!.?]*/);

        if (!match) return;

        activeRef.current = true;
        updateStatus("active");

        const command = text
          .slice(match.index + match[0].length)
          .trim();

        if (!command) return;

        busyRef.current = true;
        updateStatus("thinking");
        recognition.abort();

        try {
          const reply = await callbackRef.current(command);
          if (reply && enabledRef.current) {
            await speakReply(reply);
          }
        } finally {
          busyRef.current = false;
          if (enabledRef.current && !speakingRef.current) {
            updateStatus("active");
            scheduleRestart();
          }
        }

        return;
      }

      if (
        /\b(?:go to sleep|stop listening|sleep mode)\b/.test(normalized)
      ) {
        activeRef.current = false;
        updateStatus("standby");
        return;
      }

      busyRef.current = true;
      updateStatus("thinking");
      recognition.abort();

      try {
        const reply = await callbackRef.current(text);
        if (reply && enabledRef.current) {
          await speakReply(reply);
        }
      } finally {
        busyRef.current = false;
        if (enabledRef.current && !speakingRef.current) {
          updateStatus(activeRef.current ? "active" : "standby");
          scheduleRestart();
        }
      }
    };

    recognition.onerror = (event) => {
      if (
        event.error === "not-allowed" ||
        event.error === "service-not-allowed"
      ) {
        enabledRef.current = false;
        updateStatus("off");
        setError("Microphone or recognition permission denied.");
      } else if (
        event.error !== "no-speech" &&
        event.error !== "aborted"
      ) {
        setError(`Speech recognition: ${event.error}`);
      }
    };

    recognition.onend = () => {
      scheduleRestart();
    };

    return () => {
      enabledRef.current = false;
      clearTimeout(restartRef.current);
      recognition.abort();
      window.speechSynthesis?.cancel();
      recognitionRef.current = null;
    };
  }, [scheduleRestart, updateStatus]);

  function speakReply(text) {
    return new Promise((resolve) => {
      if (mutedRef.current || !("speechSynthesis" in window)) {
        resolve();
        return;
      }

      speakingRef.current = true;
      updateStatus("speaking");
      recognitionRef.current?.abort();

      window.speechSynthesis.cancel();

      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = "en-IN";
      utterance.rate = 1;
      utterance.pitch = 1;

      let finished = false;

      const finish = () => {
        if (finished) return;
        finished = true;
        speakingRef.current = false;
        resolve();
      };

      utterance.onend = finish;
      utterance.onerror = finish;

      window.speechSynthesis.speak(utterance);
    });
  }

  const start = useCallback(() => {
    if (!recognitionRef.current) {
      setError("Speech recognition is unavailable in this browser.");
      return;
    }

    enabledRef.current = true;
    activeRef.current = false;
    busyRef.current = false;
    setError("");
    updateStatus("standby");
    scheduleRestart();
  }, [scheduleRestart, updateStatus]);

  const stop = useCallback(() => {
    enabledRef.current = false;
    activeRef.current = false;
    busyRef.current = false;
    speakingRef.current = false;

    clearTimeout(restartRef.current);
    recognitionRef.current?.abort();
    window.speechSynthesis?.cancel();

    updateStatus("off");
  }, [updateStatus]);

  const speak = useCallback(async (text) => {
    await speakReply(text);
    if (enabledRef.current && !busyRef.current) {
      updateStatus(activeRef.current ? "active" : "standby");
      scheduleRestart();
    }
  }, [scheduleRestart, updateStatus]);

  return {
    status,
    transcript,
    error,
    start,
    stop,
    speak,
  };
}
