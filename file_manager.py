"""
JARVIS File Manager

Handles:
- Searching files by exact name, partial name and file type
- Remembering search results, the last-read file and the "focus" file
- Reading TXT, Python, Markdown, CSV, JSON, PDF and DOCX files

Key design rules
----------------
* ONE search implementation (search / search_files / find_file all use the
  same scanner, ignore rules and de-duplication), so results are identical
  on the first attempt and every later attempt.
* read_file() returns a ReadResult. A failure is NEVER returned as if it
  were file content.
* A failed read does not erase the previously read file.
* Only files inside the configured folders (or this project folder) can be
  read.
"""

import logging
import os
import re
import time
from dataclasses import dataclass, field

import config

try:
    from pypdf import PdfReader
except ImportError:          # reported to the user when a PDF is read
    PdfReader = None

try:
    from docx import Document
except ImportError:          # reported to the user when a DOCX is read
    Document = None

log = logging.getLogger("jarvis.files")


# ==========================================
# SETTINGS (read at call time, so tests can change them)
# ==========================================

SEARCH_FOLDERS = list(config.SEARCH_FOLDERS)
IGNORED_FOLDERS = set(config.IGNORED_FOLDER_NAMES)
FILE_TYPE_EXTENSIONS = dict(config.FILE_TYPE_WORDS)
SUPPORTED_EXTENSIONS = set(config.SUPPORTED_EXTENSIONS)
SUPPORTED_TEXT_EXTENSIONS = {
    ".txt", ".py", ".md", ".markdown", ".csv", ".json",
}

PROJECT_FOLDER = os.path.dirname(os.path.abspath(__file__))

_ALL_KNOWN_EXTENSIONS = (
    set(config.SUPPORTED_EXTENSIONS)
    | set(config.OTHER_SEARCHABLE_EXTENSIONS)
)

_QUERY_FILLER = {
    "search", "find", "look", "locate", "for", "the", "a", "an",
    "file", "files", "document", "documents", "named", "name", "called",
    "that", "which", "with", "my", "please", "jarvis", "java", "show",
    "me", "all", "matching", "script", "scripts", "of", "type", "any",
}


# ==========================================
# MEMORY (shared state)
# ==========================================

_last_results = []
_last_outcome = None
_last_read_file = None
_last_read_content = None
_last_read_truncated = False
_last_read_pages = 0

_focus_path = None

_tick = 0
_results_tick = 0
_focus_tick = 0


def _next_tick():
    global _tick
    _tick += 1
    return _tick


def set_last_results(results):
    """Remember the latest search results (replaces the previous ones)."""
    global _last_results, _results_tick
    _last_results = list(results)
    _results_tick = _next_tick()


def get_last_results():
    return list(_last_results)


def get_last_outcome():
    """The SearchOutcome of the most recent search (or None)."""
    return _last_outcome


def set_focus(path):
    """
    Mark a file as 'the file we are talking about'
    ('this file', 'that file', 'it').
    """
    global _focus_path, _focus_tick
    _focus_path = path
    _focus_tick = _next_tick()


def get_focus():
    return _focus_path


def results_newer_than_focus():
    """
    True when the user searched, got SEVERAL results and has not chosen
    one yet - so 'that file' would be ambiguous.
    """
    return len(_last_results) > 1 and _results_tick > _focus_tick


def _set_last_read(path, content, truncated, pages=0):
    global _last_read_file, _last_read_content, _last_read_truncated
    global _last_read_pages
    _last_read_file = path
    _last_read_content = content
    _last_read_truncated = bool(truncated)
    _last_read_pages = int(pages or 0)
    set_focus(path)


def get_last_read_file():
    return _last_read_file


def get_last_read_content():
    return _last_read_content


def get_last_read_truncated():
    return _last_read_truncated


def get_last_read_pages():
    return _last_read_pages


def reset_context():
    """Forget search results, the last-read file and the focus file."""
    global _last_results, _last_outcome, _last_read_file
    global _last_read_content, _last_read_truncated, _focus_path
    global _last_read_pages
    _last_results = []
    _last_outcome = None
    _last_read_file = None
    _last_read_content = None
    _last_read_truncated = False
    _last_read_pages = 0
    _focus_path = None


# ==========================================
# QUERY HELPERS
# ==========================================

def _normalise_query(query):
    query = str(query or "").strip().lower()
    query = query.strip(" \t\r\n\"'")
    return " ".join(query.split())


def _key(text):
    """Comparable form: lower-case words, '_', '-' and '.' become spaces."""
    return " ".join(re.sub(r"[\W_]+", " ", str(text).lower()).split())


def extract_filename(text):
    """
    Return the first token that looks like 'name.ext' with a known
    extension (for example 'main.py'), or None.
    """
    extensions = "|".join(
        sorted(
            (ext.lstrip(".") for ext in _ALL_KNOWN_EXTENSIONS),
            key=len,
            reverse=True,
        )
    )

    match = re.search(
        r"(?<![\w.\-])([\w\-]+(?:\.[\w\-]+)*\.(?:" + extensions + r"))(?![\w])",
        str(text).lower(),
    )

    return match.group(1) if match else None


def detect_file_types(query):
    """Public: extensions implied by words like 'python files' (or None)."""
    return _file_type_extensions(query)


def query_terms(query, type_extensions=None):
    """Public: the search terms left after removing command words."""
    return _query_terms(query, type_extensions)


def _file_type_extensions(query):
    """
    Detect file-type requests such as:
    'Python files', '.py files', 'PDF documents', or 'CSV'.
    """
    cleaned = _normalise_query(query)
    words = cleaned.replace(".", " ").replace("-", " ").split()

    asks_for_files = any(
        word in {
            "file", "files", "document", "documents",
            "script", "scripts"
        }
        for word in words
    )

    # An explicit extension token such as ".py" or ".docx".
    for token in cleaned.split():
        if token.startswith(".") and token in _ALL_KNOWN_EXTENSIONS:
            return {token}

    for word in words:
        if word in FILE_TYPE_EXTENSIONS and (
            asks_for_files or len(words) == 1
        ):
            return set(FILE_TYPE_EXTENSIONS[word])

    return None


def _query_terms(query, type_extensions=None):
    """Remove command words and file-type words; keep the useful terms."""
    words = _normalise_query(query).split()

    type_words = set()

    if type_extensions:
        type_words = set(FILE_TYPE_EXTENSIONS)

    kept = []

    for word in words:
        if word in _QUERY_FILLER:
            continue

        if word in type_words:
            continue

        if word.startswith(".") and word in _ALL_KNOWN_EXTENSIONS:
            continue

        kept.append(word)

    return " ".join(kept)


# ==========================================
# SCANNING
# ==========================================

@dataclass
class SearchOutcome:
    """Everything a caller needs to describe a search honestly."""

    query: str = ""
    results: list = field(default_factory=list)
    mode: str = "empty"      # exact | partial | type | type_terms | fallback
    complete: bool = True    # False if a limit stopped the scan early
    scanned: int = 0
    searched: list = field(default_factory=list)   # folders really walked
    missing: list = field(default_factory=list)    # configured, not found
    errors: int = 0                                # unreadable directories
    note: str = ""


def _is_virtualenv(path):
    return os.path.isfile(os.path.join(path, "pyvenv.cfg"))


def _scan(outcome, deadline):
    """
    Walk every configured folder and yield file paths.

    Skips ignored/hidden folders and virtual environments, survives
    unreadable directories, and records what was REALLY searched.
    """

    def on_error(_error):
        outcome.errors += 1

    seen_roots = set()

    for folder in list(SEARCH_FOLDERS):
        if not os.path.isdir(folder):
            outcome.missing.append(folder)
            continue

        root_key = os.path.normcase(os.path.realpath(folder))

        if root_key in seen_roots:
            continue

        seen_roots.add(root_key)
        outcome.searched.append(folder)

        folder_count = 0

        for root, dirs, files in os.walk(
            folder, topdown=True, onerror=on_error
        ):
            dirs[:] = [
                directory for directory in dirs
                if directory.lower() not in IGNORED_FOLDERS
                and not directory.startswith(".")
                and not _is_virtualenv(os.path.join(root, directory))
            ]

            for filename in files:
                folder_count += 1
                outcome.scanned += 1

                if folder_count > config.MAX_FILES_PER_FOLDER:
                    outcome.complete = False
                    outcome.note = (
                        f"I stopped scanning {os.path.basename(folder)} "
                        "after a very large number of files."
                    )
                    break

                yield os.path.join(root, filename)

            else:
                if time.monotonic() > deadline:
                    outcome.complete = False
                    outcome.note = "The search ran out of time."
                    return

                continue

            break   # folder cap reached: go to next folder


def _iter_files():
    """Backwards-compatible iterator over all searchable files."""
    outcome = SearchOutcome()
    deadline = time.monotonic() + config.SEARCH_TIME_BUDGET_SECONDS
    yield from _scan(outcome, deadline)


def describe_scope(outcome):
    """Plain-English list of the folders that were really searched."""
    names = []

    for folder in outcome.searched:
        name = os.path.basename(os.path.normpath(folder)) or folder

        if name not in names:
            names.append(name)

    if not names:
        return "no folders"

    if len(names) == 1:
        return names[0]

    return ", ".join(names[:-1]) + " and " + names[-1]


# ==========================================
# SEARCH
# ==========================================

def _mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def search(query, keep_previous_if_empty=False):
    """
    Search filenames or file types and return a SearchOutcome.

    Examples of clean queries:
        'main.py'          exact filename
        'main'             partial filename
        'Python files'     file type
        '.docx'            file type
        'python files main'  file type + name part
    The latest results are remembered for follow-up commands. With
    keep_previous_if_empty=True, a search that finds nothing leaves the
    previous results in memory (used for speculative searches).
    """
    global _last_outcome

    original = _normalise_query(query)
    outcome = SearchOutcome(query=original)

    if not original:
        if not keep_previous_if_empty:
            _last_outcome = outcome
            set_last_results([])
        return outcome

    filename = extract_filename(original)
    type_extensions = None if filename else _file_type_extensions(original)
    terms = "" if filename else _query_terms(original, type_extensions)
    terms_key = _key(terms)

    if not filename and not type_extensions and len(terms_key) < 2:
        outcome.note = "The search text was too short."
        if not keep_previous_if_empty:
            _last_outcome = outcome
            set_last_results([])
        return outcome

    deadline = time.monotonic() + config.SEARCH_TIME_BUDGET_SECONDS

    exact = []
    contains = []
    typed = []
    seen = set()

    for path in _scan(outcome, deadline):
        real_key = os.path.normcase(os.path.abspath(path))

        if real_key in seen:
            continue

        name = os.path.basename(path)
        lower = name.lower()
        extension = os.path.splitext(lower)[1]

        if filename:
            if lower == filename:
                seen.add(real_key)
                exact.append((0, path))
            elif filename in lower:
                seen.add(real_key)
                contains.append((2, path))
            continue

        if type_extensions:
            if extension not in type_extensions:
                continue

            if terms_key:
                name_key = _key(lower)
                if not all(t in name_key for t in terms_key.split()):
                    continue

            seen.add(real_key)
            typed.append((3, path))
            continue

        # Partial filename search.
        name_key = _key(lower)
        stem_key = _key(os.path.splitext(lower)[0])

        if not all(t in name_key for t in terms_key.split()):
            continue

        if stem_key == terms_key:
            score = 0
        elif stem_key.startswith(terms_key):
            score = 1
        elif re.search(r"(?<![a-z0-9])" + re.escape(terms_key), name_key):
            score = 2
        else:
            score = 3

        seen.add(real_key)
        contains.append((score, path))

    if filename:
        if exact:
            outcome.mode = "exact"
            chosen = exact
        elif contains:
            outcome.mode = "fallback"
            chosen = contains
        else:
            chosen = []
    elif type_extensions:
        outcome.mode = "type_terms" if terms_key else "type"
        chosen = typed
    else:
        outcome.mode = "partial"
        chosen = contains

    def sort_key(item):
        score, path = item

        if outcome.mode in ("type", "type_terms"):
            return (-_mtime(path), os.path.basename(path).lower(), path)

        return (
            score,
            path.count(os.sep),
            -_mtime(path),
            os.path.basename(path).lower(),
            path,
        )

    chosen.sort(key=sort_key)

    outcome.results = [
        os.path.abspath(path)
        for _score, path in chosen[:config.MAX_SEARCH_RESULTS]
    ]

    if not outcome.results:
        outcome.mode = "empty"

    if outcome.results or not keep_previous_if_empty:
        _last_outcome = outcome
        set_last_results(outcome.results)

    log.info(
        "search %r -> %d result(s), mode=%s, scanned=%d, complete=%s",
        original, len(outcome.results), outcome.mode,
        outcome.scanned, outcome.complete,
    )

    return outcome


def search_files(query):
    """List-returning wrapper around search() (kept for compatibility)."""
    return search(query).results


def find_file(filename):
    """
    Find exact filename matches (case-insensitive) in the configured
    folders. Returns a list of full paths.

    An explicit full path (containing a folder separator) is also accepted.
    A bare name such as 'main.py' is NEVER looked up relative to the
    current working directory - that made results depend on where
    JARVIS was started from.
    """
    filename = _normalise_query(filename)

    if not filename:
        return []

    if os.sep in filename or "/" in filename:
        supplied = os.path.abspath(
            os.path.expanduser(os.path.expandvars(filename))
        )

        if os.path.isfile(supplied) and is_allowed_path(supplied):
            return [supplied]

        return []

    outcome = SearchOutcome(query=filename)
    deadline = time.monotonic() + config.SEARCH_TIME_BUDGET_SECONDS

    matches = []
    seen = set()

    for path in _scan(outcome, deadline):
        if os.path.basename(path).lower() == filename:
            key = os.path.normcase(os.path.abspath(path))

            if key not in seen:
                seen.add(key)
                matches.append(os.path.abspath(path))

    matches.sort(key=lambda p: (p.count(os.sep), p.lower()))

    return matches


# ==========================================
# PATH SAFETY
# ==========================================

def is_allowed_path(path):
    """True if the path is inside a search folder or this project."""
    try:
        target = os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return False

    for folder in list(SEARCH_FOLDERS) + [PROJECT_FOLDER]:
        base = os.path.normcase(os.path.realpath(folder))

        try:
            if os.path.commonpath([target, base]) == base:
                return True
        except ValueError:      # different drives on Windows
            continue

    return False


# ==========================================
# READING
# ==========================================

@dataclass
class ReadResult:
    """Outcome of reading a file. Truthy only when the read succeeded."""

    ok: bool
    path: str
    content: str = ""
    error: str = ""
    truncated: bool = False
    total_chars: int = 0
    pages: int = 0

    def __bool__(self):
        return self.ok


def _fail(path, message):
    log.info("read failed: %s (%s)", path, message)
    return ReadResult(ok=False, path=path, error=message)


def _decode_bytes(data):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    return data.decode("utf-8", errors="replace")


def read_text_file(path):
    try:
        with open(path, "rb") as handle:
            data = handle.read(config.MAX_READ_BYTES + 1)
    except OSError as error:
        return _fail(path, f"I couldn't open the file: {error.strerror or error}.")

    truncated = len(data) > config.MAX_READ_BYTES

    if truncated:
        data = data[:config.MAX_READ_BYTES]

    content = _decode_bytes(data)

    if len(content) > config.MAX_READ_CHARS:
        content = content[:config.MAX_READ_CHARS]
        truncated = True

    return ReadResult(
        ok=True, path=path, content=content,
        truncated=truncated, total_chars=len(content),
    )


def read_pdf_file(path):
    if PdfReader is None:
        return _fail(
            path,
            "PDF support needs the pypdf package. "
            "Install it with: pip install pypdf",
        )

    try:
        reader = PdfReader(path)

        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return _fail(path, "That PDF is password protected.")

        pages = []

        for page in reader.pages:
            text = page.extract_text()

            if text and text.strip():
                pages.append(text.strip())

        page_count = len(reader.pages)

    except Exception as error:
        return _fail(path, f"I couldn't read that PDF: {error}")

    if not pages:
        return _fail(
            path,
            "That PDF has no extractable text. "
            "It may be a scanned image.",
        )

    content = "\n\n".join(pages)
    truncated = len(content) > config.MAX_READ_CHARS

    if truncated:
        content = content[:config.MAX_READ_CHARS]

    return ReadResult(
        ok=True, path=path, content=content, truncated=truncated,
        total_chars=len(content), pages=page_count,
    )


def read_docx_file(path):
    if Document is None:
        return _fail(
            path,
            "Word support needs the python-docx package. "
            "Install it with: pip install python-docx",
        )

    try:
        document = Document(path)
        parts = []

        for paragraph in document.paragraphs:
            text = paragraph.text.strip()

            if text:
                parts.append(text)

        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]

                if any(cells):
                    parts.append(" | ".join(cells))

    except Exception as error:
        return _fail(path, f"I couldn't read that Word document: {error}")

    if not parts:
        return _fail(path, "That Word document contains no text.")

    content = "\n\n".join(parts)
    truncated = len(content) > config.MAX_READ_CHARS

    if truncated:
        content = content[:config.MAX_READ_CHARS]

    return ReadResult(
        ok=True, path=path, content=content,
        truncated=truncated, total_chars=len(content),
    )


def read_file(path):
    """
    Read any supported file and return a ReadResult.

    On success the file becomes the last-read file and the focus file.
    On failure the previous context is left untouched.
    """
    path = os.path.abspath(str(path))

    if not os.path.isfile(path):
        return _fail(path, "That file does not exist any more.")

    if not is_allowed_path(path):
        return _fail(
            path,
            "I only read files inside your Documents, Downloads, "
            "Desktop and Pictures folders.",
        )

    extension = os.path.splitext(path)[1].lower()

    if extension not in SUPPORTED_EXTENSIONS:
        return _fail(
            path,
            f"I can't read {extension or 'that kind of'} files. "
            "I can read TXT, Python, Markdown, CSV, JSON, PDF and "
            "DOCX files.",
        )

    if extension == ".pdf":
        result = read_pdf_file(path)
    elif extension == ".docx":
        result = read_docx_file(path)
    else:
        result = read_text_file(path)

    if result.ok:
        _set_last_read(
            path, result.content, result.truncated, result.pages
        )

    return result
