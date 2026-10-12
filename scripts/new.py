#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""Scaffold a concept from its schema, or a new idea directory.

    uv run scripts/new.py person "Jane Doe" -d "Designer at Acme." --set organizations=organizations/acme
    uv run scripts/new.py meeting "Website kickoff" -d "..." --project website \\
        --set date=2026-10-07T14:00:00+02:00 --set attendees=people/jane-doe,people/owner
    uv run scripts/new.py idea "Weekly review ritual" -d "..." --project website
    uv run scripts/new.py idea "Garden planner" -d "..."    # also creates projects/garden-planner/

--project puts the concept in brain/projects/<slug>/<dir>/ (meetings, ideas). Without it, a
meeting goes in brain/meetings/ and an idea gets a new project (phase: idea) of its own.

List fields take comma-separated values. The type can be given as the type
('Person'), the schema file ('person') or the directory ('people').
Set BRAIN_ACTOR (e.g. agent:claude-opus-5-5) or pass --by to record who created it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import yaml

import brainlib as bl


def now_iso() -> str:
    return dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def fmt(value, ftype: str = "string") -> str:
    """Format a value for frontmatter, quoting only when YAML would misread it."""
    if isinstance(value, list):
        return "[" + ", ".join(fmt(v) for v in value) + "]"
    s = str(value)
    if ftype in ("date", "datetime"):
        return s
    try:
        if yaml.safe_load(f"k: {s}")["k"] == s and not s.startswith(("[", "{", "&", "*", "!", "|", ">", "%", "@", "`")):
            return s
    except yaml.YAMLError:
        pass
    return json.dumps(s, ensure_ascii=False)


def render(fm: list[tuple[str, str]], body: str) -> str:
    lines = ["---", *(f"{k}: {v}" for k, v in fm), "---", ""]
    return "\n".join(lines) + "\n" + body.rstrip("\n") + "\n"


def section(schema: bl.Schema, project: str | None) -> Path:
    """The folder a concept of this schema goes in, checking the schema allows that scope."""
    if project:
        if "project" not in schema.scopes:
            sys.exit(f"{schema.type} concepts can't live in a project; drop --project")
        if not (bl.BRAIN / f"{bl.project_id(project)}.md").exists():
            sys.exit(f"no project {project!r}; create it with `new.py project ...` first")
        return bl.BRAIN / bl.PROJECTS_DIR / project / schema.dir
    if "root" not in schema.scopes:
        sys.exit(f"{schema.type} concepts live in a project; pass --project <slug>")
    return bl.BRAIN / schema.dir


def new_concept(schema: bl.Schema, title: str, args) -> Path:
    if "{seq}" in schema.path:
        sys.exit(f"{schema.type} files are created together by `new.py idea`")
    fields = {**bl.load_schemas().common, **schema.fields}
    values: dict[str, object] = {}
    for item in args.set or []:
        key, sep, raw = item.partition("=")
        if not sep or key not in fields:
            sys.exit(f"--set {item!r}: unknown field; {schema.type} fields are {', '.join(schema.fields)}")
        values[key] = [v.strip() for v in raw.split(",") if v.strip()] if fields[key].get("type") == "list" else raw
    for key, fdef in schema.fields.items():
        if key not in values and fdef.get("required") and fdef.get("type") == "datetime":
            values[key] = now_iso()
        if key not in values and fdef.get("required") and fdef.get("type") == "date":
            values[key] = dt.date.today().isoformat()

    slug = args.slug or bl.slugify(title)
    rel = schema.path
    for token in re.findall(r"\{([^}]*)\}", schema.path):
        name, _, f = token.partition(":")
        if name == "slug":
            rel = rel.replace("{slug}", slug)
            continue
        raw = values.get(name)
        if raw is None:
            sys.exit(f"{schema.type} paths need `{name}`; pass --set {name}=...")
        when = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00")) if "T" in str(raw) \
            else dt.datetime.fromisoformat(str(raw))
        rel = rel.replace("{" + token + "}", when.strftime(f))
    path = section(schema, args.project) / f"{rel}.md"
    if path.exists():
        sys.exit(f"{path.relative_to(bl.ROOT)} already exists")

    fm = [("type", schema.type), ("title", fmt(title)), ("description", fmt(args.description or ""))]
    if args.tags:
        fm.append(("tags", fmt([t.strip() for t in args.tags.split(",")])))
    fm.append(("generated", f"{{ by: {args.by}, at: {now_iso()} }}"))
    for key, fdef in schema.fields.items():
        if key in values:
            fm.append((key, fmt(values[key], fdef.get("type", "string"))))
    for key in values:
        if key not in schema.fields:
            fm.append((key, fmt(values[key], fields[key].get("type", "string"))))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(fm, schema.body), encoding="utf-8")
    return path


def new_idea(title: str, args) -> Path:
    schemas = bl.load_schemas()
    idea, plan, result = (schemas.by_type[t] for t in ("Idea", "Plan", "Result"))
    slug = args.slug or bl.slugify(title)
    desc = args.description or ""
    project = args.project
    if project is None:
        # An idea always belongs to a project; a new idea starts its own (phase: idea).
        project = slug
        if (bl.BRAIN / f"{bl.project_id(project)}.md").exists():
            sys.exit(f"project {project!r} already exists; pass --project {project} to add the idea to it")
        proj = argparse.Namespace(**{**vars(args), "set": ["phase=idea"], "project": None, "slug": slug})
        print(new_concept(schemas.by_type["Project"], title, proj).relative_to(bl.ROOT))

    numbers = [int(m.group(1)) for p in bl.BRAIN.glob(f"{bl.PROJECTS_DIR}/*/{idea.dir}/*")
               if (m := re.match(r"^(\d{5})-", p.name))]
    d = section(idea, project) / f"{(max(numbers) + 1 if numbers else 0):05d}-{slug}"
    d.mkdir(parents=True)
    gen = f"{{ by: {args.by}, at: {now_iso()} }}"
    (d / "idea.md").write_text(render(
        [("type", "Idea"), ("title", fmt(title)), ("description", fmt(desc)), ("generated", gen), ("phase", "proposed")],
        idea.body), encoding="utf-8")
    (d / "plan.md").write_text(render(
        [("type", "Plan"), ("title", fmt(f"{title}: plan")), ("description", fmt(f"How we will do: {title}.")),
         ("status", "draft"), ("generated", gen)], plan.body), encoding="utf-8")
    (d / "result.md").write_text(render(
        [("type", "Result"), ("title", fmt(f"{title}: result")), ("description", fmt(f"Outcome of: {title}.")),
         ("status", "draft"), ("generated", gen)], result.body), encoding="utf-8")
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", help="entity type (person, meeting, ...) or 'idea'")
    ap.add_argument("title")
    ap.add_argument("-d", "--description", help="one-sentence summary")
    ap.add_argument("--set", action="append", metavar="FIELD=VALUE", help="set a schema field (repeatable)")
    ap.add_argument("--tags", help="comma-separated tags")
    ap.add_argument("--slug", help="override the filename slug")
    ap.add_argument("--project", metavar="SLUG", help="put it in brain/projects/<SLUG>/ (meetings, ideas)")
    ap.add_argument("--by", default=os.environ.get("BRAIN_ACTOR", bl.DEFAULT_ACTOR), help="actor id")
    args = ap.parse_args()
    if not bl.ACTOR_RE.match(args.by):
        sys.exit(f"--by {args.by!r} must look like human:<id>, agent:<model> or process:<name>")

    if args.kind == "idea":
        path = new_idea(args.title, args)
    else:
        schema = bl.load_schemas().by_name(args.kind)
        if schema is None:
            sys.exit(f"unknown type {args.kind!r}; add schemas/{args.kind}.yaml first")
        path = new_concept(schema, args.title, args)
    print(path.relative_to(bl.ROOT))
    print("next: fill in the body, then run `uv run scripts/lint.py --fix`", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
