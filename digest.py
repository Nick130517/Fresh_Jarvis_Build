"""
digest.py — the daily briefing.

Not part of the bot process. This is a standalone script meant to be fired
by cron (e.g. daily at 07:00) on your always-on VM. It reads every active
project's latest stats + recent notes straight from the database, asks the
LLM to turn that into a short natural paragraph, and pushes it to you on
Telegram — so it lands before you've even opened the app.

Cron example (edit with `crontab -e`):
    0 7 * * *  cd /home/jarvis/jarvis && /usr/bin/python3 digest.py >> digest.log 2>&1
"""

import asyncio
import db
import llm
import config
from telegram import Bot

DIGEST_PROMPT = """You write Nick's morning briefing. You'll be given raw
data for each active project (latest stats + recent notes). Turn it into a
short, natural paragraph per project — a couple of sentences each, no
headers, no bullet spam. Skip a project entirely if there's genuinely
nothing new since last time. End with a one-line overall summary if there's
more than one project. Keep the whole thing well under 150 words."""


def gather_project_data() -> str:
    projects = db.list_projects(status="active")
    if not projects:
        return "No active projects tracked yet."

    chunks = []
    for p in projects:
        stats = db.get_latest_stats(p["name"])
        notes = db.get_recent_notes(p["name"], limit=3)
        chunk = f"Project: {p['name']} ({p['category']})\n"
        chunk += f"Stats: {stats if stats else 'none logged'}\n"
        chunk += "Recent notes: " + (
            "; ".join(n["note"] for n in notes) if notes else "none"
        )
        chunks.append(chunk)
    return "\n\n".join(chunks)


def build_digest_text() -> str:
    raw_data = gather_project_data()
    result = llm.chat(
        DIGEST_PROMPT,
        [{"role": "user", "content": raw_data}],
        tools=None,
    )
    return result["text"]


async def send(text: str):
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    await bot.send_message(chat_id=config.TELEGRAM_ALLOWED_USER_ID, text=text)


def main():
    config.require_config()
    if not config.TELEGRAM_ALLOWED_USER_ID:
        raise RuntimeError(
            "TELEGRAM_ALLOWED_USER_ID must be set for digest.py to know where to send the briefing."
        )
    text = build_digest_text()
    print(text)  # also lands in digest.log via cron redirect
    asyncio.run(send(text))


if __name__ == "__main__":
    main()
