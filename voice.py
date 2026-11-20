"""
JARVIS voice input and output.

Output : Windows PowerShell / SAPI (System.Speech), with an optional
         pyttsx3 fallback if PowerShell speech fails.
Input  : SpeechRecognition + microphone (Google web recognizer).

Speech output design
--------------------
* The text to speak is NEVER pasted into a PowerShell command line.
  It is written to a UTF-8 temporary file and the (static) PowerShell
  script reads that file. Apostrophes, quotes, newlines, '$', backticks
  and Unicode therefore cannot break the command.
* Long answers are split into sentence-sized chunks (one per line) that are
  all spoken by ONE PowerShell process, so there are no gaps and no
  per-chunk start-up cost.
* speak() returns True only if speech actually succeeded. When it fails,
  the response is still printed and a clear note is shown.
"""

import base64
import logging
import os
import re
import subprocess
import sys
import tempfile
import time

import speech_recognition as sr

import config

log = logging.getLogger("jarvis.voice")


# ==========================================
# SAFE CONSOLE OUTPUT
# ==========================================

def _safe_print(*parts):
    """print() that can never crash on a Windows console encoding."""
    try:
        print(*parts)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        text = " ".join(str(part) for part in parts)
        print(text.encode(encoding, errors="replace").decode(encoding))


# ==========================================
# TEXT PREPARATION FOR SPEECH
# ==========================================

def _clean_for_speech(text: str) -> str:
    """Remove formatting that sounds unnatural when spoken."""

    text = str(text)

    # Markdown code fences
    text = re.sub(r"```.*?```", " code block ", text, flags=re.DOTALL)

    # Inline code ticks, emphasis and headings
    text = text.replace("`", "")
    text = text.replace("**", "").replace("__", "").replace("*", "")
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)

    # Bullet points become natural pauses
    text = re.sub(r"^\s*[-\u2022]\s*", ". ", text, flags=re.MULTILINE)

    # "find_file" is spoken as "find file"
    text = re.sub(r"(?<=\w)_(?=\w)", " ", text)

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def _split_text(text: str, max_length: int = None) -> list:
    """
    Split text into speech-sized chunks WITHOUT losing or repeating words.

    Splits on sentence ends first, then on word boundaries. A chunk never
    cuts a word in half.
    """

    if max_length is None:
        max_length = config.SPEECH_CHUNK_CHARS

    text = str(text).strip()

    if not text:
        return []

    if len(text) <= max_length:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)

    chunks = []
    current = ""

    def flush():
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    for sentence in sentences:
        sentence = sentence.strip()

        if not sentence:
            continue

        if len(sentence) > max_length:
            # Over-long sentence: split on word boundaries.
            flush()

            for word in sentence.split():
                if len(word) > max_length:
                    # A single "word" longer than a chunk (rare): hard split.
                    flush()
                    for start in range(0, len(word), max_length):
                        chunks.append(word[start:start + max_length])
                    continue

                if current and len(current) + 1 + len(word) > max_length:
                    flush()

                current = f"{current} {word}".strip()

            continue

        if current and len(current) + 1 + len(sentence) > max_length:
            flush()

        current = f"{current} {sentence}".strip()

    flush()

    return chunks


# ==========================================
# SAPI SPEECH (WINDOWS POWERSHELL)
# ==========================================

# Static script: contains no user text at all.
_POWERSHELL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    try { $voice.SelectVoice($env:JARVIS_TTS_VOICE) } catch {}
    $voice.Rate = [int]$env:JARVIS_TTS_RATE
    $voice.Volume = [int]$env:JARVIS_TTS_VOLUME
    $text = [System.IO.File]::ReadAllText(
        $env:JARVIS_TTS_FILE, [System.Text.Encoding]::UTF8)
    foreach ($line in ($text -split "`n")) {
        if ($line.Trim().Length -gt 0) { $voice.Speak($line) }
    }
}
finally {
    $voice.Dispose()
}
"""


def _powershell_command() -> list:
    encoded = base64.b64encode(
        _POWERSHELL_SCRIPT.encode("utf-16le")
    ).decode("ascii")

    return [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-EncodedCommand", encoded,
    ]


def _speak_with_powershell(chunks: list) -> bool:
    """Speak all chunks in a single PowerShell process."""

    path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".txt",
            prefix="jarvis_tts_",
            delete=False,
        ) as handle:
            handle.write("\n".join(chunks))
            path = handle.name

        environment = dict(os.environ)
        environment["JARVIS_TTS_FILE"] = path
        environment["JARVIS_TTS_VOICE"] = str(config.VOICE_NAME)
        environment["JARVIS_TTS_RATE"] = str(int(config.VOICE_RATE))
        environment["JARVIS_TTS_VOLUME"] = str(int(config.VOICE_VOLUME))

        timeout = (
            config.SPEECH_TIMEOUT_BASE_SECONDS
            + config.SPEECH_TIMEOUT_PER_CHUNK_SECONDS * len(chunks)
        )

        result = subprocess.run(
            _powershell_command(),
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

        if result.returncode != 0:
            log.warning(
                "PowerShell speech failed (code %s): %s",
                result.returncode,
                (result.stderr or "").strip()[:500],
            )
            return False

        return True

    except subprocess.TimeoutExpired:
        log.warning("PowerShell speech timed out.")
        return False

    except FileNotFoundError:
        log.warning("PowerShell was not found.")
        return False

    except Exception as error:
        log.warning("PowerShell speech error: %s", error)
        return False

    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass


def _speak_with_pyttsx3(chunks: list) -> bool:
    """Optional fallback if PowerShell speech is unavailable."""

    try:
        import pyttsx3
    except ImportError:
        return False

    try:
        engine = pyttsx3.init()

        for chunk in chunks:
            engine.say(chunk)

        engine.runAndWait()
        return True

    except Exception as error:
        log.warning("pyttsx3 speech error: %s", error)
        return False


def _speak_chunk(text) -> bool:
    """Speak one piece of text (kept for backwards compatibility)."""

    if not text or not str(text).strip():
        return True

    return _speak_chunks([str(text).strip()])


def _speak_chunks(chunks: list) -> bool:
    if not chunks:
        return True

    if _speak_with_powershell(chunks):
        return True

    return _speak_with_pyttsx3(chunks)


def speak(text: str) -> bool:
    """
    Print the COMPLETE response, then speak it.

    Returns True if speech succeeded, False if it failed. The written
    response is always shown in full either way.
    """

    text = str(text)

    _safe_print("JARVIS:", text)

    clean_text = _clean_for_speech(text)

    if not clean_text:
        return True

    chunks = _split_text(clean_text)

    succeeded = _speak_chunks(chunks)

    if not succeeded:
        _safe_print(
            "[Speech output failed - the response above was not spoken.]"
        )

    return succeeded


# ==========================================
# LISTEN
# ==========================================

_recognizer = sr.Recognizer()
_recognizer.dynamic_energy_threshold = True
_recognizer.pause_threshold = 0.8

_calibrated = False
_quiet_results = 0
_last_service_warning = 0.0


def listen(timeout: int = None, phrase_time_limit: int = None) -> str:
    """
    Listen once and return lower-case text ('' if nothing was understood).

    Raises OSError / AttributeError if there is no usable microphone, so
    that the caller can count repeated failures instead of looping forever.

    Noise calibration happens once (and again after many empty results)
    instead of before every phrase. Calibrating every time delayed the
    start of recording and could clip the first word - usually "Jarvis".
    """

    global _calibrated, _quiet_results, _last_service_warning

    if timeout is None:
        timeout = config.LISTEN_TIMEOUT

    if phrase_time_limit is None:
        phrase_time_limit = config.LISTEN_PHRASE_LIMIT

    with sr.Microphone() as source:

        if not _calibrated:
            _recognizer.adjust_for_ambient_noise(
                source,
                duration=config.AMBIENT_CALIBRATION_SECONDS,
            )
            _calibrated = True

        try:
            audio = _recognizer.listen(
                source,
                timeout=timeout,
                phrase_time_limit=phrase_time_limit,
            )

        except sr.WaitTimeoutError:
            _note_quiet_result()
            return ""

    try:
        text = _recognizer.recognize_google(audio)

    except sr.UnknownValueError:
        _note_quiet_result()
        return ""

    except sr.RequestError as error:
        log.warning("Speech recognition service error: %s", error)

        # Warn at most once a minute instead of on every loop.
        now = time.time()

        if now - _last_service_warning > 60:
            _last_service_warning = now
            speak(
                "I can't reach the speech recognition service "
                "right now."
            )

        return ""

    except Exception as error:
        log.warning("Speech recognition error: %s", error)
        return ""

    _quiet_results = 0

    _safe_print("You:", text)

    return text.lower()


def _note_quiet_result():
    """Recalibrate for room noise after a long run of silence."""

    global _calibrated, _quiet_results

    _quiet_results += 1

    if _quiet_results >= config.RECALIBRATE_EVERY_N_FAILURES:
        _quiet_results = 0
        _calibrated = False
