"""
bot.py — the Telegram front end for Jarvis.

Commands:
  /start              - greet, confirm it's alive
  /newproject NAME | CATEGORY | DESCRIPTION
  /projects           - list everything being tracked
  /status NAME        - latest stats + recent notes for one project
  /log NAME | NOTE     - quick freeform log entry
  /stat NAME | METRIC | VALUE   - log a numeric stat

Anything else you send is treated as a normal message: Jarvis replies via
the LLM router, and if the model decides a tool is needed (e.g. you said
"log 3 sales for PreeceStudio" in plain English) it calls it automatically.

Locked to a single Telegram user id (yours) so nobody else can talk to your
assistant even if they find the bot.
"""

import re
import logging
import db
import tools
import llm
import config

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
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
    return str(update.effective_user.id) == str(config.TELEGRAM_ALLOWED_USER_ID)


def _current_system_prompt() -> str:
    facts = {r["key"]: r["value"] for r in db.all_memories()}
    return config.build_system_prompt(facts)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text(
        "Jarvis online. Talk to me normally, or use /projects, /status, /log, /stat, /newproject."
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
    rows = db.list_projects()
    if not rows:
        await update.message.reply_text("No projects tracked yet. Try /newproject to add one.")
        return
    lines = [f"• {p['name']} — {p['category']} — {p['status']}" for p in rows]
    await update.message.reply_text("\n".join(lines))


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    name = " ".join(context.args).strip()
    project = db.get_project_by_name(name)
    if not project:
        await update.message.reply_text(f"No project called '{name}'. Check /projects.")
        return
    stats = db.get_latest_stats(name)
    notes = db.get_recent_notes(name, limit=5)
    lines = [f"{project['name']} ({project['category']}, {project['status']})"]
    if stats:
        lines.append("Stats: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    if notes:
        lines.append("Recent notes:")
        lines += [f"  - {n['note']}" for n in notes]
    await update.message.reply_text("\n".join(lines))


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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    chat_id = update.effective_chat.id
    user_text = update.message.text

    history = _history.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})
    history[:] = history[-HISTORY_TURNS:]

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    system_prompt = _current_system_prompt()

    try:
        result = llm.chat(system_prompt, history, tools=tools.TOOL_SCHEMAS)
    except RuntimeError as e:
        await update.message.reply_text(str(e))
        return

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
    await update.message.reply_text(reply)


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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Jarvis bot starting (long polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()