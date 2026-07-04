"""
voice.py — gives Jarvis ears and a voice.

Input:  transcribe() sends a voice message's audio to Groq's Whisper API
        (part of the same free tier as the chat models — no extra cost,
        no separate signup).

Output: synthesize() turns a text reply into an actual spoken Telegram
        voice note. gTTS (free, no API key) generates the speech as mp3;
        ffmpeg then converts it to OGG/Opus, because that's the specific
        format Telegram requires for a message to render as a proper
        voice-note bubble rather than a generic audio file attachment.
"""

import os
import subprocess
import tempfile
import logging
from gtts import gTTS
from groq import Groq

log = logging.getLogger("jarvis.voice")

_groq_client = None


def _groq():
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _groq_client


def transcribe(audio_path: str) -> str:
    """Takes a path to a downloaded voice message file, returns the transcribed text."""
    client = _groq()
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-large-v3-turbo",
            file=(os.path.basename(audio_path), f.read()),
            response_format="text",
        )
    # response_format="text" returns the transcript directly as a string
    return str(result).strip()


def synthesize(text: str, max_chars: int = 500) -> str | None:
    """
    Turns text into a Telegram-ready OGG/Opus voice note file and returns
    its path, or None if synthesis fails (caller should fall back to a
    normal text reply rather than let the whole message fail).

    Truncates long replies — a voice note reading out a wall of text is a
    worse experience than a short spoken reply plus the full text version.
    """
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(".", 1)[0] + "."

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            mp3_path = os.path.join(tmpdir, "reply.mp3")
            ogg_path = os.path.join(tmpdir, "reply.ogg")

            gTTS(text=text, lang="en", tld="co.uk").save(mp3_path)

            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", mp3_path,
                    "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1",
                    ogg_path,
                ],
                check=True,
                timeout=30,
            )

            # Move out of the temp dir before it gets cleaned up
            final_path = tempfile.mktemp(suffix=".ogg")
            os.rename(ogg_path, final_path)
            return final_path
    except Exception as e:
        log.warning(f"Voice synthesis failed ({e}), falling back to text reply")
        return None