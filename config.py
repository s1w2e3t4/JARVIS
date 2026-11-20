"""
JARVIS central settings.

Change behaviour here instead of hunting through every file.
Every other module reads its folders, extensions, limits and model name
from this file, so nothing is hard-coded twice.
"""

import os

# ==========================================
# AI MODEL (unchanged from the original project)
# ==========================================

# Ollama model used for open-ended questions.
# Must already be pulled:  ollama pull gemma3:1b
MODEL = "gemma3:1b"

# Context window requested from Ollama. Ollama's default window is small and
# silently drops the START of a long prompt, which is what made file
# explanations lose the source code. gemma3:1b supports far more than this.
NUM_CTX = 8192

# Low temperature for answers that must stay faithful to a file's contents.
FILE_ANSWER_TEMPERATURE = 0.1

# Maximum number of chat messages (user + assistant) kept as memory.
MAX_HISTORY_MESSAGES = 20

# ==========================================
# WAKE WORD AND EXIT WORDS
# ==========================================

# Kept for compatibility with the original project.
WAKE_WORD = "jarvis"

# Speech-recognition variations that are always accepted as the wake word
# (only when they appear at the START of what was heard, or as the last
# word of a clear command - see main.py).
WAKE_WORDS = [
    "jarvis's",
    "jarvis",
    "javis",
    "jervis",
    "javas",
]

# Words that are often heard instead of "Jarvis" but are also ordinary words.
# They count as a wake word ONLY at the start of the sentence AND when a
# recognisable request follows (for example "java open notepad").
AMBIGUOUS_WAKE_WORDS = [
    "javascript",
    "java script",
    "java",
]

# Optional greeting words allowed before the wake word ("hey jarvis ...").
WAKE_GREETINGS = ["hey", "hi", "hello", "ok", "okay", "yo", "hey there"]

# Words that end the program when they are the request itself.
EXIT_WORDS = ["goodbye", "good bye", "exit", "quit", "shutdown", "shut down"]

# How long (seconds) JARVIS waits for an answer to its own clarification
# question, during which the wake word is not required.
PENDING_QUESTION_SECONDS = 60

# ==========================================
# FOLDERS
# ==========================================

HOME = os.path.expanduser("~")


def _existing_variants(folder_name):
    """
    Return the real folder(s) for a standard Windows folder name.

    On many PCs OneDrive redirects Documents/Desktop/Pictures to
    ~/OneDrive/<name>, so both locations are checked when they exist.
    """
    candidates = [
        os.path.join(HOME, folder_name),
        os.path.join(HOME, "OneDrive", folder_name),
    ]
    found = [path for path in candidates if os.path.isdir(path)]
    # Keep the plain location even if missing, so JARVIS can report it.
    return found or [candidates[0]]


# Folders JARVIS is allowed to search and open (name -> list of paths).
FOLDER_PATHS = {
    "documents": _existing_variants("Documents"),
    "downloads": _existing_variants("Downloads"),
    "desktop": _existing_variants("Desktop"),
    "pictures": _existing_variants("Pictures"),
}

# Flat list used by the file search.
SEARCH_FOLDERS = [
    path for paths in FOLDER_PATHS.values() for path in paths
]

# Where screenshots are saved.
SCREENSHOT_FOLDER = FOLDER_PATHS["pictures"][0]

# Folder names that are never searched (virtual environments, caches...).
IGNORED_FOLDER_NAMES = {
    "jarvisenv", "flowerenv", "venv", ".venv", "env", ".env",
    "__pycache__", "appdata", "node_modules", ".git", ".idea",
    ".vscode", "site-packages", "dist-packages",
}

# ==========================================
# FILE SUPPORT
# ==========================================

# Files JARVIS can read and explain.
SUPPORTED_EXTENSIONS = {
    ".txt", ".py", ".md", ".markdown", ".csv", ".json", ".pdf", ".docx",
}

# Extra extensions JARVIS can SEARCH for by exact name (not read).
OTHER_SEARCHABLE_EXTENSIONS = {
    ".doc", ".xls", ".xlsx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg",
    ".gif", ".zip", ".mp3", ".mp4", ".wav", ".log", ".ini", ".yaml",
    ".yml", ".html", ".css", ".js", ".bat", ".ps1", ".exe",
}

# Spoken file-type word -> extensions.
FILE_TYPE_WORDS = {
    "python": {".py"}, "py": {".py"},
    "text": {".txt"}, "txt": {".txt"},
    "pdf": {".pdf"}, "pdfs": {".pdf"},
    "docx": {".docx"}, "word": {".docx"},
    "markdown": {".md", ".markdown"}, "md": {".md", ".markdown"},
    "csv": {".csv"},
    "json": {".json"},
}

# Opening these with the default Windows handler could RUN code, so JARVIS
# refuses to launch them by voice.
BLOCKED_OPEN_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".scr", ".lnk", ".jar", ".reg", ".dll", ".hta",
}

# These are opened in Notepad as text instead of being executed.
OPEN_AS_TEXT_EXTENSIONS = {".py", ".pyw"}

# ==========================================
# LIMITS
# ==========================================

MAX_SEARCH_RESULTS = 500          # results kept in memory
RESULTS_PER_PAGE = 10             # results shown / listed per page
MAX_FILES_PER_FOLDER = 200000     # scan cap PER search folder
SEARCH_TIME_BUDGET_SECONDS = 25   # whole search
MAX_READ_BYTES = 5 * 1024 * 1024  # larger files are read partially
MAX_READ_CHARS = 400000           # characters kept in memory per file
PROMPT_SOURCE_CHARS = 9000        # source characters sent to the AI

# ==========================================
# SPEECH OUTPUT (Windows SAPI)
# ==========================================

VOICE_NAME = "Microsoft David Desktop"
VOICE_RATE = 0          # -10 (slow) to 10 (fast)
VOICE_VOLUME = 100      # 0 to 100
SPEECH_CHUNK_CHARS = 500
SPEECH_TIMEOUT_BASE_SECONDS = 30
SPEECH_TIMEOUT_PER_CHUNK_SECONDS = 60

# ==========================================
# MICROPHONE
# ==========================================

LISTEN_TIMEOUT = 6
LISTEN_PHRASE_LIMIT = 8
AMBIENT_CALIBRATION_SECONDS = 0.6
RECALIBRATE_EVERY_N_FAILURES = 15
MAX_CONSECUTIVE_LISTEN_ERRORS = 5

# ==========================================
# LOGGING
# ==========================================

LOG_FILE = "jarvis.log"
