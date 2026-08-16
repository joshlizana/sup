"""PreToolUse hook: surface the writing guidelines before a doc or source edit.

Reads the hook payload on stdin. When the target is markdown under docs/ or
Python under src/, emits the checklist as additionalContext; otherwise emits
nothing and the tool proceeds untouched.
"""
import json
import sys
from pathlib import Path

GUIDELINES = Path(__file__).with_name("writing-guidelines.md")


def wanted(path: str) -> bool:
    return (path.endswith(".md") and "/docs/" in path) or (
        path.endswith(".py") and "/src/" in path
    )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return

    path = (payload.get("tool_input") or {}).get("file_path", "")
    if not wanted(path) or not GUIDELINES.exists():
        return

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": GUIDELINES.read_text(),
        }
    }))


main()
