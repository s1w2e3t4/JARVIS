"""
JARVIS File Explainer

Everything needed to explain a file WITHOUT inventing details:

1. Deterministic analysis. Python files are parsed with the `ast` module,
   so function names, classes, imports and line numbers are facts taken from
   the code, not guesses by the language model.
2. Grounded prompts. The model is given those verified facts plus the real
   source (or an honestly labelled excerpt) and strict rules.
3. A grounding check. If the model's answer mentions an identifier that does
   not exist in the file, the answer is rejected and the verified summary is
   used instead.

This module does not talk to the model or to the user: it only analyses
text and builds strings, so it is easy to test.
"""

import ast
import builtins
import csv
import difflib
import io
import json
import os
import re
from dataclasses import dataclass, field

import config


# ==========================================
# DATA STRUCTURES
# ==========================================

@dataclass
class FuncInfo:
    name: str
    lineno: int
    end_lineno: int
    args: list = field(default_factory=list)
    docstring: str = ""
    parent: str = ""                 # class name for methods
    calls_defined: list = field(default_factory=list)
    calls_imported: list = field(default_factory=list)

    @property
    def qualname(self):
        return f"{self.parent}.{self.name}" if self.parent else self.name


@dataclass
class ClassInfo:
    name: str
    lineno: int
    end_lineno: int
    bases: list = field(default_factory=list)
    docstring: str = ""
    methods: list = field(default_factory=list)


@dataclass
class PythonFacts:
    ok: bool
    error: str = ""
    line_count: int = 0
    docstring: str = ""
    imports: list = field(default_factory=list)       # (module, [names])
    local_imports: list = field(default_factory=list)  # project modules
    functions: list = field(default_factory=list)
    classes: list = field(default_factory=list)
    constants: list = field(default_factory=list)
    has_main_guard: bool = False
    main_guard_calls: list = field(default_factory=list)


# ==========================================
# SMALL HELPERS
# ==========================================

def _first_sentence(text, limit=140):
    """First sentence of the first paragraph (or its first line)."""
    text = str(text or "").strip()

    if not text:
        return ""

    paragraph = re.split(r"\n\s*\n", text)[0]
    joined = " ".join(paragraph.split())

    match = re.match(r"(.+?[.!?])(\s|$)", joined)

    if match:
        sentence = match.group(1)
    else:
        # No sentence end: use only the first line (e.g. a title line).
        sentence = " ".join(paragraph.splitlines()[0].split())

    if len(sentence) > limit:
        sentence = sentence[:limit].rstrip() + "..."

    return sentence


def _plural(count, word):
    if count == 1:
        return f"{count} {word}"

    return f"{count} {word}" + ("es" if word.endswith("s") else "s")


def file_kind(path):
    """Human description of a file type."""
    return {
        ".py": "Python file",
        ".txt": "text file",
        ".md": "Markdown file",
        ".markdown": "Markdown file",
        ".csv": "CSV file",
        ".json": "JSON file",
        ".pdf": "PDF document",
        ".docx": "Word document",
    }.get(os.path.splitext(str(path))[1].lower(), "file")


def normalise_symbol(name):
    """'find file', 'find_file' and '_Find_File' all become 'findfile'."""
    return re.sub(r"[\W_]+", "", str(name).lower())


# ==========================================
# PYTHON ANALYSIS
# ==========================================

def _called_names(node):
    names = set()

    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func

            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)

    return names


def analyze_python(source, sibling_modules=None):
    """
    Parse Python source and return verified facts (PythonFacts).

    sibling_modules: names of other .py files next to this one, used to tell
    project modules apart from libraries.
    """
    sibling_modules = {m.lower() for m in (sibling_modules or [])}
    source = str(source)

    facts = PythonFacts(ok=False, line_count=len(source.splitlines()))

    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        facts.error = f"line {error.lineno}: {error.msg}"
        return facts
    except (ValueError, RecursionError) as error:
        facts.error = str(error)
        return facts

    facts.ok = True
    facts.docstring = ast.get_docstring(tree) or ""

    # ---- imports (anywhere in the file, including try/except) ----
    imported_names = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                facts.imports.append((alias.name, []))
                imported_names.add((alias.asname or alias.name).split(".")[0])

        elif isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            names = [alias.name for alias in node.names]
            facts.imports.append((module, names))

            for alias in node.names:
                imported_names.add(alias.asname or alias.name)

    for module, names in facts.imports:
        top = module.lstrip(".").split(".")[0].lower()

        if top and top in sibling_modules:
            facts.local_imports.append((module, names))

    # ---- functions, classes, constants ----
    def make_func(node, parent=""):
        decorator_lines = [d.lineno for d in node.decorator_list]
        start = min([node.lineno] + decorator_lines)

        args = [a.arg for a in node.args.posonlyargs + node.args.args]

        if node.args.vararg:
            args.append("*" + node.args.vararg.arg)

        args += [a.arg for a in node.args.kwonlyargs]

        if node.args.kwarg:
            args.append("**" + node.args.kwarg.arg)

        return FuncInfo(
            name=node.name,
            lineno=start,
            end_lineno=getattr(node, "end_lineno", node.lineno),
            args=args,
            docstring=ast.get_docstring(node) or "",
            parent=parent,
        )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            facts.functions.append(make_func(node))

        elif isinstance(node, ast.ClassDef):
            info = ClassInfo(
                name=node.name,
                lineno=node.lineno,
                end_lineno=getattr(node, "end_lineno", node.lineno),
                bases=[
                    ast.unparse(base) if hasattr(ast, "unparse") else "?"
                    for base in node.bases
                ],
                docstring=ast.get_docstring(node) or "",
            )

            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    info.methods.append(item.name)
                    facts.functions.append(make_func(item, parent=node.name))

            facts.classes.append(info)

        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    facts.constants.append(target.id)

        elif isinstance(node, ast.If) and _is_main_guard(node.test):
            facts.has_main_guard = True
            guard_calls = set()

            for statement in node.body:
                guard_calls |= _called_names(statement)

            facts.main_guard_calls = sorted(guard_calls)

    # ---- call relationships (only to names we can verify) ----
    defined = {f.name for f in facts.functions} | {c.name for c in facts.classes}

    nodes_by_key = {}

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nodes_by_key[(node.name, node.lineno)] = node

    for func in facts.functions:
        node = None

        for (name, lineno), candidate in nodes_by_key.items():
            if name == func.name and (
                lineno == func.lineno
                or candidate.lineno == func.lineno
                or (
                    candidate.decorator_list
                    and min(d.lineno for d in candidate.decorator_list)
                    == func.lineno
                )
            ):
                node = candidate
                break

        if node is None:
            continue

        called = _called_names(node)

        func.calls_defined = sorted(
            n for n in called if n in defined and n != func.name
        )
        func.calls_imported = sorted(n for n in called if n in imported_names)

    return facts


def _is_main_guard(test):
    """True for:  __name__ == "__main__"  (either operand order)."""
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False

    if not isinstance(test.ops[0], ast.Eq):
        return False

    sides = [test.left] + list(test.comparators)

    has_name = any(
        isinstance(s, ast.Name) and s.id == "__name__" for s in sides
    )
    has_main = any(
        isinstance(s, ast.Constant) and s.value == "__main__" for s in sides
    )

    return has_name and has_main


def format_python_facts(facts, filename):
    """Verified facts as plain text (shown on screen and given to the model)."""

    if not facts.ok:
        return (
            f"VERIFIED FACTS about {filename}:\n"
            f"- The file could not be parsed as Python ({facts.error}), so "
            "its structure is unknown."
        )

    lines = [
        f"VERIFIED FACTS about {filename} "
        "(extracted from the code, not guessed):",
        f"- Length: {facts.line_count} lines.",
    ]

    if facts.docstring:
        lines.append(
            f"- Its own description (docstring): "
            f"{_first_sentence(facts.docstring, 200)}"
        )

    if facts.imports:
        pieces = []

        for module, names in facts.imports[:25]:
            pieces.append(
                f"{module} ({', '.join(names)})" if names else module
            )

        lines.append("- Imports: " + "; ".join(pieces) + ".")

    if facts.local_imports:
        pieces = [
            f"{module} ({', '.join(names)})" if names else module
            for module, names in facts.local_imports
        ]
        lines.append(
            "- Project modules it imports from: " + "; ".join(pieces) + "."
        )

    if facts.classes:
        for cls in facts.classes[:15]:
            bases = f"({', '.join(cls.bases)})" if cls.bases else ""
            methods = f" methods: {', '.join(cls.methods)}" if cls.methods else ""
            lines.append(
                f"- Class {cls.name}{bases} at line {cls.lineno}.{methods}"
            )

    if facts.functions:
        lines.append(f"- Functions and methods ({len(facts.functions)}):")

        for func in facts.functions[:60]:
            summary = _first_sentence(func.docstring, 110)
            text = (
                f"  * {func.qualname}({', '.join(func.args)}) "
                f"lines {func.lineno}-{func.end_lineno}"
            )

            if summary:
                text += f" - {summary}"

            if func.calls_defined:
                text += f" [calls: {', '.join(func.calls_defined)}]"

            lines.append(text)

        if len(facts.functions) > 60:
            lines.append(f"  * ...and {len(facts.functions) - 60} more.")
    else:
        lines.append("- It defines no functions.")

    if facts.has_main_guard:
        calls = (
            f" and calls {', '.join(facts.main_guard_calls)}"
            if facts.main_guard_calls else ""
        )
        lines.append(
            "- It has an `if __name__ == \"__main__\"` block"
            f"{calls}, so it can be run directly."
        )
    else:
        lines.append("- It has no `if __name__ == \"__main__\"` block.")

    return "\n".join(lines)


def python_spoken_summary(facts, filename):
    """A short, fully verified spoken summary of a Python file."""

    if not facts.ok:
        return (
            f"{filename} is not valid Python that I can analyse "
            f"({facts.error})."
        )

    parts = [
        f"{filename} is a Python file with {facts.line_count} lines."
    ]

    if facts.docstring:
        parts.append(
            "Its own description says: "
            + _first_sentence(facts.docstring, 160)
        )

    if facts.local_imports:
        modules = ", ".join(m for m, _n in facts.local_imports)
        parts.append(f"It uses these project modules: {modules}.")

    names = [f.qualname for f in facts.functions]

    if names:
        shown = ", ".join(names[:8])
        extra = len(names) - 8
        more = f", and {extra} more" if extra > 0 else ""
        parts.append(
            f"It defines {_plural(len(names), 'function')}: {shown}{more}."
        )
    else:
        parts.append("It defines no functions.")

    if facts.classes:
        parts.append(
            f"It defines {_plural(len(facts.classes), 'class')}: "
            + ", ".join(c.name for c in facts.classes[:5]) + "."
        )

    if facts.has_main_guard:
        parts.append("It runs code when started directly.")

    return " ".join(parts)


# ==========================================
# SYMBOL LOOKUP
# ==========================================

def find_symbol(facts, spoken_name):
    """
    Find a function/method/class by a spoken or written name.

    Returns (match, suggestions):
      match       a FuncInfo / ClassInfo, or None
      suggestions close names to offer when nothing matches
    """
    wanted = normalise_symbol(spoken_name)

    if not wanted or not facts.ok:
        return None, []

    candidates = {}

    for func in facts.functions:
        candidates.setdefault(normalise_symbol(func.name), func)

    for cls in facts.classes:
        candidates.setdefault(normalise_symbol(cls.name), cls)

    if wanted in candidates:
        return candidates[wanted], []

    close = difflib.get_close_matches(wanted, list(candidates), n=3, cutoff=0.6)

    names = []

    for key in close:
        item = candidates[key]
        names.append(item.name)

    return None, names


def symbol_source(content, info):
    """The exact source text of a function or class."""
    lines = str(content).splitlines()
    start = max(1, info.lineno)
    end = max(start, info.end_lineno)
    return "\n".join(lines[start - 1:end])


def describe_function_facts(func):
    """Plain-text verified facts about one function."""
    parts = [
        f"Function: {func.qualname}({', '.join(func.args)})",
        f"Location: lines {func.lineno}-{func.end_lineno}",
    ]

    if func.docstring:
        parts.append(f"Docstring: {_first_sentence(func.docstring, 300)}")

    if func.calls_defined:
        parts.append("Calls functions defined in this file: "
                     + ", ".join(func.calls_defined))

    if func.calls_imported:
        parts.append("Calls imported names: " + ", ".join(func.calls_imported))

    return "\n".join(parts)


def function_spoken_summary(func):
    """A short, fully verified spoken description of one function."""
    args = ", ".join(func.args) if func.args else "no arguments"
    parts = [
        f"{func.qualname} takes {args} and spans lines "
        f"{func.lineno} to {func.end_lineno}."
    ]

    if func.docstring:
        parts.append(
            "Its docstring says: " + _first_sentence(func.docstring, 200)
        )

    if func.calls_defined:
        parts.append(
            "It calls these functions from the same file: "
            + ", ".join(func.calls_defined) + "."
        )

    if func.calls_imported:
        parts.append(
            "It also calls these imported names: "
            + ", ".join(func.calls_imported) + "."
        )

    return " ".join(parts)


# ==========================================
# NON-PYTHON FILES
# ==========================================

def describe_other_file(path, content, pages=0):
    """Return (facts_text, spoken_summary) for non-Python files."""

    name = os.path.basename(path)
    extension = os.path.splitext(name)[1].lower()
    kind = file_kind(path)

    text = str(content)
    line_count = len(text.splitlines())
    word_count = len(text.split())

    facts = [
        f"VERIFIED FACTS about {name}:",
        f"- Type: {kind}.",
        f"- Length: {line_count} lines, about {word_count} words.",
    ]

    spoken = [f"{name} is a {kind} with about {word_count} words."]

    if pages:
        facts.append(f"- Pages: {pages}.")
        spoken = [
            f"{name} is a {kind} with {_plural(pages, 'page')} and "
            f"about {word_count} words."
        ]

    if extension in (".md", ".markdown"):
        headings = [
            line.lstrip("# ").strip()
            for line in text.splitlines()
            if line.startswith("#")
        ]

        if headings:
            facts.append("- Headings: " + "; ".join(headings[:15]) + ".")
            spoken.append("Its headings are: " + ", ".join(headings[:6]) + ".")

    elif extension == ".csv":
        try:
            rows = list(csv.reader(io.StringIO(text)))
        except csv.Error:
            rows = []

        if rows:
            header = [cell.strip() for cell in rows[0]]
            facts.append(
                f"- First row (likely column names): {', '.join(header)}."
            )
            facts.append(f"- Data rows after the first row: {len(rows) - 1}.")
            spoken.append(
                f"Its first row lists {_plural(len(header), 'column')}: "
                + ", ".join(header[:8]) + "."
            )

    elif extension == ".json":
        try:
            data = json.loads(text)

            if isinstance(data, dict):
                keys = list(data)[:15]
                facts.append("- Top-level object with keys: "
                             + ", ".join(map(str, keys)) + ".")
                spoken.append("It is a JSON object with the keys "
                              + ", ".join(map(str, keys[:8])) + ".")
            elif isinstance(data, list):
                facts.append(f"- Top-level list with {len(data)} items.")
                spoken.append(f"It is a JSON list with {len(data)} items.")
            else:
                facts.append(f"- Top-level value of type "
                             f"{type(data).__name__}.")
        except ValueError as error:
            facts.append(f"- It is not valid JSON ({error}).")
            spoken.append("It is not valid JSON.")

    beginning = " ".join(text.split())[:200]

    if beginning:
        facts.append(f"- It begins with: {beginning}")
        spoken.append(f"It begins with: {beginning}")

    return "\n".join(facts), " ".join(spoken)


# ==========================================
# OVERVIEWS (used when a file is read)
# ==========================================

def sibling_module_names(path):
    """Names of the other .py files next to `path`."""
    folder = os.path.dirname(os.path.abspath(path))

    try:
        return [
            os.path.splitext(name)[0]
            for name in os.listdir(folder)
            if name.lower().endswith(".py")
        ]
    except OSError:
        return []


def analyse_file(path, content, pages=0):
    """
    Return (facts_text, spoken_summary, python_facts_or_None).

    Everything returned is derived from the file itself.
    """
    name = os.path.basename(path)

    if os.path.splitext(name)[1].lower() == ".py":
        facts = analyze_python(content, sibling_module_names(path))
        return (
            format_python_facts(facts, name),
            python_spoken_summary(facts, name),
            facts,
        )

    facts_text, spoken = describe_other_file(path, content, pages)
    return facts_text, spoken, None


def read_confirmation(path, content, pages=0, truncated=False):
    """One or two short sentences confirming what was read."""
    name = os.path.basename(path)
    extension = os.path.splitext(name)[1].lower()
    lines = len(str(content).splitlines())

    if extension == ".py":
        facts = analyze_python(content, sibling_module_names(path))

        if facts.ok:
            text = (
                f"I've read {name}. It is a Python file with {lines} lines, "
                f"{_plural(len(facts.functions), 'function')} and "
                f"{_plural(len(facts.classes), 'class')}."
            )
        else:
            text = (
                f"I've read {name}, {lines} lines, but it isn't valid "
                f"Python that I can analyse ({facts.error})."
            )

    elif pages:
        text = (
            f"I've read {name}. It is a {file_kind(path)} with "
            f"{_plural(pages, 'page')} and about "
            f"{len(str(content).split())} words."
        )

    else:
        text = (
            f"I've read {name}. It is a {file_kind(path)} with {lines} "
            f"lines and about {len(str(content).split())} words."
        )

    if truncated:
        text += (
            " The file is very large, so I only loaded the first part of it."
        )

    return text


def limitation_note(content, truncated):
    """A sentence to add when the explanation could not cover everything."""

    if truncated:
        return (
            "Note: this file was too large to load completely, so this "
            "covers only the part I loaded."
        )

    if len(str(content)) > config.PROMPT_SOURCE_CHARS:
        return (
            "Note: the file is longer than I can show my language model "
            "at once, so details toward the end come from the verified "
            "function list rather than the full code."
        )

    return ""


# ==========================================
# PROMPTS
# ==========================================

_RULES = """Rules you must follow:
- Use ONLY the verified facts and the file contents above.
- Do not mention any function, class, module, library or feature that does not appear above.
- If something is not shown in the material, say that it is not shown.
- You may say what the code clearly does; label any guess as "this suggests".
- Do not use markdown, bullet characters or emojis. Write plain sentences for speech.
- Keep it to about six sentences unless the user asked for detail."""


def _excerpt(content, limit):
    content = str(content)

    if len(content) <= limit:
        return content, False

    return content[:limit], True


def build_explain_prompt(path, content, facts_text, request, truncated=False):
    """Prompt for explaining a whole file."""
    source, cut = _excerpt(content, config.PROMPT_SOURCE_CHARS)

    notes = []

    if cut:
        notes.append(
            f"NOTE: the source below is only the first {len(source)} of "
            f"{len(content)} characters. Say clearly that your explanation "
            "may not cover the rest. The verified facts cover the whole file."
        )

    if truncated:
        notes.append(
            "NOTE: the file was too large to load completely."
        )

    return "\n".join([
        f"Filename: {os.path.basename(path)}",
        "",
        facts_text,
        "",
        *notes,
        "SOURCE BEGIN",
        source,
        "SOURCE END",
        "",
        f"User request: {request}",
        "",
        _RULES,
    ])


def build_function_prompt(path, name, function_facts, function_source, request):
    """Prompt for explaining one function (its exact code is supplied)."""
    source, _cut = _excerpt(function_source, config.PROMPT_SOURCE_CHARS)

    return "\n".join([
        f"Filename: {os.path.basename(path)}",
        "",
        "VERIFIED FACTS (extracted from the code):",
        function_facts,
        "",
        "FUNCTION SOURCE BEGIN",
        source,
        "FUNCTION SOURCE END",
        "",
        f"User request: {request}",
        "",
        _RULES,
        "- Explain only this function. You may mention what it calls only "
        "if the facts above list it.",
    ])


# ==========================================
# GROUNDING CHECK
# ==========================================

_BUILTIN_NAMES = set(dir(builtins))


def ungrounded_names(answer, content, extra_names=()):
    """
    Names in `answer` that look like code identifiers (snake_case words,
    `name()` calls, or `something.py` files) but do not exist in the source.

    An empty list means the answer is consistent with the file.
    """
    source_tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(content))
    }
    source_tokens |= {str(n).lower() for n in extra_names}

    suspicious = []

    def check(token):
        key = token.lower().strip("_")

        if not key:
            return

        if key in source_tokens or token.lower() in source_tokens:
            return

        if key in _BUILTIN_NAMES:
            return

        if token not in suspicious:
            suspicious.append(token)

    answer = str(answer)

    # snake_case identifiers: find_wake_word
    for token in re.findall(r"\b[A-Za-z]+(?:_[A-Za-z0-9]+)+\b", answer):
        check(token)

    # function-call style: name()
    for token in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\(\)", answer):
        check(token)

    # file names: voice.py
    for token in re.findall(r"\b([A-Za-z0-9_]+)\.py\b", answer):
        check(token)

    return suspicious
