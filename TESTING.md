# JARVIS testing

## Automated tests (no microphone, Ollama or internet needed)

    cd C:\Users\sweth\Downloads\jarvis_claude
    jarvisenv\Scripts\activate
    python -m unittest discover -s tests -v

126 tests. They use a temporary fake home folder and replace the AI model,
text-to-speech, app launching and screenshots with fakes.

## Needs YOUR computer (could not be run in the build sandbox)

Say each line after "Jarvis" and check the result.

| # | Say | Expected |
|---|-----|----------|
| 1 | "open Notepad" / "open Calculator" / "open Chrome" / "open YouTube" | the app / site opens, JARVIS says "Opening ..." |
| 2 | "open downloads" | Downloads folder opens |
| 3 | "take a screenshot" (twice) | two different files `jarvis_screenshot_<time>.png` in Pictures |
| 4 | "search google for cats" | browser opens a Google search |
| 5 | "search for main.py" | finds files, lists them on screen, says the count |
| 6 | "read the first file" then "what does this file do" | confirms the read, then an explanation that names only real functions |
| 7 | "search for main.py and read that file" | asks "which one?" if there are several; answer "the second one" WITHOUT saying Jarvis |
| 8 | "search for python files, read the second result, and explain what it does" | search, read, explain in order |
| 9 | "what functions does it contain" / "explain the find file function" | exact function names / explanation of that function |
| 10 | "open that file" (a .txt) / (a .py) | .txt opens normally; .py opens in Notepad, never runs |
| 11 | a long question, e.g. "explain how a car engine works in detail" | the WHOLE answer is spoken, no cut-off |
| 12 | "good boy, Jarvis" / "I like Java" | nothing happens |
| 13 | "java open notepad" | Notepad opens (Java heard as Jarvis) |
| 14 | "how do I exit a loop in Python" | answered by the AI, JARVIS keeps running |
| 15 | "Jarvis goodbye" | JARVIS exits |
| 16 | unplug the microphone, start JARVIS | clear message and exit after a few retries (no endless loop) |
| 17 | stop Ollama, then ask a question / "explain this file" | "Is Ollama running?" / verified summary instead of an invented one |

If speech fails, `jarvis.log` (next to main.py) records why.
