#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""Lint the brain: frontmatter against schemas/, links, indexes, ideas, sessions.

    uv run scripts/lint.py          # check; exit 1 on errors
    uv run scripts/lint.py --fix    # also regenerate index.md files, Related blocks, templates/

--fix only rewrites generated content; it never touches what a human or agent wrote.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import brainlib as bl

IDEAS_DIR = "ideas"
IDEA_DIR_RE = re.compile(r"^(\d{5})-[a-z0-9]+(?:-[a-z0-9]+)*$")
IDEA_FILES = ("idea.md", "plan.md", "result.md")
DATE_HEADING_RE = re.compile(r"^## (.+)$", re.M)


# --- reporting ---------------------------------------------------------------

class Report:
    ORDER = {"error": 0, "warning": 1, "info": 2, "fixed": 3}

    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []

    def add(self, severity: str, path: Path | str, msg: str) -> None:
        if isinstance(path, Path):
            path = path.relative_to(bl.ROOT).as_posix() if path.is_absolute() else path.as_posix()
        self.items.append((severity, path, msg))

    def count(self, severity: str) -> int:
        return sum(1 for s, _, _ in self.items if s == severity)

    def print(self, quiet: bool) -> None:
        for sev, path, msg in sorted(self.items, key=lambda i: (self.ORDER[i[0]], i[1], i[2])):
            if quiet and sev in ("info", "warning"):
                continue
            print(f"{sev:<8} {path}: {msg}")
        counts = ", ".join(f"{self.count(s)} {s}" for s in self.ORDER if self.count(s))
        print(f"lint: {counts or 'clean'}")


# --- value checks ------------------------------------------------------------

def is_datetime(v) -> bool:
    if isinstance(v, str):
        try:
            v = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return False
    return isinstance(v, dt.datetime) and v.tzinfo is not None


def as_datetime(v):
    if isinstance(v, str):
        return dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day, tzinfo=dt.timezone.utc)
    return None


def is_date(v) -> bool:
    if isinstance(v, str):
        try:
            dt.date.fromisoformat(v)
            return True
        except ValueError:
            return False
    return isinstance(v, dt.date) and not isinstance(v, dt.datetime)


def check_event(v, name: str, need_at: bool) -> list[str]:
    if not isinstance(v, dict):
        return [f"`{name}` must be a mapping {{ by, at }}"]
    errs = []
    if not bl.ACTOR_RE.match(str(v.get("by", ""))):
        errs.append(f"`{name}.by` must be an actor like human:<id>, agent:<model> or process:<name>")
    if "at" in v or need_at:
        if not is_datetime(v.get("at")):
            errs.append(f"`{name}.at` must be an ISO 8601 datetime with a UTC offset")
    return errs


def check_value(fdef: dict, v, name: str) -> list[str]:
    """Return error messages for one field value. Refs are only shape-checked here."""
    t = fdef.get("type", "string")
    if t == "list":
        if not isinstance(v, list):
            return [f"`{name}` must be a list"]
        errs = []
        if len(v) < fdef.get("min", 0):
            errs.append(f"`{name}` needs at least {fdef['min']} item(s)")
        item_def = {k: val for k, val in fdef.items() if k not in ("type", "of", "min")}
        item_def["type"] = fdef.get("of", "string")
        for i, item in enumerate(v):
            errs += check_value(item_def, item, f"{name}[{i}]")
        return errs
    if t == "string" and not isinstance(v, str):
        return [f"`{name}` must be a string"]
    if t == "url" and not (isinstance(v, str) and v.strip()):
        return [f"`{name}` must be a URL or path"]
    if t == "int" and (not isinstance(v, int) or isinstance(v, bool)):
        return [f"`{name}` must be an integer"]
    if t == "bool" and not isinstance(v, bool):
        return [f"`{name}` must be true or false"]
    if t == "date" and not is_date(v):
        return [f"`{name}` must be a date (YYYY-MM-DD)"]
    if t == "datetime" and not is_datetime(v):
        return [f"`{name}` must be an ISO 8601 datetime with a UTC offset, e.g. 2026-10-07T14:00:00+02:00"]
    if t == "enum" and v not in fdef.get("values", []):
        return [f"`{name}` is {v!r}; expected one of {', '.join(map(str, fdef.get('values', [])))}"]
    if t == "actor" and not bl.ACTOR_RE.match(str(v)):
        return [f"`{name}` must be an actor like human:<id>, agent:<model> or process:<name>"]
    if t == "ref":
        if not isinstance(v, str) or v.endswith(".md") or v.startswith("/"):
            return [f"`{name}` must be a concept ID like people/jane-doe (no leading slash, no .md)"]
    if t == "generated":
        return check_event(v, name, need_at=True)
    if t == "verified":
        events = v if isinstance(v, list) else [v]
        return [e for i, ev in enumerate(events) for e in check_event(ev, f"{name}[{i}]", need_at=True)]
    if t == "sources":
        if not isinstance(v, list):
            return [f"`{name}` must be a list"]
        return [f"`{name}[{i}]` needs a `resource`" for i, s in enumerate(v)
                if not isinstance(s, dict) or not s.get("resource")]
    return []


# --- concepts ----------------------------------------------------------------

@dataclass
class Concept:
    path: Path
    id: str
    fm: dict
    raw_fm: str
    body: str
    schema: bl.Schema | None

    @property
    def title(self) -> str:
        return str(self.fm.get("title") or self.path.stem)


def walk_md(base: Path):
    for p in sorted(base.rglob("*.md")):
        if not any(part.startswith(".") for part in p.relative_to(base).parts):
            yield p


def load_concepts(report: Report, schemas: bl.Schemas) -> dict[str, Concept]:
    concepts: dict[str, Concept] = {}
    for p in walk_md(bl.BRAIN):
        if p.name in bl.RESERVED:
            continue
        text = p.read_text(encoding="utf-8")
        try:
            raw, body = bl.split_frontmatter(text)
            fm, _ = bl.parse_frontmatter(text)
        except bl.FrontmatterError as e:
            report.add("error", p, str(e))
            continue
        if fm is None:
            report.add("error", p, "missing YAML frontmatter (OKF requires at least `type`)")
            continue
        schema = schemas.by_type.get(fm.get("type"))
        concepts[bl.concept_id(p)] = Concept(p, bl.concept_id(p), fm, raw, body, schema)
    return concepts


def path_pattern(c: Concept) -> re.Pattern | None:
    """Turn a schema path like '{date:%Y}/{date:%Y-%m-%d}-{slug}' into a regex for this concept.

    A repeated {slug} must repeat the same value ('{slug}/{slug}'); {seq} is a 5-digit number.
    """
    out, seen = "", set()
    for literal, token in re.findall(r"([^{]*)(?:\{([^}]*)\})?", c.schema.path):
        out += re.escape(literal)
        if not token:
            continue
        if token in ("slug", "seq"):
            if token in seen:
                out += f"(?P={token})"
            else:
                out += rf"(?P<{token}>[a-z0-9]+(?:-[a-z0-9]+)*)" if token == "slug" else r"(?P<seq>\d{5})"
            seen.add(token)
            continue
        name, _, fmt = token.partition(":")
        value = as_datetime(c.fm.get(name)) if is_datetime(c.fm.get(name)) or is_date(c.fm.get(name)) else None
        if value is None:
            return None
        out += re.escape(value.strftime(fmt))
    return re.compile(rf"^{out}$")


def check_concept(c: Concept, schemas: bl.Schemas, report: Report) -> None:
    if not c.fm.get("type"):
        report.add("error", c.path, "`type` is required")
        return
    if c.schema is None:
        report.add("error", c.path, f"no schema for type {c.fm['type']!r}; add schemas/<type>.yaml or use an existing type")
        return
    fields = {**schemas.common, **c.schema.fields}
    for name, fdef in fields.items():
        v = c.fm.get(name)
        if v is None or v == "" or v == []:
            if fdef.get("required"):
                report.add("error", c.path, f"`{name}` is required")
            continue
        for err in check_value(fdef, v, name):
            report.add("error", c.path, err)
    for key in c.fm:
        if key not in fields:
            report.add("warning", c.path, f"unknown field `{key}` (not in schemas/_common.yaml or {c.schema.file.name})")
    for tag in c.fm.get("tags") or []:
        if isinstance(tag, str) and not bl.SLUG_RE.match(tag):
            report.add("warning", c.path, f"tag {tag!r} should be lowercase kebab-case")

    # Location and filename.
    loc = bl.locate(c.path)
    if loc.dir != c.schema.dir or loc.scope not in c.schema.scopes:
        report.add("error", c.path, f"{c.schema.type} concepts belong in {placement(c.schema)}")
    else:
        pat = path_pattern(c)
        inner = Path(*loc.inner).with_suffix("").as_posix()
        if pat is not None and not pat.match(inner):
            report.add("error", c.path, f"path must match {c.schema.dir}/{c.schema.path}.md (kebab-case slug)")

    stale = c.fm.get("stale_after")
    if is_datetime(stale) and as_datetime(stale) <= dt.datetime.now(dt.timezone.utc):
        report.add("info", c.path, f"stale since {stale}")


def placement(schema: bl.Schema) -> str:
    where = {"root": f"brain/{schema.dir}/", "project": f"brain/{bl.PROJECTS_DIR}/<project>/{schema.dir}/"}
    return " or ".join(where[s] for s in schema.scopes)


def check_refs(c: Concept, concepts: dict[str, Concept], report: Report, edges: set) -> None:
    if c.schema is None:
        return
    owner = bl.locate(c.path).project
    if owner:
        target = concepts.get(owner)
        if target is None or target.fm.get("type") != "Project":
            report.add("error", c.path, f"project folder has no project note (expected brain/{owner}.md, type: Project)")
        else:
            edges.add((owner, c.id))
            if owner in (c.fm.get("projects") or []):
                report.add("warning", c.path, f"`projects` repeats {owner}; the owning project is implied by the path")
    for name, fdef in c.schema.ref_fields():
        values = c.fm.get(name)
        if values in (None, "", []):
            continue
        for v in values if isinstance(values, list) else [values]:
            if not isinstance(v, str):
                continue
            target = concepts.get(v)
            if target is None:
                report.add("error", c.path, f"`{name}` → {v!r} does not exist (expected brain/{v}.md)")
            elif target.fm.get("type") != fdef.get("ref"):
                report.add("error", c.path, f"`{name}` → {v!r} has type {target.fm.get('type')}, expected {fdef.get('ref')}")
            elif v != c.id:
                edges.add((c.id, v))


def check_body_links(path: Path, body: str, report: Report, edges: set | None = None,
                     src_id: str | None = None) -> None:
    for _, target in bl.iter_links(body):
        if not target:
            continue
        dst = bl.resolve_link(path, target)
        if not dst.exists():
            report.add("error", path, f"broken link → {target}")
            continue
        if edges is not None and dst.suffix == ".md" and dst.name not in bl.RESERVED:
            try:
                dst_id = bl.concept_id(dst)
            except ValueError:
                continue  # link out of the bundle (e.g. to resources/)
            if dst_id != src_id:
                edges.add((src_id, dst_id))


# --- generated content -------------------------------------------------------

def related_block(c: Concept, concepts: dict[str, Concept]) -> str:
    if c.schema is None:
        return ""
    lines = []
    owner = bl.locate(c.path).project
    if owner in concepts:
        lines.append(f"- Project: [{concepts[owner].title}]({bl.rel_link(c.path, concepts[owner].path)})")
    for name, _ in c.schema.ref_fields():
        values = c.fm.get(name)
        values = values if isinstance(values, list) else [values] if values else []
        links = [f"[{concepts[v].title}]({bl.rel_link(c.path, concepts[v].path)})"
                 for v in values if isinstance(v, str) and v in concepts]
        if links:
            lines.append(f"- {name.replace('_', ' ').capitalize()}: {', '.join(links)}")
    if c.fm.get("type") == "Project":  # ideas live in the project's folder; list them for the graph
        ideas = sorted((i for i in concepts.values() if i.fm.get("type") == "Idea" and bl.locate(i.path).project == c.id),
                       key=lambda i: i.path)
        if ideas:
            lines.append("- Ideas: " + ", ".join(f"[{i.title}]({bl.rel_link(c.path, i.path)})" for i in ideas))
    if not lines:
        return ""
    return f"{bl.RELATED_START}\n## Related\n\n" + "\n".join(lines) + f"\n{bl.RELATED_END}\n"


def expected_body(c: Concept, concepts: dict[str, Concept]) -> str:
    block = related_block(c, concepts)
    if not block and not bl.RELATED_RE.search(c.body):
        return c.body
    stripped = bl.RELATED_RE.sub("", c.body).rstrip("\n")
    return f"{stripped}\n\n{block}" if block else stripped + "\n"


def dir_title(d: Path) -> str:
    return d.name.replace("-", " ").title() if not d.name.isdigit() else d.name


def folder_note(d: Path, concepts: dict[str, Concept]) -> Concept | None:
    """The concept that names a folder: a project's <slug>.md or an idea's idea.md."""
    for name in (f"{d.name}.md", "idea.md"):
        if c := concepts.get(bl.concept_id(d / name)):
            return c
    return None


def expected_index(d: Path, concepts: dict[str, Concept], schemas: bl.Schemas) -> str:
    loc = bl.locate(d)
    schema = schemas.by_dir(loc.dir) if loc.dir else None
    newest_first = bool(schema and schema.path.startswith("{date"))

    subdirs = sorted((p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".")),
                     key=lambda p: p.name, reverse=newest_first)
    items = [c for c in concepts.values() if c.path.parent == d]
    items.sort(key=(lambda c: c.path.name) if newest_first else (lambda c: c.title.casefold()),
               reverse=newest_first)

    def count(p: Path, dir_name: str) -> int:
        return sum(1 for c in concepts.values() if p in c.path.parents and c.schema and c.schema.dir == dir_name)

    def counted(p: Path) -> str:
        """'3 ideas', '1 meeting': counts the section's main type (by_dir), so plans and results don't inflate it."""
        s = schemas.by_dir(bl.locate(p).dir)
        if s is None:
            return ""
        n = sum(1 for c in concepts.values() if p in c.path.parents and c.fm.get("type") == s.type)
        return f" - {n} {s.dir if n != 1 else s.type.lower()}"

    def described(c: Concept, target: str) -> str:
        desc = str(c.fm.get("description") or "").strip()
        return f"* [{c.title}]({target})" + (f" - {desc}" if desc else "")

    lines = []
    if d == bl.BRAIN:
        lines += ["---", f'okf_version: "{bl.OKF_VERSION}"', "---", "", "# Brain", "",
                  "Personal knowledge bundle ([OKF 0.2](../resources/specs/OKF-SPEC.md)). "
                  "Each section below has its own index; recent changes are in the [log](log.md).",
                  "", "# Sections", ""]
        for p in subdirs:
            s = schemas.by_dir(p.name)
            desc = s.description if s else ""
            lines.append(f"* [{dir_title(p)}]({p.name}/index.md) - {desc} ({count(p, p.name)})".replace(" -  (", " - ("))
    else:
        lead = folder_note(d, concepts)
        lines += [f"# {lead.title if lead else dir_title(d)}", ""]
        if schema and not loc.inner and schema.description:
            lines += [schema.description, ""]
        if lead in items:  # a folder note (project, idea) comes before what's inside it
            lines += [described(c, c.path.name) for c in items] + ([""] if subdirs else [])
            items = []
        for p in subdirs:
            sub = folder_note(p, concepts)
            lines.append(described(sub, f"{p.name}/index.md") if sub
                         else f"* [{dir_title(p)}]({p.name}/index.md){counted(p)}")
    if items:
        if d == bl.BRAIN:
            lines += ["", "# Concepts", ""]
        elif subdirs:
            lines.append("")
        lines += [described(c, c.path.name) for c in items]
    if not subdirs and not any(c.path.parent == d for c in concepts.values()):
        lines.append("_No entries yet._")
    return "\n".join(lines) + "\n"


def expected_template(schema: bl.Schema, schemas: bl.Schemas) -> str:
    """Obsidian Templates-plugin version of a schema. new.py is the preferred way to create concepts."""
    now = "{{date:YYYY-MM-DDTHH:mm:ssZ}}"
    lines = ["---", f"type: {schema.type}", 'title: "{{title}}"', 'description: ""', "tags: []",
             f'generated: {{ by: {bl.DEFAULT_ACTOR}, at: "{now}" }}']
    for name, fdef in schema.fields.items():
        t = fdef.get("type")
        if t == "list":
            lines.append(f"{name}: []")
        elif t == "datetime" and fdef.get("required"):
            lines.append(f'{name}: "{now}"')
        elif t == "date" and fdef.get("required"):
            lines.append(f'{name}: "{{{{date:YYYY-MM-DD}}}}"')
        else:
            lines.append(f"{name}:")
    lines += ["---", "", schema.body.rstrip("\n"), ""]
    return "\n".join(lines)


def sync(path: Path, expected: str, fix: bool, report: Report, what: str) -> None:
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current == expected:
        return
    if fix:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8")
        report.add("fixed", path, f"regenerated {what}")
    else:
        report.add("error", path, f"{what} is {'missing' if current is None else 'out of date'} (run lint.py --fix)")


# --- reserved files, ideas, sessions -----------------------------------------

def check_log(path: Path, report: Report) -> None:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        report.add("error", path, "log.md must not have frontmatter (OKF §9)")
    dates = []
    for heading in DATE_HEADING_RE.findall(text):
        if not is_date(heading.strip()):
            report.add("error", path, f"log heading {heading!r} must be an ISO date (## YYYY-MM-DD)")
        else:
            dates.append(heading.strip())
    if dates != sorted(dates, reverse=True):
        report.add("warning", path, "log entries should be newest first")
    check_body_links(path, text, report)


def check_index_frontmatter(path: Path, report: Report) -> None:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n") and path.parent != bl.BRAIN:
        report.add("error", path, "only the bundle-root index.md may have frontmatter (OKF §8)")


def check_ideas(report: Report) -> None:
    """Idea folders (brain/projects/*/ideas/NNNNN-slug/) hold all three files; numbers are global."""
    seen: dict[str, Path] = {}
    for d in sorted(p for p in bl.BRAIN.glob(f"{bl.PROJECTS_DIR}/*/{IDEAS_DIR}/*") if p.is_dir()):
        m = IDEA_DIR_RE.match(d.name)
        if not m:
            report.add("error", d, "idea folders must be named NNNNN-kebab-slug")
            continue
        if m.group(1) in seen:
            report.add("error", d, f"idea number {m.group(1)} is also used by {seen[m.group(1)].relative_to(bl.BRAIN)}")
        seen.setdefault(m.group(1), d)
        for fname in IDEA_FILES:
            if not (d / fname).exists():
                report.add("error", d / fname, f"missing; every idea needs {', '.join(IDEA_FILES)}")


def check_sessions(report: Report) -> None:
    if not bl.SESSIONS.exists():
        return
    for p in walk_md(bl.SESSIONS):
        try:
            fm, _ = bl.parse_frontmatter(p.read_text(encoding="utf-8"))
        except bl.FrontmatterError as e:
            report.add("error", p, str(e))
            continue
        if not fm or fm.get("type") != "Agent Session":
            report.add("error", p, "session notes need frontmatter with `type: Agent Session`")
        elif not bl.ACTOR_RE.match(str(fm.get("agent", ""))):
            report.add("error", p, "`agent` must be an actor like agent:<model>")


# --- main --------------------------------------------------------------------

def lint(fix: bool) -> Report:
    report = Report()
    schemas = bl.load_schemas()
    for s in schemas.by_type.values():
        for name, fdef in s.ref_fields():
            if fdef.get("ref") not in schemas.by_type:
                report.add("error", s.file, f"field `{name}` refs unknown type {fdef.get('ref')!r}")
        if bad := [x for x in s.scopes if x not in bl.SCOPES]:
            report.add("error", s.file, f"unknown scopes {bad}; use {', '.join(bl.SCOPES)}")
        if "root" in s.scopes and not (bl.BRAIN / s.dir).is_dir():
            report.add("error", s.file, f"brain/{s.dir}/ does not exist")

    concepts = load_concepts(report, schemas)
    edges: set[tuple[str, str]] = set()
    for c in concepts.values():
        check_concept(c, schemas, report)
        check_refs(c, concepts, report, edges)
        check_body_links(c.path, bl.RELATED_RE.sub("", c.body), report, edges, c.id)

    linked = defaultdict(int)
    for a, b in edges:
        linked[a] += 1
        linked[b] += 1
    for c in concepts.values():
        if not linked[c.id]:
            report.add("warning", c.path, "orphan: no links to or from other concepts")

    names: dict[tuple[str, str], Concept] = {}
    for c in concepts.values():
        for n in [c.fm.get("title"), *(c.fm.get("aliases") or [])]:
            if not isinstance(n, str):
                continue
            key = (str(c.fm.get("type")), n.casefold())
            if key in names and names[key] is not c:
                report.add("warning", c.path, f"{n!r} is also used by {names[key].id}; duplicate entity?")
            names[key] = c

    for c in concepts.values():
        body = expected_body(c, concepts)
        if body != c.body:
            if fix:
                c.path.write_text(f"---\n{c.raw_fm}---\n{body}", encoding="utf-8")
                c.body = body
                report.add("fixed", c.path, "regenerated Related block")
            else:
                report.add("error", c.path, "Related block is out of date (run lint.py --fix)")

    for d in [bl.BRAIN, *sorted(p for p in bl.BRAIN.rglob("*") if p.is_dir() and not p.name.startswith("."))]:
        sync(d / "index.md", expected_index(d, concepts, schemas), fix, report, "index.md")
    for p in bl.BRAIN.rglob("index.md"):
        check_index_frontmatter(p, report)
    for p in bl.BRAIN.rglob("log.md"):
        check_log(p, report)
    for s in schemas.by_type.values():
        sync(bl.TEMPLATES / f"{s.file.stem}.md", expected_template(s, schemas), fix, report, "template")

    check_ideas(report)
    check_sessions(report)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fix", action="store_true", help="regenerate index.md files, Related blocks and templates")
    ap.add_argument("--quiet", "-q", action="store_true", help="only print errors")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    ap.add_argument("--root", type=Path, help="repo root to lint (default: this repo)")
    args = ap.parse_args()
    if args.root:
        bl.set_root(args.root)
    report = lint(args.fix)
    report.print(args.quiet)
    failed = report.count("error") or (args.strict and report.count("warning"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
