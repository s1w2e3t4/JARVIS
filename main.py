"""
JARVIS - Entry Point

Handles:
- Wake word detection and removal
- Voice input
- System commands (system_actions.py)
- AI conversation (brain.py)
- Conversation memory and conversation reset
- Exit

Wake-word rules
---------------
A sentence is for JARVIS only if
  * it STARTS with the wake word (optionally after "hey", "ok" ...):
        "jarvis open notepad", "hey jarvis, what is a list"
  * or it ENDS with the wake word and what comes before it is clearly a
    request:  "open notepad jarvis"
  * or it starts with an ambiguous mis-hearing ("java", "javascript") that
    is followed by a recognisable request:  "java open notepad"

So "good boy, jarvis", "I was telling a friend about jarvis" and
"java is a programming language" do NOT activate JARVIS.
"""

import logging
import logging.handlers
import os
import re
import sys
import time

import config
import system_actions
from brain import ask_ai, reset_conversation
from system_actions import execute_command
from voice import listen, speak

log = logging.getLogger("jarvis.main")


# ==========================================
# WAKE WORD
# ==========================================

# A request must begin with one of these for the ambiguous aliases
# ("java") and for a wake word placed at the END of a sentence.
COMMAND_STARTS = [
    "open ", "launch ", "start ", "search ", "find ", "read ", "explain ",
    "tell me ", "what is ", "what are ", "what does ", "what's ", "who is ",
    "how do ", "how does ", "why ", "show ", "take ", "capture ", "reset ",
    "clear ", "forget ", "goodbye", "good bye", "exit", "quit", "stop",
    "hello", "help", "calculate ", "play ", "turn on ", "turn off ",
    "summarize ", "summarise ", "describe ", "list ", "run ", "load ",
    "can you ", "could you ", "please ",
]

# Kept as an alias of the original name.
JAVA_COMMAND_STARTS = COMMAND_STARTS


def _alternation(words):
    return "|".join(
        re.escape(word) for word in sorted(words, key=len, reverse=True)
    )


_GREETING = r"(?:(?:" + _alternation(config.WAKE_GREETINGS) + r")[\s,]+)?"
_STRONG = _alternation(config.WAKE_WORDS)
_AMBIGUOUS = _alternation(config.AMBIGUOUS_WAKE_WORDS)
_PUNCT = " ,.!?:;-"

_LEADING_STRONG = re.compile(
    r"^\s*" + _GREETING + r"(?P<wake>" + _STRONG + r")(?![\w'])"
    r"[\s,.!?:;-]*(?P<rest>.*)$",
    re.DOTALL,
)

_LEADING_AMBIGUOUS = re.compile(
    r"^\s*" + _GREETING + r"(?P<wake>" + _AMBIGUOUS + r")(?![\w'])"
    r"[\s,.!?:;-]*(?P<rest>.*)$",
    re.DOTALL,
)

_TRAILING_STRONG = re.compile(
    r"^(?P<rest>.+?)[\s,]+(?:please[\s,]+)?(?P<wake>" + _STRONG + r")"
    r"[\s.!?]*$",
    re.DOTALL,
)


def looks_like_request(text):
    """True if text begins with a recognisable assistant request."""
    text = text.strip().lower()

    for start in COMMAND_STARTS:
        word = start.strip()

        if re.match(re.escape(word) + r"(?!\w)", text):
            return True

    return False


def extract_command(text):
    """
    Return the command that follows the wake word, or None.

    "" means the wake word was said on its own.
    """
    if not text:
        return None

    text = str(text).lower().strip()

    match = _LEADING_STRONG.match(text)

    if match:
        return match.group("rest").strip(_PUNCT)

    match = _LEADING_AMBIGUOUS.match(text)

    if match and looks_like_request(match.group("rest")):
        return match.group("rest").strip(_PUNCT)

    match = _TRAILING_STRONG.match(text)

    if match and looks_like_request(match.group("rest")):
        return match.group("rest").strip(_PUNCT)

    return None


def find_wake_word(text):
    """Compatibility helper: the wake word that was used, or None."""
    if extract_command(text) is None:
        return None

    for pattern in (_LEADING_STRONG, _LEADING_AMBIGUOUS, _TRAILING_STRONG):
        match = pattern.match(str(text).lower().strip())

        if match:
            return match.group("wake")

    return None


# ==========================================
# RESET COMMAND DETECTION
# ==========================================

_RESET_PHRASES = [
    "reset conversation",
    "reset the conversation",
    "clear conversation",
    "clear the conversation",
    "forget our conversation",
    "forget this conversation",
    "start a new conversation",
    "start new conversation",
    "new conversation",
]


def is_reset_command(command):
    """Check whether the user wants to reset memory."""
    command = str(command).lower().strip(_PUNCT)

    if len(command.split()) > 8:
        return False

    return any(phrase in command for phrase in _RESET_PHRASES)


# ==========================================
# EXIT COMMAND DETECTION
# ==========================================

_EXIT_FILLER = {
    "please", "now", "jarvis", "thanks", "thank", "you", "the", "program",
    "assistant", "system", "application", "app", "ok", "okay", "ok,",
}


def is_exit_command(command):
    """
    True only when the request IS an exit phrase ("goodbye", "exit now",
    "shut down", "goodbye jarvis").

    Sentences that merely contain an exit word - "how do I exit a loop",
    "what does exit mean in main.py" - are ordinary questions.
    """
    words = [
        word for word in re.findall(r"[a-z']+", str(command).lower())
        if word not in _EXIT_FILLER
    ]

    text = " ".join(words)

    return any(
        text == " ".join(str(word).lower().split())
        for word in config.EXIT_WORDS
    )


# ==========================================
# LOGGING
# ==========================================

def setup_logging():
    """Write a rolling log file next to the project (never to the screen)."""
    folder = os.path.dirname(os.path.abspath(__file__))

    handler = logging.handlers.RotatingFileHandler(
        os.path.join(folder, config.LOG_FILE),
        maxBytes=1_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )

    root = logging.getLogger("jarvis")
    root.setLevel(logging.INFO)

    if not root.handlers:
        root.addHandler(handler)


# ==========================================
# MAIN
# ==========================================

def handle_command(command):
    """
    Process one command (wake word already removed).

    Returns False if JARVIS should exit, True to keep running.
    """

    if not command:
        speak("Yes?")
        return True

    print("JARVIS command:", command)
    log.info("command: %s", command)

    if is_reset_command(command):
        reset_conversation()
        system_actions.reset_context()

        speak(
            "Conversation memory and file context have been "
            "cleared. We can start fresh."
        )
        return True

    if is_exit_command(command):
        speak("Goodbye. I will be here when you need me.")
        return False

    try:
        handled = execute_command(command)

    except Exception as error:
        log.exception("system command error")
        print("System command error:", error)

        speak(
            "I encountered an error while "
            "executing that system command."
        )
        return True

    if handled:
        return True

    try:
        answer = ask_ai(command)

        if answer:
            speak(answer)
        else:
            speak("I couldn't generate an answer. Please try again.")

    except Exception as error:
        log.exception("AI response error")
        print("AI response error:", error)

        speak("I encountered a problem while generating a response.")

    return True


def main():
    setup_logging()

    # A Windows console can reject some characters; never crash on them.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass

    speak("Hello. I am Jarvis. My systems are online.")

    listen_errors = 0

    while True:

        try:
            heard = listen()

        except KeyboardInterrupt:
            speak("Goodbye.")
            break

        except Exception as error:
            listen_errors += 1
            log.warning("listening error: %s", error)
            print("Listening error:", error)

            if listen_errors >= config.MAX_CONSECUTIVE_LISTEN_ERRORS:
                speak(
                    "I can't use the microphone. Please check that it is "
                    "connected and allowed in Windows privacy settings, "
                    "then start me again."
                )
                break

            time.sleep(min(listen_errors, 3))
            continue

        listen_errors = 0

        if not heard:
            continue

        heard = heard.lower().strip()

        command = extract_command(heard)

        if command is None:
            # Without the wake word, only an answer to JARVIS's own
            # question ("which one?") is accepted.
            if system_actions.has_pending():
                system_actions.handle_pending_reply(heard)

            continue

        try:
            keep_running = handle_command(command)
        except KeyboardInterrupt:
            speak("Goodbye.")
            break

        if not keep_running:
            break


# ==========================================
# START
# ==========================================

if __name__ == "__main__":
    main()
