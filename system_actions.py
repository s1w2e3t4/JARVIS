"""
JARVIS system-command router.

How a spoken command is handled
-------------------------------
1. normalize_spoken()   "main dot py" -> "main.py", "file underscore x" -> "file_x"
2. split_clauses()      "search for main.py and read that file"
                        -> ["search for main.py", "read that file"]
3. parse_clause()       each clause becomes a Step (app, search, read, ...)
                        or None when it is NOT a system command.
4. _run()               steps execute IN ORDER; a failed step stops the rest;
                        ambiguous choices are asked as a short question.

If no clause is recognised, execute_command() returns False and main.py
sends the sentence to the AI as normal conversation. That is why ordinary
questions such as "explain quantum computing" or "open the door" are no
longer mistaken for file commands: a file command needs evidence of a file
(a filename, "file", a result number, "this file", a file type ...).

Context memory (file_manager)
-----------------------------
  last search results   used by "read the second result"
  last-read file        used by "what does this file do"
  focus file            "this file / that file / it": the file selected,
                        read or found most recently

Only an allow-listed set of actions exists: launching four named apps/sites,
opening four folders, screenshots, Google searches, and searching / reading /
opening files inside the configured folders. Nothing the AI says is ever
executed.
"""

import logging
import os
import re
import subprocess
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime

import config
import file_explainer as explainer
import file_manager as fm
from brain import ask_about_text
from voice import speak

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

log = logging.getLogger("jarvis.actions")


# ==========================================
# CONSTANTS
# ==========================================

_INVALID_INDEX = -99      # e.g. "number 0": not a valid result number

ORDINALS = {
    "first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4,
    "sixth": 5, "seventh": 6, "eighth": 7, "ninth": 8, "tenth": 9,
    "1st": 0, "2nd": 1, "3rd": 2, "4th": 3, "5th": 4,
    "6th": 5, "7th": 6, "8th": 7, "9th": 8, "10th": 9,
}

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_ORD_RE = "|".join(sorted(ORDINALS, key=len, reverse=True))
_NUM_RE = "|".join(NUMBER_WORDS)

# Allow-listed applications: key -> (spoken names, command, url, label)
APPS = [
    ("notepad", ("notepad",), "notepad.exe", None, "Notepad"),
    ("calculator", ("calculator", "calc"), "calc.exe", None, "Calculator"),
    ("chrome", ("google chrome", "chrome"), "chrome", None, "Chrome"),
    ("youtube", ("youtube",), None, "https://www.youtube.com", "YouTube"),
]

FOLDER_WORDS = ("downloads", "documents", "desktop", "pictures")

_FILE_WORDS = r"(?:files?|documents?|scripts?)"

_ACTION_START = (
    r"(?:search|find|look for|locate|read|open|load|explain|summari[sz]e|"
    r"describe|analy[sz]e|tell me|show|what|which|how|why|list|take|"
    r"capture|launch|start|run|notepad|calculator|calc|chrome|youtube)"
)

_SPLIT_RE = re.compile(
    r"(?:\s*,\s*(?:and\s+)?(?:then\s+)?"
    r"|\s+and\s+(?:then\s+)?"
    r"|\s+then\s+"
    r"|\s+after that\s+)"
    r"(?=" + _ACTION_START + r"\b)"
)


# ==========================================
# STEPS
# ==========================================

@dataclass
class Step:
    kind: str
    data: dict = field(default_factory=dict)
    text: str = ""

    def get(self, key, default=None):
        return self.data.get(key, default)


@dataclass
class Pending:
    """A question JARVIS asked and is waiting to have answered."""

    kind: str                      # "choose" | "search_target"
    expires: float
    step: Step = None
    items: list = field(default_factory=list)
    rest: list = field(default_factory=list)


_pending = None
_display_offset = 0


# ==========================================
# SPOKEN-TEXT NORMALISATION
# ==========================================

_SPOKEN_EXTENSIONS = (
    r"p\s*y|p\s*d\s*f|d\s*o\s*c\s*x|d\s*o\s*c\s*s?|t\s*x\s*t|m\s*d|"
    r"c\s*s\s*v|j\s*s\s*o\s*n|py|pdf|docx|txt|md|csv|json"
)


def normalize_spoken(text):
    """Make speech-recognition output easier to parse."""
    text = str(text or "").lower().strip()

    text = re.sub(r"\bscreen\s+shots?\b", "screenshot", text)
    text = re.sub(r"\bunderscore\b", "_", text)
    text = re.sub(r"\s*_\s*", "_", text)

    def repair_extension(match):
        extension = re.sub(r"\s+", "", match.group(1))
        return "." + ("docx" if extension == "docs" else extension)

    text = re.sub(
        r"\s*\bdot\s+(" + _SPOKEN_EXTENSIONS + r")\b",
        repair_extension,
        text,
    )

    return re.sub(r"\s+", " ", text).strip()


_POLITE_RE = re.compile(
    r"^(?:please|can you|could you|would you|will you|i want you to|"
    r"i would like you to|i'd like you to|go ahead and|just|now|then|"
    r"and|also|ok|okay|hey)\b[\s,]*"
)

_TRAILING_RE = re.compile(r"\s+(?:please|for me|thank you|thanks)$")


def _clean(clause):
    text = normalize_spoken(clause).strip(" .,?!")

    previous = None

    while previous != text:
        previous = text
        text = _POLITE_RE.sub("", text).strip(" .,?!")
        text = _TRAILING_RE.sub("", text).strip(" .,?!")

    return text


def split_clauses(command):
    """Split a spoken command into ordered action clauses."""
    text = normalize_spoken(command).strip(" .,?!")

    parts = [
        part.strip(" .,?!") for part in _SPLIT_RE.split(text)
    ]

    return [part for part in parts if part]


# ==========================================
# RESULT SELECTION ("the second one", "number 3")
# ==========================================

def parse_selector(text):
    """
    Return a 0-based result index (or -1 for 'the last one'), else None.

    The ordinal must stand alone ("the second", "the second file",
    "the second result") so that "explain the first law of physics" is NOT
    treated as a search-result reference.
    """
    text = str(text)

    match = re.search(
        r"\b(?:number|result|item|match|option|file)\s+(\d{1,3})\b", text
    )
    if match:
        number = int(match.group(1)) - 1
        return number if number >= 0 else _INVALID_INDEX

    match = re.search(
        r"\b(?:number|result|item|match|option|file)\s+(" + _NUM_RE + r")\b",
        text,
    )
    if match:
        return NUMBER_WORDS[match.group(1)] - 1

    match = re.search(
        r"\b(" + _ORD_RE + r")\b"
        r"(?:\s+(?:file|result|one|item|match|document|script))?"
        r"(?=\s*$|\s*,|\s+and\b|\s+then\b)",
        text,
    )
    if match:
        return ORDINALS[match.group(1)]

    if re.search(r"\blast\s+(?:one|result|item|match)\b", text):
        return -1

    return None


def selector_from_reply(text, item_count=0):
    """Understand an answer to 'which one?'. Returns index, 'cancel' or None."""
    text = _clean(text)

    if text in {"cancel", "never mind", "nevermind", "forget it", "stop",
                "none", "no", "nope", "neither"}:
        return "cancel"

    if item_count == 1 and text in {"yes", "yeah", "yep", "sure", "ok",
                                    "okay", "that one", "correct"}:
        return 0

    index = parse_selector(text)

    if index is not None:
        return index

    bare = re.sub(r"^(?:the|number|option|result)\s+", "", text)
    bare = re.sub(r"\s+(?:one|file|result)$", "", bare)

    if bare.isdigit():
        number = int(bare) - 1
        return number if number >= 0 else _INVALID_INDEX

    if bare in NUMBER_WORDS:
        return NUMBER_WORDS[bare] - 1

    if bare in ORDINALS:
        return ORDINALS[bare]

    if bare == "last":
        return -1

    return None


# ==========================================
# TARGETS ("main.py", "the second result", "this file")
# ==========================================

_DEMONSTRATIVE_FILE_RE = re.compile(
    r"\b(?:this|that|the\s+(?:same|current|last|previous|open|selected|loaded))"
    r"\s+(?:file|script|document|doc)\b"
)

_THE_FILE_RE = re.compile(r"\bthe\s+(?:file|script|document|doc)\b")

_WEAK_CONTEXT_RES = [
    re.compile(
        r"^(?:read|open|explain|summari[sz]e|describe|analy[sz]e|load)"
        r"\s+(?:it|that|this|them)$"
    ),
    re.compile(
        r"^(?:explain|tell me|describe)\s+(?:to me\s+)?what\s+"
        r"(?:it|that|this)\s+(?:does|do|is|contains?)$"
    ),
    re.compile(
        r"^(?:what|how)\s+(?:does|do|is)\s+(?:it|that)\s*"
        r"(?:do|work|contain|mean|for|about)?$"
    ),
    re.compile(r"^what(?:'s| is)\s+(?:it|that)\s+(?:for|about)$"),
]


def parse_target(clause):
    """
    Work out WHICH file a clause is about. Returns a dict or None:

      {"type": "name",    "name": "main.py"}
      {"type": "index",   "index": 1}
      {"type": "context", "explicit": True}   ("this file", "the file")
      {"type": "context", "explicit": False}  ("it" - needs a file in mind)
    """
    name = fm.extract_filename(clause)

    if name:
        return {"type": "name", "name": name, "explicit": True}

    index = parse_selector(clause)

    if index is not None:
        return {"type": "index", "index": index, "explicit": True}

    if _DEMONSTRATIVE_FILE_RE.search(clause) or _THE_FILE_RE.search(clause):
        return {"type": "context", "explicit": True}

    for pattern in _WEAK_CONTEXT_RES:
        if pattern.search(clause):
            return {"type": "context", "explicit": False}

    return None


# ==========================================
# CLAUSE PARSERS  (text -> Step or None)
# ==========================================

def _parse_app(c):
    for key, aliases, command, url, label in APPS:
        for alias in aliases:
            with_verb = (
                r"^(?:open|launch|start|run)\s+(?:the\s+)?(?:my\s+)?"
                + re.escape(alias)
                + r"(?:\.exe)?(?:\s+(?:app|application|program))?$"
            )
            bare = r"^" + re.escape(alias) + r"(?:\.exe)?$"

            if re.search(with_verb, c) or re.search(bare, c):
                return Step(
                    "app",
                    {"key": key, "command": command, "url": url,
                     "label": label},
                    c,
                )

    return None


def _parse_folder(c):
    match = re.match(
        r"^(?:(?:open|show|go to|browse)\s+)?(?:the\s+|my\s+)?"
        r"(" + "|".join(FOLDER_WORDS) + r")(?:\s+(?:folder|directory))?$",
        c,
    )

    if match:
        return Step("folder", {"name": match.group(1)}, c)

    return None


def _parse_screenshot(c):
    if re.match(
        r"^(?:take|capture|grab|save|make)\s+(?:me\s+)?(?:a\s+|the\s+)?"
        r"(?:screenshot|screen capture)\b",
        c,
    ) or c == "screenshot":
        return Step("screenshot", {}, c)

    return None


def _parse_google(c):
    patterns = (
        r"^(?:search|google)\s+(?:on\s+)?google\b\s*(?:for\b\s*)?(.*)$",
        r"^google\s+search\b\s*(?:for\b\s*)?(.*)$",
        r"^search\s+(?:for\s+)?(.+?)\s+(?:on|in|using)\s+google$",
    )

    for pattern in patterns:
        match = re.match(pattern, c)

        if match:
            return Step("google", {"query": match.group(1).strip()}, c)

    return None


def _parse_show_more(c):
    if re.match(
        r"^(?:show|give|list)\s+(?:me\s+)?(?:more|the next)"
        r"(?:\s+(?:results?|files?|matches))?$",
        c,
    ) or re.match(r"^(?:more|next)\s+(?:results?|page|files?)$", c):
        return Step("show_more", {}, c)

    return None


_FUNCTION_VERBS = (
    r"(?:explain|describe|summari[sz]e|tell me about|what does|what is|"
    r"what's|how does|show me)"
)


_NOT_A_SYMBOL_NAME = {
    "a", "an", "the", "this", "that", "it", "each", "every", "any", "one",
    "python", "what", "which", "my", "your", "recursive", "lambda",
}


def _strip_file_reference(name_text):
    """Remove 'in main.py' / 'of this file' from a function name."""
    name_text = re.sub(r"\s+(?:in|of|from|inside)\s+.*$", "", name_text)
    return name_text.strip(" .,?!")


def _parse_function(c):
    target = parse_target(c)

    for pattern in (
        r"^" + _FUNCTION_VERBS +
        r"\s+(?:the\s+)?(?:function|method|class)\s+(?:called\s+|named\s+)?"
        r"(.+?)(?:\s+(?:do|does|work|works|mean))?$",
        r"^" + _FUNCTION_VERBS +
        r"\s+(?:the\s+)?(.+?)\s+(?:function|method|class)"
        r"(?:\s+(?:do|does|work|works|mean))?(?:\s+(?:in|of|from)\s+.*)?$",
    ):
        match = re.match(pattern, c)

        if match:
            name = _strip_file_reference(match.group(1))

            if (
                name
                and name not in _NOT_A_SYMBOL_NAME
                and not fm.extract_filename(name)
            ):
                return Step(
                    "function",
                    {"name": name, "strict": True, "target": target},
                    c,
                )

    # "what does find_file do" (no word 'function'): only used when the
    # name really exists in the file that is loaded.
    match = re.match(r"^what does\s+(?:the\s+)?([\w ]+?)\s+do$", c)

    if match:
        name = match.group(1).strip()

        if (
            name
            and not re.search(r"\b(?:it|this|that|file|script|code)\b", name)
            and not fm.extract_filename(name)
        ):
            return Step(
                "function",
                {"name": name, "strict": False, "target": None},
                c,
            )

    return None


def _parse_list(c):
    asks_imports = re.search(
        r"\bwhat\s+(?:does|do)\s+(?:it|this file|that file|the file)\s+import\b"
        r"|\bwhich\s+(?:modules|libraries|packages)\b.*\b(?:import|use|uses)\b",
        c,
    )

    asks_symbols = re.match(
        r"^(?:what|which|list|show|tell me|name)\b.*"
        r"\b(functions?|methods?|classes)\b",
        c,
    )

    if not asks_imports and not asks_symbols:
        return None

    has_reference = re.search(
        r"\b(?:it|this file|that file|the file|this script|that script|"
        r"contain|contains|define|defines|has|have|inside|in it)\b",
        c,
    )

    if not has_reference:
        return None

    target = parse_target(c)

    if target is None:
        target = {"type": "context", "explicit": False}

    if asks_imports:
        what = "imports"
    elif asks_symbols and asks_symbols.group(1).startswith("class"):
        what = "classes"
    else:
        what = "functions"

    return Step("list", {"what": what, "target": target}, c)


def _parse_read_open(c):
    match = re.match(r"^(read|load|open)\b\s*(.*)$", c)

    if not match:
        return None

    verb = "open" if match.group(1) == "open" else "read"

    target = parse_target(c)

    if target is None:
        return None

    return Step(verb, {"target": target}, c)


_QUESTION_START = re.compile(
    r"^(?:explain|summari[sz]e|describe|analy[sz]e|review|tell me|"
    r"what|what's|which|how|why|does|is|are|do|where|give me|walk me)\b"
)


def _parse_explain(c):
    if not _QUESTION_START.match(c):
        return None

    target = parse_target(c)

    if target is None:
        return None

    return Step("explain", {"target": target, "request": c}, c)


_SEARCH_VERB_RE = re.compile(
    r"^(search for|search|find|look for|locate|list|show me|show|get me|get)"
    r"\b\s*(.*)$"
)

_NOT_A_FILE_SEARCH_RE = re.compile(
    r"^(?:for\s+)?(?:out\b|on\s+google|google|the web|the internet|online|"
    r"on youtube|youtube|wikipedia|news|weather|images?|pictures? of|"
    r"directions?|recipes?)"
)

_QUESTION_WORDS = {
    "what", "who", "why", "how", "when", "where", "which", "is", "are",
    "does", "do", "can", "should", "best", "good",
}


def _parse_search(c):
    match = _SEARCH_VERB_RE.match(c)

    if not match:
        return None

    verb, rest = match.group(1), match.group(2).strip()

    if _NOT_A_FILE_SEARCH_RE.match(rest):
        return None

    rest = re.sub(r"^for\s+", "", rest)

    filename = fm.extract_filename(rest)
    types = fm.detect_file_types(rest)
    hint = bool(re.search(r"\b" + _FILE_WORDS + r"\b", rest))

    # "show me / get / list" must clearly be about files.
    if verb in ("show me", "show", "get me", "get", "list") and not (
        filename or types or hint
    ):
        return None

    terms = fm.query_terms(rest, types)
    words = terms.split()

    if filename or types or hint:
        return Step(
            "search",
            {"query": filename or rest, "soft": False,
             "has_target": bool(filename or types or terms)},
            c,
        )

    # No file evidence: only a short, plain term is tried, and the router
    # lets the AI answer if no file matches.
    if (
        1 <= len(words) <= 3
        and not (set(words) & _QUESTION_WORDS)
        and not any(ch.isdigit() for ch in terms)
    ):
        return Step(
            "search",
            {"query": rest, "soft": True, "has_target": True},
            c,
        )

    return None


def parse_clause(clause):
    """Turn ONE clause into a Step, or None if it is not a system command."""
    c = _clean(clause)

    if not c:
        return None

    for parser in (
        _parse_app,
        _parse_folder,
        _parse_screenshot,
        _parse_google,
        _parse_show_more,
        _parse_function,
        _parse_list,
        _parse_read_open,
        _parse_explain,
        _parse_search,
    ):
        step = parser(c)

        if step is not None:
            return step

    return None


def plan(command):
    """
    Turn a whole spoken command into an ordered list of Steps.

    A multi-clause command is only executed as separate steps when EVERY
    clause is understood; otherwise the whole sentence is treated as one
    clause (and usually goes to the AI).
    """
    clauses = split_clauses(command)

    if len(clauses) > 1:
        steps = [parse_clause(clause) for clause in clauses]

        if all(step is not None for step in steps):
            return steps

    single = parse_clause(command)

    return [single] if single else []


# ==========================================
# LOW-LEVEL OPERATING-SYSTEM HELPERS
# (small wrappers so they can be replaced in tests)
# ==========================================

def _start_target(command):
    """Launch an allow-listed program via the Windows 'start' command."""
    result = subprocess.run(
        ["cmd", "/c", "start", "", command],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return result.returncode == 0


def _open_url(url):
    return bool(webbrowser.open(url))


def _startfile(path):
    if not hasattr(os, "startfile"):
        raise OSError("Opening files is only supported on Windows.")

    os.startfile(path)


def _open_in_notepad(path):
    subprocess.Popen(["notepad.exe", path])


# ==========================================
# DISPLAY HELPERS
# ==========================================

def _where(path):
    return os.path.basename(os.path.dirname(path)) or path


def _print_results(results, start=0):
    """Print one page of numbered results and remember where it ended."""
    global _display_offset

    end = min(len(results), start + config.RESULTS_PER_PAGE)

    print(f"\nFiles {start + 1}-{end} of {len(results)}:")

    for number in range(start, end):
        print(f"{number + 1}. {results[number]}")

    print()

    _display_offset = end


_TYPE_LABELS = {
    ".py": "Python", ".pdf": "PDF", ".docx": "Word", ".txt": "text",
    ".md": "Markdown", ".markdown": "Markdown", ".csv": "CSV",
    ".json": "JSON",
}


def _type_label(extensions):
    labels = sorted({_TYPE_LABELS.get(e, e) for e in (extensions or [])})
    return " or ".join(labels) if labels else ""


# ==========================================
# PENDING QUESTIONS
# ==========================================

def has_pending():
    """True while JARVIS is waiting for the answer to its own question."""
    global _pending

    if _pending is None:
        return False

    if time.time() > _pending.expires:
        _pending = None
        return False

    return True


def reset_context():
    """Forget pending questions, search results and the current file."""
    global _pending, _display_offset

    _pending = None
    _display_offset = 0
    fm.reset_context()


def _expiry():
    return time.time() + config.PENDING_QUESTION_SECONDS


def _ask_choice(step, items, rest, intro):
    """Ask the user to pick one of several files (a short question)."""
    global _pending

    items = list(items)

    _print_results(items, 0)
    fm.set_last_results(items)

    verb = {
        "read": "read", "open": "open", "explain": "explain",
    }.get(step.kind, "use")

    if len(items) == 1:
        speak(f"{intro} Should I {verb} it? Say yes or no.")
    else:
        speak(
            f"{intro} Which one should I {verb}? "
            "Say the first, the second, or a number."
        )

    _pending = Pending(
        kind="choose",
        expires=_expiry(),
        step=step,
        items=items,
        rest=list(rest),
    )

    return "wait"


def _ask_search_target(rest):
    global _pending

    speak(
        "What filename or file type should I search for? "
        "For example, main.py or Python files."
    )

    _pending = Pending(
        kind="search_target", expires=_expiry(), rest=list(rest)
    )

    return "wait"


def handle_pending_reply(text):
    """
    Try to use `text` as the answer to JARVIS's last question.

    Returns True if the text was consumed. main.py also calls this for
    sentences WITHOUT the wake word, but only while a question is pending.
    """
    global _pending

    if not has_pending():
        return False

    pending = _pending

    if pending.kind == "search_target":
        query = _clean(text)

        filename = fm.extract_filename(query)
        types = fm.detect_file_types(query)
        terms = fm.query_terms(query, types)

        if not (filename or types or (terms and len(terms.split()) <= 4)):
            return False

        _pending = None

        step = Step(
            "search",
            {"query": filename or query, "soft": False, "has_target": True},
            query,
        )

        _run([step] + pending.rest)
        return True

    if pending.kind == "choose":
        choice = selector_from_reply(text, len(pending.items))

        if choice is None:
            return False

        if choice == "cancel":
            _pending = None
            speak("Okay, I've cancelled that.")
            return True

        index = len(pending.items) - 1 if choice == -1 else choice

        if index < 0 or index >= len(pending.items):
            pending.expires = _expiry()
            speak(
                f"I only have {len(pending.items)} to choose from. "
                "Please pick one of those."
            )
            return True

        _pending = None

        path = pending.items[index]
        fm.set_focus(path)

        status = _perform(pending.step, path)

        if status == "ok":
            if pending.rest:
                _run(pending.rest)
        elif status == "stop" and pending.rest:
            speak("I stopped there, so I did not run the remaining steps.")

        return True

    return False


# ==========================================
# TARGET RESOLUTION
# ==========================================

def _resolve(step, rest, only):
    """
    Decide which file a step is about.

    Returns (status, path) where status is
      "ok"         path is the file
      "wait"       a clarification question was asked
      "stop"       failed; an explanation was spoken
      "unhandled"  not actually a file command (let the AI answer)
    """
    target = step.get("target") or {"type": "context", "explicit": False}
    kind = target["type"]

    # ---- "the second result" ------------------------------------
    if kind == "index":
        results = fm.get_last_results()
        index = target["index"]

        if not results:
            speak(
                "I don't have any search results yet. "
                "Ask me to search for a file first."
            )
            return "stop", None

        if index == -1:
            index = len(results) - 1

        if index < 0 or index >= len(results):
            number = "that number" if index == _INVALID_INDEX else (
                f"number {index + 1}"
            )
            speak(
                f"There is no result {number}. "
                f"I have {len(results)} result"
                f"{'' if len(results) == 1 else 's'}."
            )
            return "stop", None

        path = results[index]
        fm.set_focus(path)
        return "ok", path

    # ---- "main.py" ----------------------------------------------
    if kind == "name":
        name = target["name"]
        print(f"Looking for {name}...")

        outcome = fm.search(name)

        if not outcome.results:
            speak(_not_found_message(outcome, f"a file named {name}"))
            return "stop", None

        if outcome.mode == "exact" and len(outcome.results) == 1:
            path = outcome.results[0]
            fm.set_focus(path)
            return "ok", path

        if outcome.mode == "exact":
            intro = f"I found {len(outcome.results)} files named {name}."
        else:
            intro = (
                f"I couldn't find a file named exactly {name}, but I found "
                f"{len(outcome.results)} whose names contain it."
            )

        return _ask_choice(step, outcome.results, rest, intro), None

    # ---- "this file" / "that file" / "it" -----------------------
    results = fm.get_last_results()
    explicit = target.get("explicit", False)

    if step.kind in ("read", "open"):
        if fm.results_newer_than_focus():
            return _ask_choice(
                step, results, rest,
                f"I have {len(results)} search results.",
            ), None

        path = fm.get_focus()

        if not path and len(results) == 1:
            path = results[0]

        if not path and len(results) > 1:
            return _ask_choice(
                step, results, rest,
                f"I have {len(results)} search results.",
            ), None
    else:
        path = fm.get_focus() or fm.get_last_read_file()

        if not path and len(results) == 1:
            path = results[0]

    if not path:
        if only and not explicit:
            return "unhandled", None

        speak(
            "I don't have a file in mind yet. "
            "Ask me to search for a file, or tell me its name."
        )
        return "stop", None

    if not os.path.isfile(path):
        speak(f"{os.path.basename(path)} doesn't exist any more.")
        return "stop", None

    return "ok", path


def _not_found_message(outcome, what):
    """An honest 'not found' message that names only folders really searched."""

    if not outcome.searched:
        return (
            "I couldn't search because none of my search folders "
            "could be found."
        )

    message = f"I couldn't find {what} in {fm.describe_scope(outcome)}."

    if outcome.missing:
        names = ", ".join(
            os.path.basename(os.path.normpath(p)) for p in outcome.missing
        )
        message += f" I couldn't check {names}, because that folder wasn't found."

    if not outcome.complete:
        message += " The search was cut short, so it may exist."

    return message


# ==========================================
# STEP EXECUTION
# ==========================================

def _run(steps):
    """
    Execute steps in order. Returns True if the command was handled,
    False if (and only if) a single step turned out not to be a system
    command, so main.py should hand the sentence to the AI.
    """
    for number, step in enumerate(steps):
        rest = steps[number + 1:]
        only = len(steps) == 1

        log.info("step %s %s", step.kind, step.text)

        try:
            status = _dispatch(step, rest, only)
        except Exception as error:
            log.exception("step failed")
            print("System command error:", error)
            speak(
                "I encountered an error while executing that command."
            )
            status = "stop"

        if status == "unhandled":
            if only:
                return False

            status = "stop"

        if status == "wait":
            return True

        if status == "stop":
            if rest:
                speak(
                    "I stopped there, so I did not run the remaining steps."
                )
            return True

    return True


def _dispatch(step, rest, only):
    kind = step.kind

    if kind == "app":
        return _do_app(step)

    if kind == "folder":
        return _do_folder(step)

    if kind == "screenshot":
        return _do_screenshot()

    if kind == "google":
        return _do_google(step)

    if kind == "show_more":
        return _do_show_more()

    if kind == "search":
        return _do_search(step, rest, only)

    if kind == "function":
        return _do_function(step, rest, only)

    if kind in ("read", "open", "explain", "list"):
        status, path = _resolve(step, rest, only)

        if status != "ok":
            return status

        return _perform(step, path)

    return "unhandled"


def _perform(step, path):
    """Run a file-based step on a file that has been chosen."""
    kind = step.kind

    if kind == "read":
        return _read_path(path)

    if kind == "open":
        return _open_file(path)

    status = _ensure_loaded(path)

    if status != "ok":
        return status

    if kind == "explain":
        return _explain_loaded(step.get("request") or step.text)

    if kind == "function":
        return _explain_function(path, step)

    if kind == "list":
        return _list_loaded(path, step)

    return "stop"


# ---- applications, folders, web --------------------------------------

def _do_app(step):
    label = step.get("label")

    try:
        if step.get("url"):
            opened = _open_url(step.get("url"))
        else:
            opened = _start_target(step.get("command"))
    except Exception as error:
        log.warning("app launch error: %s", error)
        opened = False

    if opened:
        speak(f"Opening {label}.")
        return "ok"

    speak(f"Sorry, I couldn't open {label}.")
    return "stop"


def _do_folder(step):
    name = step.get("name")

    for path in config.FOLDER_PATHS.get(name, []):
        if os.path.isdir(path):
            try:
                _startfile(path)
            except Exception as error:
                log.warning("folder open error: %s", error)
                speak(f"Sorry, I couldn't open your {name.title()} folder.")
                return "stop"

            speak(f"Opening your {name.title()} folder.")
            return "ok"

    speak(f"I couldn't find your {name.title()} folder.")
    return "stop"


def _do_screenshot():
    if ImageGrab is None:
        speak(
            "Screenshots need the Pillow library. "
            "Install it with pip install pillow."
        )
        return "stop"

    folder = config.SCREENSHOT_FOLDER

    try:
        os.makedirs(folder, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(folder, f"jarvis_screenshot_{stamp}.png")

        # Never overwrite an earlier screenshot.
        counter = 2

        while os.path.exists(path):
            path = os.path.join(
                folder, f"jarvis_screenshot_{stamp}_{counter}.png"
            )
            counter += 1

        ImageGrab.grab().save(path)

        if not os.path.isfile(path):
            raise OSError("The screenshot file was not created.")

    except Exception as error:
        log.warning("screenshot error: %s", error)
        print("Screenshot error:", error)
        speak("Sorry, I could not take the screenshot.")
        return "stop"

    print("Saved to:", path)
    speak(
        "Screenshot taken and saved to your "
        f"{os.path.basename(folder)} folder."
    )
    return "ok"


def _do_google(step):
    query = step.get("query")

    if not query:
        speak("What should I search for on Google?")
        return "ok"

    url = "https://www.google.com/search?q=" + urllib.parse.quote(query)

    try:
        opened = _open_url(url)
    except Exception as error:
        log.warning("google error: %s", error)
        opened = False

    if not opened:
        speak("Sorry, I couldn't open your browser.")
        return "stop"

    speak("Searching Google for " + query)
    return "ok"


# ---- file search -----------------------------------------------------

def _do_search(step, rest, only):
    soft = bool(step.get("soft")) and only

    if not step.get("has_target", True):
        return _ask_search_target(rest)

    query = step.get("query")

    print(f"Searching for {query!r}...")

    outcome = fm.search(query, keep_previous_if_empty=soft)

    if soft and not outcome.results:
        return "unhandled"

    return _report_search(outcome, rest)


def _report_search(outcome, rest):
    results = outcome.results

    print(f"Searched: {fm.describe_scope(outcome)}")

    if not results:
        what = f"files matching {outcome.query}"
        filename = fm.extract_filename(outcome.query)

        if filename:
            what = f"a file named {filename}"
        elif fm.detect_file_types(outcome.query):
            what = "any matching files"

        speak(_not_found_message(outcome, what))
        return "stop"

    _print_results(results, 0)

    count = len(results)
    first = os.path.basename(results[0])

    if outcome.mode == "exact" and count == 1:
        message = f"I found {first} in the {_where(results[0])} folder."
        fm.set_focus(results[0])
    elif outcome.mode == "exact":
        message = (
            f"I found {count} files named {first}. "
            "They are listed on screen."
        )
    elif outcome.mode == "fallback":
        message = (
            f"I didn't find a file named {outcome.query}, but I found "
            f"{count} whose names contain it."
        )
    elif outcome.mode == "type":
        types = _type_label(fm.detect_file_types(outcome.query))
        message = f"I found {count} {types} files, newest first."
    else:
        terms = fm.query_terms(outcome.query, fm.detect_file_types(outcome.query))
        message = f"I found {count} files matching {terms or outcome.query}."

    if count == 1 and outcome.mode != "exact":
        fm.set_focus(results[0])
        message += f" It is {first}, in the {_where(results[0])} folder."

    if count > config.RESULTS_PER_PAGE:
        message += (
            f" I've listed the first {config.RESULTS_PER_PAGE}; "
            "say show more for the rest."
        )

    if not outcome.complete:
        message += " The search was cut short, so there may be more."

    if count > 1 and not rest:
        message += " Tell me which one to read or open."

    speak(message)
    return "ok"


def _do_show_more():
    results = fm.get_last_results()

    if not results:
        speak("I don't have any search results yet.")
        return "stop"

    if _display_offset >= len(results):
        speak(f"That's all {len(results)} results.")
        return "ok"

    start = _display_offset
    _print_results(results, start)

    speak(
        f"Showing results {start + 1} to {_display_offset} "
        f"of {len(results)}."
    )
    return "ok"


# ---- reading, opening, explaining ------------------------------------

def _read_path(path):
    print("Reading:", path)

    result = fm.read_file(path)

    if not result.ok:
        speak(result.error)
        return "stop"

    speak(
        explainer.read_confirmation(
            path, result.content, result.pages, result.truncated
        )
    )
    return "ok"


def _ensure_loaded(path):
    """Make sure `path` is the file whose content is in memory."""
    if (
        fm.get_last_read_file() == path
        and fm.get_last_read_content() is not None
    ):
        return "ok"

    result = fm.read_file(path)

    if not result.ok:
        speak(result.error)
        return "stop"

    return "ok"


def _open_file(path):
    name = os.path.basename(path)
    extension = os.path.splitext(name)[1].lower()

    if extension in config.BLOCKED_OPEN_EXTENSIONS:
        speak(
            f"For safety, I don't launch {extension} files by voice."
        )
        return "stop"

    try:
        print("Opening:", path)

        if extension in config.OPEN_AS_TEXT_EXTENSIONS:
            _open_in_notepad(path)
            speak(
                f"Opening {name} in Notepad. I open Python files as text "
                "so they are never run by accident."
            )
        else:
            _startfile(path)
            speak(f"Opening {name}.")

        fm.set_focus(path)
        return "ok"

    except Exception as error:
        log.warning("open error: %s", error)
        print("File opening error:", error)
        speak("Sorry, I couldn't open that file.")
        return "stop"


def _explain_loaded(request):
    """Explain the last-read file using its real contents."""
    path = fm.get_last_read_file()
    content = fm.get_last_read_content()

    if not path or content is None:
        speak("I don't have a file in memory yet. Ask me to read a file first.")
        return "stop"

    name = os.path.basename(path)
    truncated = fm.get_last_read_truncated()

    if not content.strip():
        speak(f"{name} is empty, so there is nothing to explain.")
        return "ok"

    facts_text, fallback, py_facts = explainer.analyse_file(
        path, content, fm.get_last_read_pages()
    )

    print("\n" + facts_text + "\n")

    note = explainer.limitation_note(content, truncated)

    prompt = explainer.build_explain_prompt(
        path, content, facts_text, request, truncated
    )

    answer = ask_about_text(
        prompt, history_note=f"Please explain the file {name}. ({request})"
    )

    return _deliver_answer(answer, path, content, fallback, note, name,
                           check_code=py_facts is not None)


def _deliver_answer(answer, path, content, fallback, note, name, check_code):
    """Speak a model answer only if it is grounded in the file."""

    if answer is None:
        speak(
            "My language model isn't responding, so here is what I can "
            "verify directly from the file. " + fallback
            + (" " + note if note else "")
        )
        return "ok"

    if check_code:
        extras = [os.path.splitext(name)[0]] + explainer.sibling_module_names(path)
        invented = explainer.ungrounded_names(answer, content, extras)

        if invented:
            log.warning("ungrounded names in answer: %s", invented)
            print("Rejected unverified details:", ", ".join(invented))
            speak(
                "My language model mentioned things that are not in this "
                "file, so I won't repeat them. Here is what I can verify "
                "directly. " + fallback + (" " + note if note else "")
            )
            return "ok"

    speak(f"About {name}. {answer}" + (" " + note if note else ""))
    return "ok"


def _do_function(step, rest, only):
    """Explain one function of the loaded Python file."""
    name = step.get("name")
    strict = step.get("strict", True)
    target = step.get("target")

    if target and target.get("type") in ("name", "index"):
        # "explain the find_file function in file_manager.py"
        status, path = _resolve(
            Step("function", {"target": target}, step.text), rest, only
        )

        if status != "ok":
            return status

    elif strict:
        path = fm.get_focus() or fm.get_last_read_file()

        if not path:
            speak("Please ask me to read a file first.")
            return "stop"

    else:
        # "what does find_file do" with no word 'function': only treated as
        # a code question when that name exists in the file already loaded.
        path = fm.get_last_read_file()

        if (
            not path
            or fm.get_last_read_content() is None
            or os.path.splitext(path)[1].lower() != ".py"
        ):
            return "unhandled"

    status = _ensure_loaded(path)

    if status != "ok":
        return status

    path = fm.get_last_read_file()
    extension = os.path.splitext(path)[1].lower()

    if extension != ".py":
        if not strict:
            return "unhandled"

        speak(
            "I can look up functions in Python files. "
            f"{os.path.basename(path)} is a {explainer.file_kind(path)}."
        )
        return "stop"

    facts = explainer.analyze_python(
        fm.get_last_read_content(), explainer.sibling_module_names(path)
    )

    symbol, suggestions = explainer.find_symbol(facts, name)

    if symbol is None:
        if not strict:
            return "unhandled"

        if not facts.ok:
            speak(
                f"I can't analyse {os.path.basename(path)} as Python "
                f"({facts.error})."
            )
            return "stop"

        message = (
            f"I don't see a function called {name} in "
            f"{os.path.basename(path)}."
        )

        if suggestions:
            message += " Did you mean " + " or ".join(suggestions) + "?"
        elif facts.functions:
            message += (
                " It defines "
                + ", ".join(f.qualname for f in facts.functions[:8])
                + ("." if len(facts.functions) <= 8 else ", and more.")
            )

        speak(message)
        return "stop"

    return _explain_function(path, step, facts=facts, symbol=symbol)


def _explain_function(path, step, facts=None, symbol=None):
    content = fm.get_last_read_content()
    name_file = os.path.basename(path)

    if facts is None:
        facts = explainer.analyze_python(
            content, explainer.sibling_module_names(path)
        )
        symbol, _ = explainer.find_symbol(facts, step.get("name"))

    if symbol is None:
        speak(f"I don't see {step.get('name')} in {name_file}.")
        return "stop"

    source = explainer.symbol_source(content, symbol)

    if isinstance(symbol, explainer.ClassInfo):
        facts_text = (
            f"Class: {symbol.name}"
            + (f"({', '.join(symbol.bases)})" if symbol.bases else "")
            + f"\nLocation: lines {symbol.lineno}-{symbol.end_lineno}"
            + (f"\nMethods: {', '.join(symbol.methods)}" if symbol.methods else "")
        )
        fallback = (
            f"{symbol.name} is a class on lines {symbol.lineno} to "
            f"{symbol.end_lineno}"
            + (f" with the methods {', '.join(symbol.methods)}." if symbol.methods else ".")
        )
    else:
        facts_text = explainer.describe_function_facts(symbol)
        fallback = explainer.function_spoken_summary(symbol)

    print("\n" + facts_text + "\n")

    request = step.get("request") or step.text or f"Explain {symbol.name}"

    prompt = explainer.build_function_prompt(
        path, symbol.name, facts_text, source, request
    )

    answer = ask_about_text(
        prompt,
        history_note=f"Please explain {symbol.name} in {name_file}.",
    )

    return _deliver_answer(
        answer, path, content, fallback, "", name_file, check_code=True
    )


def _list_loaded(path, step):
    content = fm.get_last_read_content()
    name = os.path.basename(path)

    if os.path.splitext(path)[1].lower() != ".py":
        speak(
            "I can list functions, classes and imports for Python files. "
            f"{name} is a {explainer.file_kind(path)}."
        )
        return "stop"

    facts = explainer.analyze_python(
        content, explainer.sibling_module_names(path)
    )

    if not facts.ok:
        speak(f"I can't analyse {name} as Python ({facts.error}).")
        return "stop"

    what = step.get("what")

    if what == "imports":
        modules = []

        for module, _names in facts.imports:
            if module not in modules:
                modules.append(module)

        print("\nImports in " + name + ":")
        for module, names in facts.imports:
            print(f"  {module}" + (f": {', '.join(names)}" if names else ""))
        print()

        if not modules:
            speak(f"{name} has no imports.")
        else:
            speak(
                f"{name} imports {len(modules)} module"
                f"{'' if len(modules) == 1 else 's'}: "
                + ", ".join(modules[:12])
                + (", and more." if len(modules) > 12 else ".")
            )

        return "ok"

    if what == "classes":
        print("\nClasses in " + name + ":")
        for cls in facts.classes:
            print(f"  {cls.name} (line {cls.lineno})")
        print()

        if not facts.classes:
            speak(f"{name} defines no classes.")
        else:
            speak(
                f"{name} defines {len(facts.classes)} class"
                f"{'' if len(facts.classes) == 1 else 'es'}: "
                + ", ".join(c.name for c in facts.classes[:12]) + "."
            )

        return "ok"

    print("\nFunctions in " + name + ":")
    for func in facts.functions:
        print(
            f"  {func.qualname}({', '.join(func.args)})  "
            f"lines {func.lineno}-{func.end_lineno}"
        )
    print()

    if not facts.functions:
        speak(f"{name} defines no functions.")
    else:
        names = [f.qualname for f in facts.functions]
        speak(
            f"{name} defines {len(names)} function"
            f"{'' if len(names) == 1 else 's'}: "
            + ", ".join(names[:12])
            + (", and more. The full list is on screen." if len(names) > 12 else ".")
        )

    return "ok"


# ==========================================
# PUBLIC ENTRY POINT
# ==========================================

def execute_command(command):
    """
    Try to handle `command` as a system command.

    Returns True if it was handled (successfully or with a spoken error),
    False if main.py should treat it as normal AI conversation.
    """
    global _pending

    command = str(command or "").lower().strip()

    if not command:
        return False

    # An answer to a question JARVIS just asked ("the first one").
    if _pending is not None:
        if handle_pending_reply(command):
            return True

        _pending = None     # the user moved on to something else

    steps = plan(command)

    if not steps:
        return False

    return _run(steps)
