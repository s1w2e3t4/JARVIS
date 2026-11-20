# JARVIS — Python Desktop Voice Assistant

JARVIS is a modular, Windows-based Python voice assistant that listens for a wake word, routes supported desktop commands, and answers questions using a local Ollama model.

## Features

- Wake-word-based voice commands, including supported recognition variants
- Open supported applications such as Notepad and Calculator
- Capture screenshots
- Search for files and read supported document formats
- Explain Python files using source-grounded analysis
- Answer conversational questions using Ollama and `gemma3:1b`
- Spoken responses and conversation reset

## Technology

Python, SpeechRecognition, PyAudio, Windows PowerShell/SAPI (with optional pyttsx3 fallback), Ollama, Pillow, pypdf, python-docx.

**Note:** Speech recognition uses Google's web recognizer and requires internet access; the AI model runs locally through Ollama.

## Requirements

- Windows
- Python and pip
- Microphone
- Ollama installed with `gemma3:1b`

## Setup

Open PowerShell in the project directory:

```powershell
python -m venv jarvisenv
.\jarvisenv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull gemma3:1b
.\jarvisenv\Scripts\python.exe main.py
```

To activate the virtual environment in PowerShell, use `Set-ExecutionPolicy -Scope Process Bypass` only if necessary, then `.\jarvisenv\Scripts\Activate.ps1`.

## Sample commands

- "Jarvis, open Notepad"
- "Jarvis, open Calculator"
- "Jarvis, take a screenshot"
- "Jarvis, search for main.py"
- "Jarvis, what is artificial intelligence?"
- "Goodbye, Jarvis"

## Project modules

| File | Purpose |
| --- | --- |
| `main.py` | Entry point, wake word and command flow |
| `voice.py` | Microphone input and spoken output |
| `brain.py` | Ollama conversations and short-term history |
| `system_actions.py` | Command parsing and supported desktop actions |
| `file_manager.py` | File search and reading |
| `file_explainer.py` | Source-grounded file explanation |
| `config.py` | Central settings |

## Testing

Live manual tests have passed for voice input, app launch, screenshots, file search, AI question answering, and exit commands. Automated tests are described in `TESTING.md`; their results have not yet been verified in this release.

If the `tests/` directory is available:

```powershell
python -m unittest discover -s tests -v
```

## Safety and limitations

JARVIS routes system actions through an allowlist rather than executing arbitrary AI-generated commands. File access is limited to configured locations. Test desktop actions locally before relying on them. The project is under active development.
