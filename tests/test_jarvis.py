"""
JARVIS automated tests.

Run from the project folder (Command Prompt):

    python -m unittest discover -s tests -v

These tests need NO microphone, NO Ollama and NO internet: the language
model, text-to-speech, application launching and screenshots are replaced
by fakes. They use a temporary fake home folder, never your real files.
Things that need real hardware are listed in TESTING.md.
"""

import ast
import builtins
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

# ---- stand-ins for packages that are not needed to test the logic ----
try:
    import speech_recognition  # noqa: F401
except Exception:
    sr = types.ModuleType("speech_recognition")

    class _Recognizer:
        dynamic_energy_threshold = True
        pause_threshold = 0.8

    class _Microphone:
        def __enter__(self):
            raise OSError("no microphone")

        def __exit__(self, *args):
            return False

    sr.Recognizer, sr.Microphone = _Recognizer, _Microphone
    sr.WaitTimeoutError = type("WaitTimeoutError", (Exception,), {})
    sr.UnknownValueError = type("UnknownValueError", (Exception,), {})
    sr.RequestError = type("RequestError", (Exception,), {})
    sys.modules["speech_recognition"] = sr

try:
    import ollama  # noqa: F401
except Exception:
    fake_ollama = types.ModuleType("ollama")
    fake_ollama.chat = lambda *a, **k: {"message": {"content": "x"}}
    sys.modules["ollama"] = fake_ollama

import brain            # noqa: E402
import config           # noqa: E402
import file_explainer as explainer   # noqa: E402
import file_manager as fm            # noqa: E402
import main as jarvis_main           # noqa: E402
import system_actions as sa          # noqa: E402
import voice            # noqa: E402

MODULES = [
    "config", "voice", "brain", "file_manager", "file_explainer",
    "system_actions", "main",
]

SAMPLE_MAIN = '''"""Demo entry point."""
import re
from helpers import speak


def find_wake_word(text):
    """Find the wake word."""
    return re.search("jarvis", text)


def main():
    word = find_wake_word("jarvis hi")
    speak(word)


if __name__ == "__main__":
    main()
'''

SAMPLE_FILE_MANAGER = '''"""Files."""
import os


def find_file(name):
    """Find exact name."""
    return [name]


class Store:
    def save(self):
        return os.getcwd()
'''


def read_project_file(name):
    with open(os.path.join(PROJECT, name + ".py"), encoding="utf-8") as handle:
        return handle.read()


def make_pdf(path, text):
    """Write a tiny but valid one-page PDF containing `text`."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    with open(path, "wb") as handle:
        handle.write(out)


class FakeHomeTestCase(unittest.TestCase):
    """Builds a temporary home folder and routes all modules to it."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="jarvis_test_")
        self.docs = os.path.join(self.home, "Documents")
        self.downloads = os.path.join(self.home, "Downloads")
        self.desktop = os.path.join(self.home, "Desktop")
        self.pictures = os.path.join(self.home, "Pictures")

        project = os.path.join(self.downloads, "jarvis_claude")

        for folder in (
            self.docs, self.desktop, self.pictures,
            os.path.join(project, "jarvisenv", "Lib"),
            os.path.join(project, "__pycache__"),
            os.path.join(self.docs, "Reports"),
            os.path.join(self.docs, "other_env", "Lib"),
        ):
            os.makedirs(folder, exist_ok=True)

        self.project = project
        self.main_real = self.write(
            os.path.join(project, "main.py"), SAMPLE_MAIN)
        self.main_docs = self.write(
            os.path.join(self.docs, "main.py"), "# another main\nprint(1)\n")
        self.write(os.path.join(project, "jarvisenv", "Lib", "main.py"), "#x")
        self.write(os.path.join(project, "__pycache__", "main.pyc"), "x")
        self.write(os.path.join(self.docs, "other_env", "pyvenv.cfg"), "home=x")
        self.write(os.path.join(self.docs, "other_env", "Lib", "main.py"), "#v")
        self.fm_path = self.write(
            os.path.join(project, "file_manager.py"), SAMPLE_FILE_MANAGER)
        self.voice_path = self.write(
            os.path.join(project, "voice.py"), "def speak(t):\n    pass\n")
        self.notes = self.write(
            os.path.join(self.desktop, "notes.txt"), "Shopping list: milk\n")
        self.write(os.path.join(self.docs, "data.csv"), "name,age\nAnn,3\n")
        self.write(os.path.join(self.docs, "cfg.json"), '{"a": 1, "b": 2}')
        self.write(os.path.join(self.docs, "readme.md"), "# Title\n\n## Part\n")
        self.write(os.path.join(self.docs, "Reports", "main report.txt"), "r")

        from docx import Document
        document = Document()
        document.add_paragraph("Quarterly report body text.")
        self.docx_path = os.path.join(self.docs, "report.docx")
        document.save(self.docx_path)

        self.pdf_path = os.path.join(self.docs, "paper.pdf")
        make_pdf(self.pdf_path, "Hello PDF world")

        from PIL import Image
        self.blank_pdf = os.path.join(self.docs, "scan.pdf")
        Image.new("RGB", (40, 40), "white").save(self.blank_pdf)
        self.png = os.path.join(self.pictures, "photo.png")
        Image.new("RGB", (8, 8), "red").save(self.png)

        # Route every module at the fake home.
        self._patches = [
            mock.patch.object(fm, "SEARCH_FOLDERS", [
                self.docs, self.downloads, self.desktop, self.pictures]),
            mock.patch.object(config, "FOLDER_PATHS", {
                "documents": [self.docs], "downloads": [self.downloads],
                "desktop": [self.desktop], "pictures": [self.pictures]}),
            mock.patch.object(config, "SCREENSHOT_FOLDER", self.pictures),
        ]
        for patch in self._patches:
            patch.start()

        fm.reset_context()
        sa.reset_context()
        brain.reset_conversation()

        self.spoken = []
        self.ai_prompts = []
        self.ai_answer = "This file does something real."

        def fake_speak(text):
            self.spoken.append(str(text))
            return True

        def fake_ai(prompt, history_note=None):
            self.ai_prompts.append(prompt)
            answer = self.ai_answer
            return answer(prompt) if callable(answer) else answer

        for patch in (
            mock.patch.object(sa, "speak", fake_speak),
            mock.patch.object(sa, "ask_about_text", fake_ai),
            mock.patch("builtins.print"),
        ):
            patch.start()
            self._patches.append(patch)

    def tearDown(self):
        mock.patch.stopall()
        shutil.rmtree(self.home, ignore_errors=True)

    # helpers
    @staticmethod
    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def run_cmd(self, text):
        self.spoken.clear()
        handled = sa.execute_command(text)
        return handled

    @property
    def said(self):
        return " ".join(self.spoken)


# =====================================================================
# 1 + 2. SYNTAX AND IMPORT COMPATIBILITY
# =====================================================================

class TestSyntaxAndImports(unittest.TestCase):

    def test_every_module_compiles(self):
        for name in MODULES:
            path = os.path.join(PROJECT, name + ".py")
            py_compile.compile(path, doraise=True)

    def test_cross_module_names_exist(self):
        """Every `from X import name` between project modules is real."""
        for name in MODULES:
            tree = ast.parse(read_project_file(name))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in MODULES:
                    module = sys.modules[node.module]
                    for alias in node.names:
                        self.assertTrue(
                            hasattr(module, alias.name),
                            f"{name} imports {alias.name} from "
                            f"{node.module}, which does not define it")

    def test_attribute_calls_between_modules_exist(self):
        """fm.xxx / explainer.xxx / config.xxx used in the router exist."""
        tree = ast.parse(read_project_file("system_actions"))
        aliases = {"fm": fm, "explainer": explainer, "config": config}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in aliases):
                self.assertTrue(
                    hasattr(aliases[node.value.id], node.attr),
                    f"{node.value.id}.{node.attr} does not exist")

    def test_no_circular_import(self):
        order = ["config", "file_explainer", "file_manager", "brain",
                 "voice", "system_actions", "main"]
        code = "import sys; sys.path.insert(0, %r); " % PROJECT
        for name in order:
            result = subprocess.run(
                [sys.executable, "-c", code + "import " + name],
                capture_output=True, text=True)
            # a missing optional package is not a circular import
            self.assertNotIn("circular", result.stderr.lower(), name)
            self.assertNotIn("partially initialized", result.stderr, name)

    def test_no_secrets_in_source(self):
        import re
        for name in MODULES:
            text = read_project_file(name)
            self.assertIsNone(
                re.search(r"(?i)(api[_-]?key|secret|password)\s*=\s*['\"]\w",
                          text), name)


# =====================================================================
# 3-5. FILE SEARCH
# =====================================================================

class TestSearch(FakeHomeTestCase):

    def test_exact_filename_finds_both_and_ignores_envs(self):
        outcome = fm.search("main.py")
        self.assertEqual(outcome.mode, "exact")
        self.assertEqual(set(outcome.results),
                         {self.main_real, self.main_docs})

    def test_exact_search_independent_of_working_directory(self):
        original = os.getcwd()
        try:
            os.chdir(self.project)       # contains main.py
            self.assertEqual(set(fm.find_file("main.py")),
                             {self.main_real, self.main_docs})
            self.assertEqual(set(fm.search_files("main.py")),
                             {self.main_real, self.main_docs})
        finally:
            os.chdir(original)

    def test_first_and_repeat_results_identical(self):
        first = fm.search("python files").results
        second = fm.search("python files").results
        third = fm.search_files("Python files")
        self.assertEqual(first, second)
        self.assertEqual(first, third)

    def test_partial_name(self):
        results = fm.search("main").results
        names = [os.path.basename(p) for p in results]
        self.assertIn("main.py", names)
        self.assertIn("main report.txt", names)
        # exact-stem matches rank before "main report"
        self.assertEqual(names[0], "main.py")

    def test_python_files(self):
        names = {os.path.basename(p) for p in fm.search("Python files").results}
        self.assertEqual(names, {"main.py", "file_manager.py", "voice.py"})
        self.assertEqual(len(fm.search("Python files").results), 4)

    def test_pdf_docx_text_and_extension_queries(self):
        self.assertEqual(
            {os.path.basename(p) for p in fm.search("PDF files").results},
            {"paper.pdf", "scan.pdf"})
        self.assertEqual(
            [os.path.basename(p) for p in fm.search("DOCX files").results],
            ["report.docx"])
        self.assertEqual(
            [os.path.basename(p) for p in fm.search("word documents").results],
            ["report.docx"])
        self.assertEqual(
            {os.path.basename(p) for p in fm.search("text files").results},
            {"notes.txt", "main report.txt"})
        self.assertEqual(len(fm.search(".docx").results), 1)
        self.assertEqual(len(fm.search("json files").results), 1)
        self.assertEqual(len(fm.search("csv").results), 1)

    def test_type_plus_name(self):
        results = fm.search("python files named voice").results
        self.assertEqual([os.path.basename(p) for p in results], ["voice.py"])

    def test_spoken_name_with_underscore_and_spaces(self):
        results = fm.search("file manager").results
        self.assertIn(self.fm_path, results)

    def test_dedupe_when_folders_overlap(self):
        with mock.patch.object(fm, "SEARCH_FOLDERS",
                               [self.docs, self.docs, self.downloads]):
            results = fm.search("main.py").results
        self.assertEqual(len(results), len(set(results)))
        self.assertEqual(len(results), 2)

    def test_empty_and_missing_results(self):
        outcome = fm.search("nonexistent_zzz.py")
        self.assertEqual(outcome.results, [])
        self.assertEqual(outcome.mode, "empty")
        self.assertEqual(fm.search("").results, [])
        self.assertEqual(fm.search("a").results, [])

    def test_fallback_contains_match(self):
        self.write(os.path.join(self.docs, "old_main.py"), "#")
        outcome = fm.search("old_main.py")
        self.assertEqual(outcome.mode, "exact")
        outcome = fm.search("ld_main.py")
        self.assertEqual(outcome.mode, "fallback")
        self.assertEqual(len(outcome.results), 1)

    def test_missing_folder_is_reported_not_claimed(self):
        ghost = os.path.join(self.home, "Ghost")
        with mock.patch.object(fm, "SEARCH_FOLDERS", [self.docs, ghost]):
            outcome = fm.search("main.py")
        self.assertIn(ghost, outcome.missing)
        self.assertNotIn(ghost, outcome.searched)
        self.assertNotIn("Ghost", fm.describe_scope(outcome))

    def test_unreadable_directory_does_not_crash(self):
        real_walk = os.walk

        def failing_walk(top, topdown=True, onerror=None, **kw):
            if onerror:
                onerror(PermissionError("denied"))
            return real_walk(top, topdown=topdown, onerror=onerror, **kw)

        with mock.patch.object(fm.os, "walk", failing_walk):
            outcome = fm.search("main.py")
        self.assertGreater(outcome.errors, 0)
        self.assertEqual(len(outcome.results), 2)

    def test_scan_cap_marks_search_incomplete(self):
        with mock.patch.object(config, "MAX_FILES_PER_FOLDER", 1):
            outcome = fm.search("python files")
        self.assertFalse(outcome.complete)

    def test_cap_in_one_folder_does_not_hide_other_folders(self):
        # the original used ONE shared cap, so a big Documents hid Downloads
        for i in range(30):
            self.write(os.path.join(self.docs, f"filler{i}.dat"), "x")
        with mock.patch.object(config, "MAX_FILES_PER_FOLDER", 10):
            outcome = fm.search("voice.py")
        self.assertIn(self.voice_path, outcome.results)

    def test_keep_previous_results_when_speculative_search_is_empty(self):
        fm.search("python files")
        before = fm.get_last_results()
        fm.search("zzzzqqq", keep_previous_if_empty=True)
        self.assertEqual(fm.get_last_results(), before)
        fm.search("zzzzqqq")
        self.assertEqual(fm.get_last_results(), [])

    def test_results_are_capped_and_ordered_newest_first_for_types(self):
        old = self.write(os.path.join(self.docs, "old.py"), "#")
        os.utime(old, (1, 1))
        results = fm.search("python files").results
        self.assertEqual(results[-1], old)


# =====================================================================
# FILE READING
# =====================================================================

class TestRead(FakeHomeTestCase):

    def test_read_each_supported_type(self):
        for path, needle in [
            (self.main_real, "def main"),
            (self.notes, "milk"),
            (self.docx_path, "Quarterly report"),
            (self.pdf_path, "Hello PDF"),
            (os.path.join(self.docs, "data.csv"), "Ann"),
            (os.path.join(self.docs, "cfg.json"), '"a"'),
            (os.path.join(self.docs, "readme.md"), "Title"),
        ]:
            result = fm.read_file(path)
            self.assertTrue(result.ok, (path, result.error))
            self.assertIn(needle, result.content)

    def test_pdf_reports_pages(self):
        self.assertEqual(fm.read_file(self.pdf_path).pages, 1)

    def test_scanned_pdf_is_an_error_not_content(self):
        result = fm.read_file(self.blank_pdf)
        self.assertFalse(result.ok)
        self.assertEqual(result.content, "")
        self.assertIn("no extractable text", result.error)

    def test_unsupported_missing_and_outside_paths(self):
        self.assertIn("can't read", fm.read_file(self.png).error)
        self.assertFalse(fm.read_file(os.path.join(self.docs, "no.txt")).ok)
        outside = tempfile.NamedTemporaryFile(
            suffix=".txt", delete=False)
        outside.close()
        try:
            result = fm.read_file(outside.name)
            self.assertFalse(result.ok)
            self.assertIn("only read files inside", result.error)
        finally:
            os.remove(outside.name)

    def test_failed_read_keeps_previous_context(self):
        fm.read_file(self.main_real)
        fm.read_file(self.blank_pdf)           # fails
        self.assertEqual(fm.get_last_read_file(), self.main_real)
        self.assertIn("def main", fm.get_last_read_content())

    def test_large_file_is_flagged_truncated(self):
        with mock.patch.object(config, "MAX_READ_BYTES", 20):
            result = fm.read_file(self.main_real)
        self.assertTrue(result.ok)
        self.assertTrue(result.truncated)
        self.assertTrue(fm.get_last_read_truncated())

    def test_cp1252_text_file(self):
        path = os.path.join(self.docs, "latin.txt")
        with open(path, "wb") as handle:
            handle.write("caf\xe9".encode("cp1252"))
        self.assertIn("caf", fm.read_file(path).content)


# =====================================================================
# 6-9. ROUTER: selection, context, multi-step
# =====================================================================

class TestRouterSearchAndSelection(FakeHomeTestCase):

    def test_search_exact_reports_count_and_remembers(self):
        self.assertTrue(self.run_cmd("search for main.py"))
        self.assertIn("2 files named main.py", self.said)
        self.assertEqual(len(fm.get_last_results()), 2)

    def test_natural_phrasings(self):
        for phrase in [
            "search a file name called main.py",
            "find the file named main.py",
            "search for a file named main.py",
            "can you please find the file called main dot py",
            "search for main dot py",
        ]:
            self.run_cmd(phrase)
            self.assertEqual(len(fm.get_last_results()), 2, phrase)

    def test_spoken_underscore_filename(self):
        self.run_cmd("find the file named file underscore manager dot py")
        self.assertEqual(fm.get_last_results(), [self.fm_path])
        self.assertIn("file_manager.py in the jarvis_claude folder", self.said)

    def test_single_result_becomes_focus(self):
        self.run_cmd("search for voice.py")
        self.assertEqual(fm.get_focus(), self.voice_path)

    def test_type_search_message(self):
        self.run_cmd("search for python files")
        self.assertIn("4 Python files", self.said)

    def test_not_found_names_only_searched_folders(self):
        ghost = os.path.join(self.home, "Ghost")
        with mock.patch.object(fm, "SEARCH_FOLDERS", [self.docs, ghost]):
            self.run_cmd("search for zzz.py")
        self.assertIn("Documents", self.said)
        self.assertNotIn("couldn't find a file named zzz.py in Documents "
                         "and Ghost", self.said)
        self.assertIn("Ghost", self.said)       # reported as NOT checked
        self.assertIn("couldn't check Ghost", self.said)

    def test_read_numbered_results(self):
        self.run_cmd("search for python files")
        results = fm.get_last_results()
        self.run_cmd("read the first file")
        self.assertEqual(fm.get_last_read_file(), results[0])
        self.run_cmd("read the second result")
        self.assertEqual(fm.get_last_read_file(), results[1])
        self.run_cmd("read number 3")
        self.assertEqual(fm.get_last_read_file(), results[2])
        self.run_cmd("read the last one")
        self.assertEqual(fm.get_last_read_file(), results[-1])

    def test_out_of_range_and_no_results(self):
        self.run_cmd("read the first file")
        self.assertIn("don't have any search results", self.said)
        self.run_cmd("search for voice.py")
        self.run_cmd("read the fifth result")
        self.assertIn("no result number 5", self.said)
        self.run_cmd("read number 0")
        self.assertIn("no result", self.said)

    def test_read_by_name_single(self):
        self.run_cmd("read voice.py")
        self.assertEqual(fm.get_last_read_file(), self.voice_path)
        self.assertIn("I've read voice.py", self.said)

    def test_read_by_name_ambiguous_asks_then_answer(self):
        self.run_cmd("read main.py")
        self.assertTrue(sa.has_pending())
        self.assertIn("Which one should I read", self.said)
        self.assertIsNone(fm.get_last_read_file())
        self.spoken.clear()
        self.assertTrue(sa.handle_pending_reply("the second one"))
        self.assertIsNotNone(fm.get_last_read_file())
        self.assertFalse(sa.has_pending())

    def test_pending_cancel_and_expiry(self):
        self.run_cmd("read main.py")
        self.assertTrue(sa.handle_pending_reply("never mind"))
        self.assertFalse(sa.has_pending())
        self.run_cmd("read main.py")
        sa._pending.expires = time.time() - 1
        self.assertFalse(sa.has_pending())

    def test_pending_ignored_when_user_moves_on(self):
        self.run_cmd("read main.py")
        self.assertFalse(sa.handle_pending_reply("what time is it"))
        self.assertFalse(self.run_cmd("what time is it"))
        self.assertFalse(sa.has_pending())

    def test_show_more(self):
        for i in range(25):
            self.write(os.path.join(self.docs, f"bulk{i:02d}.txt"), "x")
        self.run_cmd("search for text files")
        self.assertIn("say show more", self.said)
        self.run_cmd("show more results")
        self.assertIn("11 to 20", self.said)
        self.run_cmd("show more")
        self.assertIn("21 to", self.said)

    def test_missing_target_asks_question_and_accepts_answer(self):
        self.run_cmd("search for files")
        self.assertIn("What filename or file type", self.said)
        self.assertTrue(sa.has_pending())
        self.assertTrue(sa.handle_pending_reply("voice.py"))
        self.assertEqual(fm.get_last_results(), [self.voice_path])


class TestRouterContext(FakeHomeTestCase):

    def test_this_that_it_resolve_to_last_read(self):
        self.run_cmd("read file_manager.py")
        for phrase in ["what does this file do", "explain that file",
                       "what does it do", "explain what it does",
                       "tell me what this file does"]:
            self.ai_prompts.clear()
            self.assertTrue(self.run_cmd(phrase), phrase)
            self.assertEqual(len(self.ai_prompts), 1, phrase)
            self.assertIn("def find_file", self.ai_prompts[0], phrase)

    def test_search_result_and_last_read_are_distinct(self):
        self.run_cmd("read voice.py")
        self.run_cmd("search for python files")        # several results
        self.assertTrue(fm.results_newer_than_focus())
        self.run_cmd("read that file")                 # ambiguous -> asks
        self.assertTrue(sa.has_pending())
        self.assertIn("search results", self.said)
        sa.reset_context()
        self.run_cmd("read voice.py")
        self.run_cmd("search for python files")
        self.ai_prompts.clear()
        self.run_cmd("what does this file do")         # about the loaded file
        self.assertIn("def speak", self.ai_prompts[0])

    def test_context_survives_between_commands(self):
        self.run_cmd("read main.py")
        sa.handle_pending_reply("first")
        loaded = fm.get_last_read_file()
        self.run_cmd("open notepad") if False else None
        self.run_cmd("what does this file do")
        self.run_cmd("what does this file do")
        self.assertEqual(fm.get_last_read_file(), loaded)

    def test_no_file_in_mind(self):
        self.assertTrue(self.run_cmd("what does this file do"))
        self.assertIn("don't have a file in mind", self.said)
        self.assertEqual(self.ai_prompts, [])

    def test_pronoun_without_file_goes_to_ai(self):
        self.assertFalse(self.run_cmd("what does it do"))
        self.assertFalse(self.run_cmd("explain it"))

    def test_unreadable_selection_does_not_lose_previous_file(self):
        self.run_cmd("read voice.py")
        self.run_cmd("read scan.pdf")
        self.assertIn("no extractable text", self.said)
        self.assertEqual(fm.get_last_read_file(), self.voice_path)

    def test_filename_phrase_never_becomes_part_of_name(self):
        self.run_cmd("read voice.py and tell me what this file does")
        self.assertEqual(fm.get_last_read_file(), self.voice_path)
        self.assertEqual(len(self.ai_prompts), 1)
        self.assertNotIn("couldn't find", self.said)


class TestMultiStep(FakeHomeTestCase):

    def test_search_then_read_that_file(self):
        self.assertTrue(self.run_cmd(
            "search for voice.py and read that file"))
        self.assertEqual(fm.get_last_read_file(), self.voice_path)
        spoken = self.spoken
        self.assertLess(
            next(i for i, s in enumerate(spoken) if "I found" in s),
            next(i for i, s in enumerate(spoken) if "I've read" in s))

    def test_search_then_read_that_file_when_ambiguous_asks(self):
        self.run_cmd("search for main.py and read that file")
        self.assertTrue(sa.has_pending())
        self.assertIsNone(fm.get_last_read_file())
        self.assertIn("Which one", self.said)
        sa.handle_pending_reply("number 2")
        self.assertIsNotNone(fm.get_last_read_file())
        self.assertIn("I've read main.py", self.said)

    def test_search_python_read_second_explain(self):
        self.run_cmd("search for python files, read the second result, "
                     "and explain what it does")
        results = fm.get_last_results()
        self.assertEqual(fm.get_last_read_file(), results[1])
        self.assertEqual(len(self.ai_prompts), 1)
        with open(results[1], encoding="utf-8") as handle:
            self.assertIn(handle.read().strip().splitlines()[0],
                          self.ai_prompts[0])
        order = [s for s in self.spoken]
        self.assertIn("I found 4 Python files", order[0])
        self.assertTrue(any("I've read" in s for s in order))
        self.assertIn("About", order[-1])

    def test_read_name_and_explain_after_choice(self):
        self.run_cmd("read main.py and tell me what this file does")
        self.assertTrue(sa.has_pending())
        self.assertEqual(self.ai_prompts, [])      # nothing explained yet
        sa.handle_pending_reply("the first one")
        self.assertEqual(len(self.ai_prompts), 1)  # remaining step ran

    def test_failed_step_stops_the_rest_and_does_not_claim_success(self):
        self.run_cmd("search for zzz_missing.py and read that file")
        self.assertIn("couldn't find", self.said)
        self.assertIn("did not run the remaining steps", self.said)
        self.assertNotIn("I've read", self.said)
        self.assertIsNone(fm.get_last_read_file())

    def test_unknown_clause_means_no_split(self):
        # not every clause is a system command -> whole sentence to the AI
        self.assertFalse(self.run_cmd("tell me a joke and what is love"))

    def test_open_two_apps(self):
        started = []
        with mock.patch.object(sa, "_start_target",
                               lambda c: started.append(c) or True):
            self.run_cmd("open notepad and calculator")
        self.assertEqual(started, ["notepad.exe", "calc.exe"])


class TestNotHijackingConversation(FakeHomeTestCase):
    """The original router answered these with 'I couldn't find a file...'."""

    def test_ordinary_sentences_fall_through_to_ai(self):
        for phrase in [
            "explain quantum computing", "open the door", "read the book",
            "find my keys", "what is a function",
            "explain the first law of thermodynamics",
            "how do i take a screenshot", "tell me about photosynthesis",
            "what is the weather", "search for the best pizza near me",
            "open java script tutorial",
        ]:
            self.assertFalse(self.run_cmd(phrase), phrase)
            self.assertEqual(self.spoken, [], phrase)

    def test_failed_speculative_search_keeps_results(self):
        self.run_cmd("search for python files")
        before = fm.get_last_results()
        self.assertFalse(self.run_cmd("find my keys"))
        self.assertEqual(fm.get_last_results(), before)

    def test_app_names_inside_filenames_do_not_launch_apps(self):
        started = []
        with mock.patch.object(sa, "_start_target",
                               lambda c: started.append(c) or True):
            self.run_cmd("open notepad.py")
            self.run_cmd("read calculator.txt")
        self.assertEqual(started, [])

    def test_selector_needs_standalone_ordinal(self):
        self.assertIsNone(sa.parse_selector(
            "explain the first law of physics"))
        self.assertEqual(sa.parse_selector("read the first file"), 0)
        self.assertEqual(sa.parse_selector("open the second"), 1)
        self.assertEqual(sa.parse_selector("number three"), 2)


# =====================================================================
# 10. ACCURATE EXPLANATIONS
# =====================================================================

class TestExplanations(FakeHomeTestCase):

    def test_prompt_contains_real_source_and_verified_facts(self):
        self.run_cmd("read main.py and tell me what this file does")
        sa.handle_pending_reply("the first one")
        prompt = self.ai_prompts[0]
        chosen = fm.get_last_read_file()
        with open(chosen, encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn(body.strip().splitlines()[0], prompt)
        self.assertIn("VERIFIED FACTS", prompt)

    def test_facts_list_exactly_the_real_functions(self):
        with open(self.main_real, encoding="utf-8") as handle:
            content = handle.read()
        facts = explainer.analyze_python(content, ["helpers"])
        self.assertEqual([f.name for f in facts.functions],
                         ["find_wake_word", "main"])
        self.assertTrue(facts.has_main_guard)
        self.assertEqual(facts.local_imports[0][0], "helpers")
        main_func = facts.functions[1]
        self.assertEqual(main_func.calls_defined, ["find_wake_word"])

    def test_facts_for_the_real_project_files(self):
        """Every function the facts name really exists (checked with ast)."""
        for name in MODULES:
            path = os.path.join(PROJECT, name + ".py")
            source = read_project_file(name)
            facts = explainer.analyze_python(source, MODULES)
            self.assertTrue(facts.ok, name)
            real = {n.name for n in ast.walk(ast.parse(source))
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            listed = {f.name for f in facts.functions}
            self.assertTrue(listed <= real, name)

    def test_invented_function_answer_is_rejected(self):
        self.ai_answer = ("It uses fake_helper() and the wake_word_engine "
                          "to hear you.")
        self.run_cmd("read file_manager.py")
        self.run_cmd("explain this file")
        self.assertIn("not in this file", self.said)
        self.assertNotIn("fake_helper", self.said)
        self.assertIn("find_file", self.said)         # verified summary

    def test_grounded_answer_is_spoken(self):
        self.ai_answer = "It defines find_file, which returns a list."
        self.run_cmd("read file_manager.py")
        self.run_cmd("explain this file")
        self.assertIn("It defines find_file", self.said)

    def test_model_offline_gives_verified_summary(self):
        self.ai_answer = None
        self.run_cmd("read file_manager.py")
        self.run_cmd("what does this file do")
        self.assertIn("isn't responding", self.said)
        self.assertIn("find_file", self.said)
        self.assertIn("Store", self.said)

    def test_list_functions_is_deterministic_no_ai(self):
        self.run_cmd("read main.py")
        sa.handle_pending_reply("first")
        self.ai_prompts.clear()
        self.run_cmd("what functions does it contain")
        self.assertEqual(self.ai_prompts, [])
        self.assertTrue("main" in self.said)

    def test_list_functions_exact(self):
        self.run_cmd("read file_manager.py")
        self.ai_prompts.clear()
        self.run_cmd("what functions does it contain")
        self.assertIn("find_file", self.said)
        self.assertIn("Store.save", self.said)
        self.assertEqual(self.ai_prompts, [])
        self.run_cmd("what does it import")
        self.assertIn("os", self.said)

    def test_explain_a_function_uses_its_exact_source(self):
        self.run_cmd("read file_manager.py")
        self.ai_prompts.clear()
        self.run_cmd("explain the find file function")
        prompt = self.ai_prompts[0]
        self.assertIn("def find_file(name):", prompt)
        self.assertNotIn("class Store", prompt)       # only that function
        self.assertIn("Function: find_file(name)", prompt)

    def test_function_spoken_and_underscore_names(self):
        self.run_cmd("read file_manager.py")
        for phrase in ["what does the find underscore file function do",
                       "explain the function find_file",
                       "what does find_file do"]:
            self.ai_prompts.clear()
            self.assertTrue(self.run_cmd(phrase), phrase)
            self.assertEqual(len(self.ai_prompts), 1, phrase)

    def test_unknown_function_is_not_invented(self):
        self.run_cmd("read file_manager.py")
        self.ai_prompts.clear()
        self.run_cmd("explain the quantum flux function")
        self.assertIn("don't see a function called quantum flux", self.said)
        self.assertEqual(self.ai_prompts, [])

    def test_function_without_loaded_file(self):
        self.run_cmd("explain the find file function")
        self.assertIn("read a file first", self.said)

    def test_what_does_x_do_without_match_goes_to_ai(self):
        self.run_cmd("read file_manager.py")
        self.assertFalse(self.run_cmd("what does photosynthesis do"))

    def test_large_file_is_acknowledged_as_partial(self):
        big = self.write(os.path.join(self.docs, "big.txt"),
                         "word " * 5000)
        self.run_cmd("read big.txt")
        self.run_cmd("explain this file")
        self.assertIn("Note:", self.said)
        self.assertIn("BEGIN", self.ai_prompts[0])
        self.assertIn("only the first", self.ai_prompts[0])
        self.assertLess(len(self.ai_prompts[0]),
                        config.PROMPT_SOURCE_CHARS + 3000)

    def test_non_python_files_get_facts(self):
        self.run_cmd("read data.csv")
        self.run_cmd("explain this file")
        self.assertIn("name, age", self.ai_prompts[0])
        self.ai_prompts.clear()
        self.run_cmd("read cfg.json")
        self.run_cmd("explain this file")
        self.assertIn("keys: a, b", self.ai_prompts[0])

    def test_plurals_read_naturally(self):
        self.run_cmd("read voice.py")
        self.assertIn("1 function and 0 classes", self.said)

    def test_empty_file(self):
        self.write(os.path.join(self.docs, "empty.txt"), "")
        self.run_cmd("read empty.txt")
        self.run_cmd("explain this file")
        self.assertIn("is empty", self.said)

    def test_syntax_error_python_file(self):
        self.write(os.path.join(self.docs, "broken.py"), "def x(:\n  pass\n")
        self.run_cmd("read broken.py")
        self.assertIn("isn't valid Python", self.said)

    def test_ai_history_not_polluted_by_file_prompts(self):
        brain.reset_conversation()
        with mock.patch.object(brain.ollama, "chat",
                               return_value={"message": {"content": "ok"}}):
            sa.ask_about_text = brain.ask_about_text   # real function
            try:
                self.run_cmd("read voice.py")
                self.run_cmd("explain this file")
            finally:
                pass
        for message in brain.conversation_history:
            self.assertLess(len(message["content"]), 200)


# =====================================================================
# 11. WAKE WORD
# =====================================================================

class TestWakeWord(unittest.TestCase):

    def test_accepted(self):
        cases = {
            "jarvis open notepad": "open notepad",
            "Jarvis, open Notepad.": "open notepad",
            "hey jarvis open notepad": "open notepad",
            "ok jarvis, what is a list": "what is a list",
            "javis take a screenshot": "take a screenshot",
            "jervis, read main.py": "read main.py",
            "javas open chrome": "open chrome",
            "jarvis's open notepad": "open notepad",
            "java open notepad": "open notepad",
            "java, what is a list": "what is a list",
            "javascript open notepad": "open notepad",
            "java script search for main.py": "search for main.py",
            "open notepad jarvis": "open notepad",
            "jarvis": "",
            "hey jarvis": "",
        }
        for heard, expected in cases.items():
            self.assertEqual(jarvis_main.extract_command(heard), expected,
                             heard)

    def test_rejected(self):
        for heard in [
            "good boy, jarvis",
            "i was telling my friend about jarvis yesterday",
            "do you like jarvis",
            "java is a programming language",
            "javascript is great",
            "i love java",
            "the javas are loud",
            "my java script is broken",
            "open the java folder",
            "tell jarvis later",
            "",
        ]:
            self.assertIsNone(jarvis_main.extract_command(heard), heard)

    def test_only_the_wake_word_is_removed(self):
        self.assertEqual(
            jarvis_main.extract_command("jarvis open the jarvis folder"),
            "open the jarvis folder")

    def test_compat_find_wake_word(self):
        self.assertEqual(jarvis_main.find_wake_word("jarvis hello"), "jarvis")
        self.assertIsNone(jarvis_main.find_wake_word("good boy jarvis"))


# =====================================================================
# 14. RESET AND EXIT + MAIN LOOP
# =====================================================================

class TestResetExitAndLoop(FakeHomeTestCase):

    def test_exit_commands(self):
        for yes in ["goodbye", "exit", "shutdown", "shut down", "quit",
                    "good bye", "goodbye jarvis", "exit now", "please exit"]:
            self.assertTrue(jarvis_main.is_exit_command(yes), yes)
        for no in ["how do i exit a loop", "what does exit mean in main.py",
                   "explain how to shutdown windows", "stop the music",
                   "what is the goodbye song about", "open notepad"]:
            self.assertFalse(jarvis_main.is_exit_command(no), no)

    def test_reset_commands(self):
        for yes in ["reset conversation", "clear the conversation",
                    "start a new conversation", "forget our conversation"]:
            self.assertTrue(jarvis_main.is_reset_command(yes), yes)
        self.assertFalse(jarvis_main.is_reset_command("open notepad"))

    def run_loop(self, heard_sequence, ai=None):
        said = []
        sequence = iter(heard_sequence)

        def fake_listen():
            item = next(sequence)
            if isinstance(item, BaseException):
                raise item
            return item

        asked = []

        def fake_ai(text):
            asked.append(text)
            return "ai answer"

        with mock.patch.object(jarvis_main, "listen", fake_listen), \
                mock.patch.object(jarvis_main, "speak",
                                  lambda t: said.append(t) or True), \
                mock.patch.object(jarvis_main, "ask_ai", fake_ai), \
                mock.patch.object(jarvis_main, "setup_logging"), \
                mock.patch.object(jarvis_main.time, "sleep"):
            jarvis_main.main()
        return said, asked

    def test_loop_exit_and_ai_routing(self):
        said, asked = self.run_loop([
            "", "hello there everybody",           # no wake word: ignored
            "jarvis what is a list",               # -> AI
            "jarvis how do i exit a loop",         # -> AI, NOT exit
            "jarvis",                              # -> Yes?
            "jarvis goodbye",                      # exit
            "jarvis never reached",
        ])
        self.assertEqual(asked, ["what is a list", "how do i exit a loop"])
        self.assertIn("Yes?", said)
        self.assertIn("Goodbye. I will be here when you need me.", said)
        self.assertNotIn("ai answer never", " ".join(said))

    def test_loop_reset_clears_conversation_and_file_context(self):
        fm.set_last_results([self.voice_path])
        brain.conversation_history.append({"role": "user", "content": "hi"})
        said, _ = self.run_loop(["jarvis reset conversation", "jarvis exit"])
        self.assertEqual(brain.conversation_history, [])
        self.assertEqual(fm.get_last_results(), [])
        self.assertTrue(any("cleared" in s for s in said))

    def test_loop_accepts_followup_without_wake_word_only_when_pending(self):
        said, _ = self.run_loop([
            "jarvis search for main.py and read that file",
            "the second one",                    # answer, no wake word
            "jarvis exit",
        ])
        self.assertIsNotNone(fm.get_last_read_file())
        self.assertTrue(any("I've read main.py" in s for s in self.spoken))

    def test_loop_microphone_failure_ends_cleanly(self):
        said, _ = self.run_loop([OSError("no mic")] * 10)
        self.assertTrue(any("can't use the microphone" in s for s in said))

    def test_loop_survives_one_error_and_keyboard_interrupt(self):
        said, _ = self.run_loop([OSError("blip"), "jarvis exit"])
        self.assertIn("Goodbye. I will be here when you need me.", said)
        said, _ = self.run_loop([KeyboardInterrupt()])
        self.assertIn("Goodbye.", said)

    def test_loop_system_command_error_is_reported(self):
        with mock.patch.object(jarvis_main, "execute_command",
                               side_effect=RuntimeError("boom")):
            said, _ = self.run_loop(["jarvis open notepad", "jarvis exit"])
        self.assertTrue(any("encountered an error" in s for s in said))


# =====================================================================
# 12. SPEECH OUTPUT
# =====================================================================

class TestSpeech(unittest.TestCase):

    LONG = " ".join(
        f"Sentence number {i} says it's \"quoted\", has $dollars, "
        f"`ticks`, caf\u00e9, \u2014 dashes & more." for i in range(80))

    def words(self, text):
        return voice._clean_for_speech(text).split()

    def test_split_is_lossless_and_never_cuts_words(self):
        clean = voice._clean_for_speech(self.LONG)
        chunks = voice._split_text(clean, 200)
        self.assertGreater(len(chunks), 5)
        self.assertEqual(" ".join(chunks).split(), clean.split())
        self.assertTrue(all(len(c) <= 200 for c in chunks))

    def test_split_handles_one_giant_sentence_and_giant_word(self):
        text = "word " * 400
        chunks = voice._split_text(text.strip(), 100)
        self.assertEqual(" ".join(chunks).split(), text.split())
        giant = "x" * 450
        self.assertEqual("".join(voice._split_text(giant, 100)), giant)

    def test_clean_removes_markdown_and_underscores(self):
        cleaned = voice._clean_for_speech(
            "**Bold** `find_file` ```code here``` \n# Head\n- item")
        self.assertNotIn("*", cleaned)
        self.assertNotIn("`", cleaned)
        self.assertIn("find file", cleaned)
        self.assertIn("code block", cleaned)

    def _run_speak(self, text, returncode=0):
        captured = {}

        def fake_run(args, env=None, **kwargs):
            captured["args"] = args
            with open(env["JARVIS_TTS_FILE"], encoding="utf-8") as handle:
                captured["file"] = handle.read()
            captured["file_path"] = env["JARVIS_TTS_FILE"]
            captured["timeout"] = kwargs.get("timeout")
            return types.SimpleNamespace(returncode=returncode, stderr="err")

        with mock.patch.object(voice.subprocess, "run", fake_run), \
                mock.patch("builtins.print"):
            ok = voice.speak(text)
        return ok, captured

    def test_text_never_appears_in_the_powershell_command(self):
        ok, cap = self._run_speak(self.LONG)
        self.assertTrue(ok)
        joined = " ".join(cap["args"])
        self.assertNotIn("Sentence number", joined)
        self.assertNotIn("quoted", joined)
        self.assertLess(len(joined), 4000)       # far below the 32k limit

    def test_all_text_reaches_the_file_exactly_once_in_order(self):
        ok, cap = self._run_speak(self.LONG)
        spoken = cap["file"].split()
        self.assertEqual(spoken, self.words(self.LONG))
        for i in (0, 40, 79):
            self.assertEqual(cap["file"].count(f"Sentence number {i} "), 1)

    def test_unicode_quotes_newlines_roundtrip(self):
        text = "It's \"fine\".\nSecond line \u2014 caf\u00e9 \u4f60\u597d $x `y`"
        ok, cap = self._run_speak(text)
        self.assertIn("caf\u00e9", cap["file"])
        self.assertIn("\u4f60\u597d", cap["file"])
        self.assertIn("It's \"fine\".", cap["file"])

    def test_temp_file_is_removed(self):
        ok, cap = self._run_speak("hello there")
        self.assertFalse(os.path.exists(cap["file_path"]))

    def test_failure_returns_false_and_says_so(self):
        printed = []
        with mock.patch.object(voice.subprocess, "run",
                               return_value=types.SimpleNamespace(
                                   returncode=1, stderr="bad")), \
                mock.patch.object(voice, "_speak_with_pyttsx3",
                                  return_value=False), \
                mock.patch("builtins.print",
                           lambda *a, **k: printed.append(" ".join(map(str, a)))):
            ok = voice.speak("Hello world")
        self.assertFalse(ok)
        self.assertTrue(any("Speech output failed" in p for p in printed))
        self.assertTrue(any("Hello world" in p for p in printed))

    def test_missing_powershell_and_timeout_do_not_crash(self):
        for error in (FileNotFoundError(),
                      subprocess.TimeoutExpired("powershell", 1)):
            with mock.patch.object(voice.subprocess, "run",
                                   side_effect=error), \
                    mock.patch.object(voice, "_speak_with_pyttsx3",
                                      return_value=False), \
                    mock.patch("builtins.print"):
                self.assertFalse(voice.speak("Hi"))

    def test_fallback_engine_used_when_powershell_fails(self):
        with mock.patch.object(voice.subprocess, "run",
                               side_effect=FileNotFoundError()), \
                mock.patch.object(voice, "_speak_with_pyttsx3",
                                  return_value=True), \
                mock.patch("builtins.print"):
            self.assertTrue(voice.speak("Hi"))

    def test_timeout_grows_with_length(self):
        _, short = self._run_speak("Hi.")
        _, long = self._run_speak(self.LONG)
        self.assertGreater(long["timeout"], short["timeout"])

    def test_static_script_contains_no_dynamic_text(self):
        script = voice._POWERSHELL_SCRIPT
        self.assertIn("JARVIS_TTS_FILE", script)
        self.assertNotIn("{text}", script)
        self.assertNotIn("%s", script)

    def test_empty_text_is_not_an_error(self):
        with mock.patch("builtins.print"):
            self.assertTrue(voice.speak("   "))

    def test_console_encoding_error_does_not_crash(self):
        calls = []

        def picky_print(*args, **kwargs):
            if not calls:
                calls.append(1)
                raise UnicodeEncodeError("ascii", "x", 0, 1, "bad")
            return None

        with mock.patch("builtins.print", picky_print):
            voice._safe_print("caf\u00e9")


# =====================================================================
# BRAIN
# =====================================================================

class TestBrain(unittest.TestCase):

    def setUp(self):
        brain.reset_conversation()
        self.calls = []

        def fake_chat(model, messages, options=None):
            self.calls.append((model, messages, options))
            return {"message": {"content": "answer"}}

        self.patch = mock.patch.object(brain.ollama, "chat", fake_chat)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        brain.reset_conversation()

    def test_model_and_context_come_from_config(self):
        brain.ask_ai("hi")
        model, messages, options = self.calls[0]
        self.assertEqual(model, config.MODEL)
        self.assertEqual(options["num_ctx"], config.NUM_CTX)
        self.assertEqual(messages[0]["role"], "system")

    def test_history_kept_and_reset(self):
        brain.ask_ai("one")
        brain.ask_ai("two")
        self.assertEqual(brain.get_conversation_length(), 4)
        self.assertEqual(len(self.calls[1][1]), 4)   # system + 3 messages
        brain.reset_conversation()
        self.assertEqual(brain.get_conversation_length(), 0)

    def test_history_is_bounded_and_starts_with_user(self):
        for i in range(40):
            brain.ask_ai(f"q{i}")
        self.assertLessEqual(brain.get_conversation_length(),
                             config.MAX_HISTORY_MESSAGES)
        self.assertEqual(brain.conversation_history[0]["role"], "user")

    def test_failure_removes_question_and_gives_friendly_message(self):
        def boom(*a, **k):
            raise ConnectionError("refused")
        with mock.patch.object(brain.ollama, "chat", boom), \
                mock.patch("builtins.print"):
            answer = brain.ask_ai("hello")
        self.assertIn("Is Ollama running", answer)
        self.assertEqual(brain.get_conversation_length(), 0)

    def test_missing_model_message(self):
        class NotFound(Exception):
            status_code = 404
        with mock.patch.object(brain.ollama, "chat",
                               side_effect=NotFound("x")), \
                mock.patch("builtins.print"):
            answer = brain.ask_ai("hello")
        self.assertIn("ollama pull", answer)

    def test_file_question_does_not_store_big_prompt(self):
        big = "SOURCE " * 3000
        answer = brain.ask_about_text(big, history_note="Explain x.py")
        self.assertEqual(answer, "answer")
        self.assertEqual(brain.conversation_history[0]["content"],
                         "Explain x.py")
        self.assertEqual(self.calls[0][2]["temperature"],
                         config.FILE_ANSWER_TEMPERATURE)
        self.assertNotIn(
            big, [m["content"] for m in brain.conversation_history])

    def test_file_question_failure_returns_none(self):
        with mock.patch.object(brain.ollama, "chat",
                               side_effect=ConnectionError("x")), \
                mock.patch("builtins.print"):
            self.assertIsNone(brain.ask_about_text("question"))
        self.assertEqual(brain.get_conversation_length(), 0)


# =====================================================================
# 13 + 15. ERROR HANDLING AND REGRESSION OF EXISTING FEATURES
# =====================================================================

class TestSystemFeatures(FakeHomeTestCase):

    def test_applications(self):
        started, urls = [], []
        with mock.patch.object(sa, "_start_target",
                               lambda c: started.append(c) or True), \
                mock.patch.object(sa, "_open_url",
                                  lambda u: urls.append(u) or True):
            for phrase in ["open notepad", "launch the calculator",
                           "start calc", "run chrome", "open google chrome",
                           "notepad", "calculator", "chrome",
                           "open youtube", "please open notepad for me",
                           "open notepad.exe"]:
                self.assertTrue(self.run_cmd(phrase), phrase)
        self.assertEqual(started, [
            "notepad.exe", "calc.exe", "calc.exe", "chrome", "chrome",
            "notepad.exe", "calc.exe", "chrome", "notepad.exe",
            "notepad.exe"])
        self.assertEqual(urls, ["https://www.youtube.com"])

    def test_app_failure_is_reported(self):
        with mock.patch.object(sa, "_start_target", lambda c: False):
            self.run_cmd("open notepad")
        self.assertIn("couldn't open Notepad", self.said)
        self.assertNotIn("Opening Notepad", self.said)

    def test_folders(self):
        opened = []
        with mock.patch.object(sa, "_startfile", opened.append):
            for phrase in ["downloads", "open downloads",
                           "open my documents", "my documents",
                           "open the desktop folder", "open pictures"]:
                self.assertTrue(self.run_cmd(phrase), phrase)
        self.assertEqual(opened, [self.downloads, self.downloads, self.docs,
                                  self.docs, self.desktop, self.pictures])

    def test_screenshot_saved_and_never_overwritten(self):
        class Image:
            def save(self, path):
                with open(path, "wb") as handle:
                    handle.write(b"png")

        grabber = types.SimpleNamespace(grab=lambda: Image())
        with mock.patch.object(sa, "ImageGrab", grabber):
            self.assertTrue(self.run_cmd("take a screenshot"))
            self.assertTrue(self.run_cmd("capture a screenshot"))
        shots = [f for f in os.listdir(self.pictures)
                 if f.startswith("jarvis_screenshot_")]
        self.assertEqual(len(shots), 2)
        self.assertIn("saved to your Pictures folder", self.said)

    def test_screenshot_failure_and_missing_pillow(self):
        broken = types.SimpleNamespace(
            grab=mock.Mock(side_effect=OSError("no display")))
        with mock.patch.object(sa, "ImageGrab", broken):
            self.run_cmd("take a screenshot")
        self.assertIn("could not take the screenshot", self.said)
        with mock.patch.object(sa, "ImageGrab", None):
            self.run_cmd("take a screenshot")
        self.assertIn("pillow", self.said.lower())

    def test_google_search(self):
        urls = []
        with mock.patch.object(sa, "_open_url",
                               lambda u: urls.append(u) or True):
            self.run_cmd("search google for cats & dogs")
            self.run_cmd("google search for python tutorial")
            self.run_cmd("search for raspberry pi on google")
        self.assertEqual(urls[0],
                         "https://www.google.com/search?q=cats%20%26%20dogs")
        self.assertIn("python%20tutorial", urls[1])
        self.assertIn("raspberry%20pi", urls[2])
        self.run_cmd("search google for")
        self.assertIn("What should I search for on Google", self.said)

    def test_open_numbered_and_named_files(self):
        opened, notepad = [], []
        with mock.patch.object(sa, "_startfile", opened.append), \
                mock.patch.object(sa, "_open_in_notepad", notepad.append):
            self.run_cmd("search for notes.txt")
            self.run_cmd("open the first file")
            self.run_cmd("open that file")
            self.run_cmd("open report.docx")
            self.run_cmd("open voice.py")
        self.assertEqual(opened, [self.notes, self.notes, self.docx_path])
        self.assertEqual(notepad, [self.voice_path])     # never executed

    def test_dangerous_files_are_not_launched(self):
        self.write(os.path.join(self.docs, "setup.exe"), "MZ")
        self.write(os.path.join(self.docs, "run.bat"), "echo hi")
        opened, notepad, said = [], [], []
        with mock.patch.object(sa, "_startfile", opened.append), \
                mock.patch.object(sa, "_open_in_notepad", notepad.append):
            self.run_cmd("open setup.exe")
            said.append(self.said)
            self.run_cmd("open run.bat")
            said.append(self.said)
        self.assertEqual(opened + notepad, [])
        self.assertIn("don't launch .exe", said[0])
        self.assertIn("don't launch .bat", said[1])

    def test_open_failure_reported(self):
        with mock.patch.object(sa, "_startfile",
                               side_effect=OSError("denied")):
            self.run_cmd("open notes.txt")
        self.assertIn("couldn't open that file", self.said)

    def test_file_deleted_after_search(self):
        self.run_cmd("search for notes.txt")
        os.remove(self.notes)
        self.run_cmd("read that file")
        self.assertIn("exist any more", self.said)

    def test_router_exception_is_contained(self):
        with mock.patch.object(sa.fm, "search",
                               side_effect=RuntimeError("boom")):
            handled = self.run_cmd("search for main.py")
        self.assertTrue(handled)
        self.assertIn("encountered an error", self.said)

    def test_reset_context(self):
        self.run_cmd("read voice.py")
        sa.reset_context()
        self.assertIsNone(fm.get_last_read_file())
        self.assertEqual(fm.get_last_results(), [])
        self.assertFalse(sa.has_pending())

    def test_router_never_executes_shell_text_from_speech(self):
        urls = []
        with mock.patch.object(sa.subprocess, "run") as run, \
                mock.patch.object(sa.subprocess, "Popen") as popen, \
                mock.patch.object(sa, "_open_url",
                                  lambda u: urls.append(u) or True):
            self.run_cmd("open notepad & del /q c:\\ ")
            self.run_cmd("search google for & calc")
        run.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(urls, ["https://www.google.com/search?q=%26%20calc"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
