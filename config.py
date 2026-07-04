"""
config.py — loads settings from environment variables (or a local .env file
via python-dotenv). Keeps API keys out of source code.
"""

import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_USER_ID = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "")  # your Telegram user id — locks the bot to you only

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

SYSTEM_PROMPT = """You are Jarvis, a personal assistant for Nick.

You track his ongoing projects — currently his cybersecurity degree
(coursework, TryHackMe/HackTheBox practice) and side-hustle ventures
(e.g. PreeceStudio) — plus general day-to-day life stuff.

You have tools to create projects, log notes and stats against them, and
pull up a project's current status. Use them whenever the conversation is
clearly about a tracked project. If someone mentions a new project or
venture that isn't tracked yet, offer to create it rather than losing the
context.

Keep responses conversational and concise — this is a chat interface, not
a report. Only go long when he's asked for a proper summary or digest.
"""


def require_config():
    missing = [
        name
        for name, val in [
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
            ("GROQ_API_KEY", GROQ_API_KEY),
            ("GEMINI_API_KEY", GEMINI_API_KEY),
        ]
        if not val
    ]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}. "
            f"Copy .env.example to .env and fill them in."
        )
