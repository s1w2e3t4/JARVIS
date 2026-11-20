"""
JARVIS Brain

Handles:
- Local Ollama/Gemma responses (model comes from config.MODEL)
- Conversation history (bounded) and conversation reset
- Stateless, source-grounded questions about files

Two entry points:

    ask_ai(question)            normal conversation, keeps full history
    ask_about_text(prompt, ...) one-shot question about supplied text such as
                                a file. The big prompt is NOT stored in the
                                chat history (only a short note and the
                                answer are), so it cannot bloat or pollute
                                later conversation.
"""

import logging

import ollama

import config

log = logging.getLogger("jarvis.brain")


# ==========================================
# SYSTEM PROMPTS
# ==========================================

SYSTEM_PROMPT = (
    "You are JARVIS, a helpful personal AI assistant. "
    "You can have natural conversations with the user. "
    "You can listen to their day-to-day thoughts, frustrations, "
    "questions, jokes, and casual conversations like a friendly "
    "assistant. "
    "Use the conversation history to understand references and "
    "follow-up questions. "
    "Be warm, natural, and conversational when appropriate. "
    "When the user asks a factual or technical question, answer "
    "accurately and clearly. "
    "Do not pretend to be human. "
    "Do not use emojis or markdown formatting because your answer "
    "will be spoken aloud. "
    "Keep normal responses reasonably concise unless the user "
    "asks for a detailed explanation."
)

FILE_SYSTEM_PROMPT = (
    "You are JARVIS explaining a real file for a voice assistant. "
    "Use ONLY the supplied facts and file contents. "
    "Never mention a function, class, module, feature or behaviour that is "
    "not in the supplied material. "
    "If something is not shown, say that it is not shown. "
    "Do not use markdown, bullet characters or emojis because your answer "
    "will be spoken aloud. "
    "Keep the answer clear and reasonably concise."
)


# ==========================================
# CONVERSATION MEMORY
# ==========================================

conversation_history = []

_last_error = None


def _trim_history():
    """Keep memory bounded so long sessions cannot overflow the model."""

    limit = max(2, int(config.MAX_HISTORY_MESSAGES))

    while len(conversation_history) > limit:
        conversation_history.pop(0)

    # History must always start with a user message.
    while conversation_history and conversation_history[0]["role"] != "user":
        conversation_history.pop(0)


def describe_error(error) -> str:
    """Turn an Ollama/connection exception into a helpful spoken message."""

    status = getattr(error, "status_code", None)

    if status == 404:
        return (
            f"My AI model {config.MODEL} is not installed. "
            f"Run: ollama pull {config.MODEL}"
        )

    return (
        "I'm having trouble reaching my AI model. "
        "Is Ollama running?"
    )


def get_last_error():
    """Return the most recent AI error message (or None)."""
    return _last_error


def _chat(messages, temperature=None) -> str:
    """Call Ollama. Raises on failure."""

    options = {"num_ctx": int(config.NUM_CTX)}

    if temperature is not None:
        options["temperature"] = temperature

    response = ollama.chat(
        model=config.MODEL,
        messages=messages,
        options=options,
    )

    return str(response["message"]["content"]).strip()


# ==========================================
# ASK AI (CONVERSATION)
# ==========================================

def ask_ai(question: str) -> str:
    """
    Send a question to Ollama while preserving the conversation history.
    """

    global _last_error

    question = str(question).strip()

    if not question:
        return "I'm listening."

    user_message = {"role": "user", "content": question}
    conversation_history.append(user_message)
    _trim_history()

    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(conversation_history)

        answer = _chat(messages)

        if not answer:
            raise ValueError("The model returned an empty answer.")

        conversation_history.append(
            {"role": "assistant", "content": answer}
        )
        _trim_history()

        _last_error = None
        return answer

    except Exception as error:
        log.warning("AI error: %s", error)
        print("AI error:", error)

        _last_error = describe_error(error)

        # Remove the user's message if the AI request failed.
        if conversation_history and conversation_history[-1] is user_message:
            conversation_history.pop()

        return _last_error


# ==========================================
# ASK ABOUT SUPPLIED TEXT (FILES)
# ==========================================

def ask_about_text(prompt: str, history_note: str = None):
    """
    Ask a one-shot question about supplied text (for example a file).

    - Uses a strict, grounded system prompt and a low temperature.
    - Does NOT put the large prompt into the chat history.
    - On success, stores a short note + the answer so that conversational
      follow-ups ("tell me more") still make sense.
    - Returns the answer, or None if the model could not be reached
      (see get_last_error()). Callers should then fall back to a
      deterministic summary instead of inventing one.
    """

    global _last_error

    prompt = str(prompt).strip()

    if not prompt:
        return None

    try:
        answer = _chat(
            [
                {"role": "system", "content": FILE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=config.FILE_ANSWER_TEMPERATURE,
        )

        if not answer:
            raise ValueError("The model returned an empty answer.")

    except Exception as error:
        log.warning("AI file-question error: %s", error)
        print("AI error:", error)
        _last_error = describe_error(error)
        return None

    _last_error = None

    note = history_note or "Please explain the file we just discussed."

    conversation_history.append({"role": "user", "content": note})
    conversation_history.append({"role": "assistant", "content": answer})
    _trim_history()

    return answer


# ==========================================
# RESET CONVERSATION
# ==========================================

def reset_conversation():
    """Clear all conversation memory."""

    conversation_history.clear()


# ==========================================
# MEMORY INFORMATION
# ==========================================

def get_conversation_length():
    """Return the number of stored conversation messages."""

    return len(conversation_history)
