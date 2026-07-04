"""
bot.py — the Telegram front end for Jarvis.

Commands:
  /start              - greet, confirm it's alive, shows tappable buttons
  /newproject NAME | CATEGORY | DESCRIPTION
  /projects           - list everything being tracked, as tappable buttons
  /status NAME        - latest stats + recent notes for one project
  /log NAME | NOTE     - quick freeform log entry
  /stat NAME | METRIC | VALUE   - log a numeric stat

Anything else you send is treated as a normal message: Jarvis replies via
the LLM router, and if the model decides a tool is needed (e.g. you said
"log 3 sales for PreeceStudio" in plain English) it calls it automatically.

Voice messages are transcribed (Groq Whisper) and handled exactly like
text — and Jarvis replies with an actual spoken voice note in return,
matching the modality you used.

Locked to a single Telegram user id (yours) so nobody else can talk to your
assistant even if they find the bot.
"""

import os
import re
import time
import logging
import db
import tools
import llm
import voice
import config

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("jarvis.bot")

# in-memory short conversation history per chat, so replies have context
# without re-sending your whole life every message. Kept small on purpose —
# long-term memory lives in the database, not in this buffer.
_history: dict[int, list[dict]] = {}
HISTORY_TURNS = 8

# Safety net: strips any leftover bracket-style tool-call artifacts (e.g.
# "[remember_fact: x = y]") that a model occasionally mimics into its visible
# reply after seeing tool-call formatting earlier in the conversation.
_BRACKET_ARTIFACT_RE = re.compile(r"\[[^\[\]\n]{0,150}\]")


def _authorized(update: Update) -> bool:
    if not config.TELEGRAM_ALLOWED_USER_ID:
        return True  # not locked down yet — fine for local testing, set this before going live
    user = update.effective_user
    return user is not None and str(user.id) == str(config.TELEGRAM_ALLOWED_USER_ID)


def _current_system_prompt() -> str:
    facts = {r["key"]: r["value"] for r in db.all_memories()}
    return config.build_system_prompt(facts)


def _projects_keyboard() -> InlineKeyboardMarkup | None:
    rows = db.list_projects()
    if not rows:
        return None
    buttons = [
        [InlineKeyboardButton(f"{p['name']} ({p['category']})", callback_data=f"status:{p['name']}")]
        for p in rows
    ]
    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📋 My Projects", callback_data="list_projects")]]
    )
    await update.message.reply_text(
        "Jarvis online. Talk to me normally (text or voice), or tap below.",
        reply_markup=keyboard,
    )


async def newproject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    text = " ".join(context.args)
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 2:
        await update.message.reply_text(
            "Usage: /newproject Name | category | optional description\n"
            "Categories: cybersecurity, side-hustle, hobby, general"
        )
        return
    name, category = parts[0], parts[1]
    description = parts[2] if len(parts) > 2 else ""
    db.create_project(name, category, description)
    await update.message.reply_text(f"Tracking new project: {name} ({category})")


async def projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    keyboard = _projects_keyboard()
    if not keyboard:
        await update.message.reply_text("No projects tracked yet. Try /newproject to add one.")
        return
    await update.message.reply_text("Tap a project for its current status:", reply_markup=keyboard)


def _format_status(project_name: str) -> str:
    project = db.get_project_by_name(project_name)
    if not project:
        return f"No project called '{project_name}'. Check /projects."
    stats = db.get_latest_stats(project_name)
    notes = db.get_recent_notes(project_name, limit=5)
    lines = [f"{project['name']} ({project['category']}, {project['status']})"]
    if stats:
        lines.append("Stats: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    if notes:
        lines.append("Recent notes:")
        lines += [f"  - {n['note']}" for n in notes]
    return "\n".join(lines)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    name = " ".join(context.args).strip()
    await update.message.reply_text(_format_status(name))


async def button_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles taps on inline keyboard buttons (the 'fancier UI' layer)."""
    query = update.callback_query
    if not query or not query.from_user or (
        config.TELEGRAM_ALLOWED_USER_ID
        and str(query.from_user.id) != str(config.TELEGRAM_ALLOWED_USER_ID)
    ):
        return
    await query.answer()

    if query.data == "list_projects":
        keyboard = _projects_keyboard()
        if not keyboard:
            await query.edit_message_text("No projects tracked yet. Try /newproject to add one.")
        else:
            await query.edit_message_text("Tap a project for its current status:", reply_markup=keyboard)

    elif query.data.startswith("status:"):
        project_name = query.data.split(":", 1)[1]
        back_keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅ Back to projects", callback_data="list_projects")]]
        )
        await query.edit_message_text(_format_status(project_name), reply_markup=back_keyboard)


async def log_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    text = " ".join(context.args)
    parts = [p.strip() for p in text.split("|", 1)]
    if len(parts) != 2:
        await update.message.reply_text("Usage: /log Project name | your note text")
        return
    name, note = parts
    ok = db.add_note(name, note)
    await update.message.reply_text("Logged." if ok else f"No project called '{name}'.")


async def log_stat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    text = " ".join(context.args)
    parts = [p.strip() for p in text.split("|")]
    if len(parts) != 3:
        await update.message.reply_text("Usage: /stat Project name | metric_name | value")
        return
    name, metric, value = parts
    try:
        ok = db.log_stat(name, metric, float(value))
    except ValueError:
        await update.message.reply_text("Value must be a number.")
        return
    await update.message.reply_text("Logged." if ok else f"No project called '{name}'.")


async def _generate_reply(chat_id: int, user_text: str) -> str:
    """
    Core chat loop shared by text and voice input: maintains short rolling
    history, calls the LLM (with tools), executes any tool calls, and
    returns the final natural-language reply text.
    """
    history = _history.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})
    history[:] = history[-HISTORY_TURNS:]

    system_prompt = _current_system_prompt()

    try:
        result = llm.chat(system_prompt, history, tools=tools.TOOL_SCHEMAS)
    except RuntimeError as e:
        return str(e)

    # If the model wants to call a tool, run it, then give the model a
    # second pass to reply in natural language. The tool outcome is only
    # added to a *temporary* copy of history for this one follow-up call —
    # not saved permanently — and it's phrased as plain English rather than
    # bracket/code-style notation, because feeding the model bracket syntax
    # taught it to mimic that exact syntax back in its visible reply.
    if result["tool_calls"]:
        outcomes = []
        for call in result["tool_calls"]:
            output = tools.execute_tool(call["name"], call["arguments"])
            outcomes.append(f"{call['name']} completed successfully: {output}")

        note = (
            "(Internal system note, not something Nick said — this happened "
            "behind the scenes: " + " | ".join(outcomes) + ". Now just "
            "continue the conversation naturally, reacting to what Nick "
            "actually said. Do not describe or confirm the saving/logging "
            "action itself, do not use brackets or code syntax — respond "
            "exactly like a normal reply to his last message.)"
        )
        temp_history = history + [{"role": "user", "content": note}]
        follow_up = llm.chat(system_prompt, temp_history, tools=None)
        reply = follow_up["text"]
    else:
        reply = result["text"]

    # Some models return an empty string after a tool call (they consider the
    # action itself the "answer"). Telegram rejects empty messages outright,
    # so fall back to a plain confirmation rather than sending nothing.
    if not reply or not reply.strip():
        reply = "Done." if result["tool_calls"] else "..."

    # Safety net: strip any bracket-style artifacts that slipped through
    # anyway (belt and braces on top of the fix above).
    cleaned_reply = _BRACKET_ARTIFACT_RE.sub("", reply).strip()
    if cleaned_reply:
        reply = cleaned_reply

    history.append({"role": "assistant", "content": reply})
    return reply


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    reply = await _generate_reply(chat_id, update.message.text)
    await update.message.reply_text(reply)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Voice in, voice out — transcribes the message, runs it through the
    same brain as text, then replies with an actual spoken voice note
    (falling back to text if speech synthesis fails for any reason).

    Logs timing for each stage so real numbers are visible in
    `journalctl -u jarvis-bot`, rather than guessing where time goes."""
    if not _authorized(update):
        return
    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    t_start = time.perf_counter()

    voice_file = await update.message.voice.get_file()
    local_path = f"/tmp/voice_in_{update.message.message_id}.ogg"
    await voice_file.download_to_drive(local_path)
    t_downloaded = time.perf_counter()

    try:
        user_text = voice.transcribe(local_path)
    except Exception as e:
        log.error(f"Transcription failed: {e}")
        await update.message.reply_text("Couldn't quite make that out — mind trying again, or typing it?")
        return
    finally:
        if os.path.exists(local_path):
            os.remove(local_path)
    t_transcribed = time.perf_counter()

    if not user_text:
        await update.message.reply_text("Didn't catch anything in that one — try again?")
        return

    reply = await _generate_reply(chat_id, user_text)
    t_replied = time.perf_counter()

    voice_reply_path = voice.synthesize(reply)
    t_synthesized = time.perf_counter()

    if voice_reply_path:
        try:
            with open(voice_reply_path, "rb") as f:
                await update.message.reply_voice(voice=f, caption=reply[:1024])
        finally:
            os.remove(voice_reply_path)
    else:
        # Synthesis failed (network hiccup, etc.) — text is a fine fallback
        await update.message.reply_text(reply)
    t_sent = time.perf_counter()

    log.info(
        "Voice timing (s) — download: %.2f, transcribe: %.2f, think: %.2f, "
        "synthesize: %.2f, send: %.2f, TOTAL: %.2f",
        t_downloaded - t_start,
        t_transcribed - t_downloaded,
        t_replied - t_transcribed,
        t_synthesized - t_replied,
        t_sent - t_synthesized,
        t_sent - t_start,
    )


def main():
    config.require_config()
    db.init_db()

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newproject", newproject))
    app.add_handler(CommandHandler("projects", projects))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("log", log_note))
    app.add_handler(CommandHandler("stat", log_stat))
    app.add_handler(CallbackQueryHandler(button_tap))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Jarvis bot starting (long polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()