"""
voice.py — gives Jarvis ears and a voice.

Input:  transcribe() sends a voice message's audio to Groq's Whisper API
        (part of the same free tier as the chat models — no extra cost,
        no separate signup).

Output: synthesize() uses Piper — a fully local, CPU-only neural TTS
        engine — to generate speech, then ffmpeg converts it to OGG/Opus,
        the specific format Telegram requires for a message to render as
        a proper voice-note bubble rather than a generic audio file.

        Piper replaced an earlier gTTS-based version: gTTS needs a network
        round trip to Google for every single reply (measured at ~2.6s of
        a ~5.5s total response time), which is the dominant cost in the
        whole pipeline. Piper runs the model directly on the server's CPU
        with no network call at all, and is designed to be real-time even
        on modest hardware (it runs live on a Raspberry Pi 5) — so this
        should cut voice-reply latency roughly in half.
"""

import os
import time
import wave
import subprocess
import tempfile
import logging
from groq import Groq
from piper import PiperVoice
from piper.config import SynthesisConfig

log = logging.getLogger("jarvis.voice")

_groq_client = None
_piper_voice = None

PIPER_MODEL_DIR = os.environ.get(
    "PIPER_MODEL_DIR", os.path.join(os.path.dirname(__file__), "piper_voices")
)
# Swapped from en_GB-alan-medium, which came across as flat/sluggish —
# northern_english_male has a warmer, livelier delivery while staying British.
PIPER_VOICE_NAME = os.environ.get("PIPER_VOICE_NAME", "en_GB-northern_english_male-medium")

# length_scale controls speaking pace in Piper/VITS: 1.0 is the model's
# trained default, lower values speak faster (0.85 ≈ ~15% quicker), higher
# values slower. This is independent of which voice model is loaded — it's
# a dial on delivery speed, not a different voice.
PIPER_LENGTH_SCALE = float(os.environ.get("PIPER_LENGTH_SCALE", "0.88"))


def _groq():
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _groq_client


def _piper() -> PiperVoice:
    """
    Lazily loads the Piper voice model once and reuses it for every reply
    (loading the ONNX model has real overhead — you don't want to pay that
    cost on every single message). If the voice files aren't downloaded
    yet, this raises a clear error telling you exactly what to run.
    """
    global _piper_voice
    if _piper_voice is None:
        model_path = os.path.join(PIPER_MODEL_DIR, f"{PIPER_VOICE_NAME}.onnx")
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"Piper voice model not found at {model_path}. Run:\n"
                f"  python3 -m piper.download_voices {PIPER_VOICE_NAME} "
                f"--download-dir {PIPER_MODEL_DIR}"
            )
        _piper_voice = PiperVoice.load(model_path)
    return _piper_voice


def transcribe(audio_path: str) -> str:
    """Takes a path to a downloaded voice message file, returns the transcribed text."""
    client = _groq()
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-large-v3-turbo",
            file=(os.path.basename(audio_path), f.read()),
            response_format="text",
        )
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
        t0 = time.perf_counter()
        voice = _piper()
        t_loaded = time.perf_counter()

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = os.path.join(tmpdir, "reply.wav")
            ogg_path = os.path.join(tmpdir, "reply.ogg")

            with wave.open(wav_path, "wb") as wav_file:
                syn_config = SynthesisConfig(length_scale=PIPER_LENGTH_SCALE)
                voice.synthesize_wav(text, wav_file, syn_config=syn_config)
            t_synthesized = time.perf_counter()

            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", wav_path,
                    "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1",
                    ogg_path,
                ],
                check=True,
                timeout=30,
            )
            t_converted = time.perf_counter()

            final_path = tempfile.mktemp(suffix=".ogg")
            os.rename(ogg_path, final_path)

            log.info(
                "Synthesize breakdown (s) — model_ready: %.2f, piper_inference: %.2f, ffmpeg_convert: %.2f",
                t_loaded - t0,
                t_synthesized - t_loaded,
                t_converted - t_synthesized,
            )
            return final_path
    except Exception as e:
        log.warning(f"Voice synthesis failed ({e}), falling back to text reply")
        return None