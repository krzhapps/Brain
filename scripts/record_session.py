#!/usr/bin/env python3
"""Render a Claude Code transcript into resources/sessions/YYYY/MM/<start>-<id>.md.

Called by the Stop and SessionEnd hooks in .claude/settings.json with the hook
payload ({session_id, transcript_path, ...}) on stdin. It can also be run by
hand: `python3 scripts/record_session.py <transcript.jsonl>`.

Only rendered markdown is written; the raw JSONL is never copied. Thinking and
tool output are omitted, tool calls are summarized in one line, and known
secret patterns are redacted. Stdlib only, and it always exits 0 so a failure
can never block the agent.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSIONS = ROOT / "resources" / "sessions"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from redaction import redact  # noqa: E402  (stdlib-only, unlike brainlib)

EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SKIP_PREFIXES = ("<local-command-", "<command-message>", "<system-reminder>", "<task-notification>")
IDEA_RE = re.compile(r"\b(projects/[a-z0-9-]+/ideas/\d{5}-[a-z0-9]+(?:-[a-z0-9]+)*)")  # with or without brain/


def parse_ts(s: str | None) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel(path: str) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return path


def one_line(s: str, n: int = 140) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def demote(text: str) -> str:
    """Push markdown headings down two levels so they nest under ## User / ## Assistant."""
    return re.sub(r"^(#{1,4}) ", lambda m: "#" * (len(m.group(1)) + 2) + " ", text, flags=re.M)


def tool_summary(name: str, inp: dict) -> str:
    if name == "Bash":
        detail = inp.get("description") or inp.get("command", "")
    elif "file_path" in inp:
        detail = rel(inp["file_path"])
    elif "pattern" in inp:
        detail = inp["pattern"]
    else:
        detail = next((v for v in inp.values() if isinstance(v, str)), "")
    return f"`{name}` {one_line(detail)}".rstrip()


def user_text(content) -> str | None:
    """The human-typed part of a user message, or None for tool results and meta messages."""
    if isinstance(content, list):
        texts = [b.get("text", "") for b in content if b.get("type") == "text"]
        if not texts:
            return None
        content = "\n".join(texts)
    text = str(content).strip()
    m = re.match(r"<command-name>(.*?)</command-name>.*?(?:<command-args>(.*?)</command-args>)?", text, re.S)
    if m:
        return f"`{(m.group(1) + ' ' + (m.group(2) or '')).strip()}`"
    if not text or text.startswith(SKIP_PREFIXES):
        return None
    return text


def render(transcript: Path, last_message: str | None = None) -> tuple[Path, str] | None:
    entries = []
    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    session_id, title, cwd = transcript.stem, None, None
    models: Counter[str] = Counter()
    files: list[str] = []
    times: list[dt.datetime] = []
    blocks: list[list[str]] = []  # [kind, markdown]
    prompts: list[str] = []
    last_role = None

    for e in entries:
        if e.get("type") == "ai-title":
            title = e.get("aiTitle") or title
            continue
        if e.get("type") not in ("user", "assistant") or e.get("isSidechain") or e.get("isMeta"):
            continue
        session_id = e.get("sessionId", session_id)
        cwd = cwd or e.get("cwd")
        t = parse_ts(e.get("timestamp"))
        if t:
            times.append(t)
        msg = e.get("message") or {}
        content = msg.get("content")

        if e["type"] == "user":
            text = user_text(content)
            if text is None:
                continue
            prompts.append(text)
            stamp = t.astimezone().strftime("%Y-%m-%d %H:%M") if t else ""
            blocks.append(["user", f"## User · {stamp}\n\n{demote(text)}"])
            last_role = "user"
            continue

        if msg.get("model") and not msg["model"].startswith("<"):
            models[msg["model"]] += 1
        for b in content if isinstance(content, list) else []:
            if b.get("type") == "text" and b.get("text", "").strip():
                kind, text = "text", demote(b["text"].strip())
            elif b.get("type") == "tool_use":
                inp = b.get("input") or {}
                kind, text = "tools", f"- {tool_summary(b.get('name', '?'), inp)}"
                if b.get("name") in EDIT_TOOLS and inp.get("file_path"):
                    f = rel(inp["file_path"])
                    if f not in files:
                        files.append(f)
            else:
                continue
            if last_role != "assistant":
                blocks.append(["heading", "## Assistant"])
                last_role = "assistant"
            if kind == "tools" and blocks[-1][0] == "tools":
                blocks[-1][1] += "\n" + text
            else:
                blocks.append([kind, text])

    # The Stop hook can fire before the final reply is flushed to the transcript;
    # the payload's last_assistant_message fills that gap.
    last = (last_message or "").strip()
    if last and not any(k == "text" and t == demote(last) for k, t in blocks[-3:]):
        if last_role != "assistant":
            blocks.append(["heading", "## Assistant"])
        blocks.append(["text", demote(last)])
        times.append(dt.datetime.now(dt.timezone.utc))

    if not prompts or not times:
        return None
    started, ended = min(times), max(times)
    model = models.most_common(1)[0][0] if models else "unknown"
    ideas = sorted({m for text in prompts + files for m in IDEA_RE.findall(text)})
    typed = [p for p in prompts if not p.startswith("`/")]  # skip bare slash commands
    title = title or one_line(typed[0] if typed else prompts[0], 80)

    fm = [
        "---",
        "type: Agent Session",
        f"title: {json.dumps(one_line(title, 100), ensure_ascii=False)}",
        f"description: {json.dumps(one_line(typed[0] if typed else prompts[0], 200), ensure_ascii=False)}",
        f"agent: agent:{model}",
        f"session_id: {session_id}",
        f"started: {iso(started)}",
        f"ended: {iso(ended)}",
        f"cwd: {json.dumps(rel(cwd) if cwd else '', ensure_ascii=False)}",
        "files_touched: [" + ", ".join(json.dumps(f) for f in files) + "]",
    ]
    if ideas:
        fm.append("ideas: [" + ", ".join(f"{i}/idea" for i in ideas) + "]")  # concept IDs of the Idea notes
    fm += [f"generated: {{ by: process:record-session, at: {iso(dt.datetime.now(dt.timezone.utc))} }}", "---", ""]

    out = SESSIONS / started.strftime("%Y/%m") / f"{started.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H%MZ')}-{session_id[:8]}.md"
    body = f"# {one_line(title, 100)}\n\n" + "\n\n".join(text for _, text in blocks) + "\n"
    return out, redact("\n".join(fm) + "\n" + body)


def main() -> int:
    try:
        if len(sys.argv) > 1:
            transcript, last_message = Path(sys.argv[1]), None
        else:
            payload = json.loads(sys.stdin.read() or "{}")
            transcript = Path(payload.get("transcript_path", ""))
            last_message = payload.get("last_assistant_message")
        if not transcript.is_file():
            return 0
        result = render(transcript, last_message)
        if result:
            out, text = result
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
    except Exception as e:  # never block the agent
        print(f"record_session: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
