"""
tools.py — the "hands" Jarvis has for the projects system.

Each tool is a plain Python function plus an OpenAI-style schema describing
it to the LLM. Groq's API is OpenAI-compatible, so these schemas are passed
straight through in llm.chat(..., tools=TOOL_SCHEMAS).

Adding a new capability later = write a function + add its schema here.
Nothing else in the system needs to change.
"""

import json
import db

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "create_project",
            "description": "Register a new project Jarvis should track (uni work, a side hustle, a hobby, etc).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short project name, e.g. 'PreeceStudio'"},
                    "category": {
                        "type": "string",
                        "enum": ["cybersecurity", "side-hustle", "hobby", "general"],
                    },
                    "description": {"type": "string", "description": "One-line description"},
                },
                "required": ["name", "category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_projects",
            "description": "List all tracked projects and their status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_note",
            "description": "Log a freeform update against a project, e.g. 'completed the Pre Security path'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["project_name", "note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_stat",
            "description": "Record a numeric stat against a project, e.g. revenue, sales count, rooms completed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string"},
                    "metric": {"type": "string", "description": "e.g. 'revenue_gbp', 'rooms_completed'"},
                    "value": {"type": "number"},
                },
                "required": ["project_name", "metric", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_status",
            "description": "Get the latest stats and recent notes for a specific project.",
            "parameters": {
                "type": "object",
                "properties": {"project_name": {"type": "string"}},
                "required": ["project_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_project_status",
            "description": "Change a project's status (active, paused, done).",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_name": {"type": "string"},
                    "status": {"type": "string", "enum": ["active", "paused", "done"]},
                },
                "required": ["project_name", "status"],
            },
        },
    },
]


def execute_tool(name: str, arguments: str) -> str:
    """Runs a tool call and returns a JSON string result to feed back to the LLM."""
    args = json.loads(arguments) if arguments else {}

    if name == "create_project":
        pid = db.create_project(
            args["name"], args.get("category", "general"), args.get("description", "")
        )
        return json.dumps({"ok": True, "project_id": pid})

    if name == "list_projects":
        projects = db.list_projects()
        return json.dumps([dict(p) for p in projects], default=str)

    if name == "add_note":
        ok = db.add_note(args["project_name"], args["note"])
        return json.dumps({"ok": ok})

    if name == "log_stat":
        ok = db.log_stat(args["project_name"], args["metric"], float(args["value"]))
        return json.dumps({"ok": ok})

    if name == "get_project_status":
        project = db.get_project_by_name(args["project_name"])
        if not project:
            return json.dumps({"ok": False, "error": "project not found"})
        return json.dumps(
            {
                "ok": True,
                "project": dict(project),
                "latest_stats": db.get_latest_stats(args["project_name"]),
                "recent_notes": [dict(n) for n in db.get_recent_notes(args["project_name"], limit=5)],
            },
            default=str,
        )

    if name == "set_project_status":
        ok = db.set_project_status(args["project_name"], args["status"])
        return json.dumps({"ok": ok})

    return json.dumps({"ok": False, "error": f"unknown tool: {name}"})
