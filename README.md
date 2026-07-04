# Jarvis — Personal Projects Assistant

A Telegram-based personal assistant that tracks your ongoing projects (cybersecurity coursework, side hustles, hobbies) with persistent notes and daily stats. Automatically generates morning briefings and lets you chat naturally — "I completed the Pre Security path today" gets logged as a note without any command syntax.

**Features:**
- Projects registry: organize by category (cybersecurity, side-hustle, hobby, general)
- Freeform note logging and numeric stat tracking (rooms completed, revenue, streak days, etc.)
- LLM-powered natural language chat — the assistant decides when to call tools based on what you say
- Daily briefing: cron-scheduled morning digest of all active projects pushed to Telegram
- Runs entirely on free-tier APIs (Groq + Gemini, no GPU needed, no per-call billing)
- Always-on deployment on Oracle Cloud's free tier VM

**Quick start:**
1. Get free API keys (Telegram bot, Groq, Gemini — all no credit card)
2. Copy `.env.example` to `.env` and fill in your keys
3. `pip install -r requirements.txt && python3 bot.py`
4. Talk to it on Telegram like a person

**Architecture:**
SQLite on a free-forever always-on VM. The bot (Telegram interface) and digest (cron script) both read/write the same database, so your data lives in one place and every project's history compounds over time. Adding a new data source later (GitHub commits, Etsy sales, TryHackMe stats) is just a new connector script that calls the same `db.log_stat()` function — no schema changes needed.




