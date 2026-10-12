# Brain

A template for a digital brain: a git-versioned, Obsidian-friendly knowledge base of the people,
projects, meetings and organizations you deal with, and the products (and their vendors) you use.
Humans and agents both maintain it.

- **Knowledge** is in [`brain/`](brain/index.md), an [OKF 0.2](resources/specs/OKF-SPEC.md) bundle.
- **Entity types** are defined in [`schemas/`](schemas/).
- **Projects** each have a folder in [`brain/projects/`](brain/projects/index.md) holding the project
  note, its ideas (`ideas/NNNNN-slug/` with `idea.md`, `plan.md` and `result.md`) and its meetings.
- **Agent sessions** are recorded in [`resources/sessions/`](resources/sessions/).
- **Agent instructions** are in [`AGENTS.md`](AGENTS.md); [`CLAUDE.md`](CLAUDE.md) imports it.

## Setup

Copy this repo (keep it private: it will hold personal notes), then run once:

```sh
uv run scripts/init.py jdoe "Jane Doe"     # owner actor human:jdoe + brain/people/jdoe.md
git config core.hooksPath scripts/hooks    # pre-commit: secret scan + lint
```

Requires [uv](https://docs.astral.sh/uv/) (the scripts declare their own dependencies) and Python ≥ 3.11.

**Obsidian:** open the repo root as a vault. Shared settings are committed in `.obsidian/`:
markdown links, relative paths, new notes in `brain/`, and templates from `templates/`.
Templates are generated, so prefer `scripts/new.py`.

## Everyday commands

```sh
uv run scripts/new.py person "Jane Doe" -d "Designer at Acme."      # scaffold from schema
uv run scripts/new.py meeting "Kickoff" -d "..." --project website \
    --set date=2026-10-07T14:00:00+02:00 --set attendees=people/jane-doe,people/jdoe
uv run scripts/new.py idea "Weekly review" -d "..." --project website # next NNNNN idea folder
uv run scripts/new.py idea "Garden planner" -d "..."                 # new project + its first idea
uv run scripts/mv.py brain/a.md brain/b.md -n                        # move/rename, rewriting refs and links
uv run scripts/lint.py --fix                                         # regenerate indexes/Related, then check
uv run scripts/tests/test_lint.py                                    # tests for lint, new and mv
```

## Adding an entity type

1. Copy a file in `schemas/`, rename it, and set `type`, `description`, `dir`, `path`, `fields` and `body`.
   Set `scopes` to `[project]` or `[root, project]` if it should live in project folders (default `[root]`).
2. `mkdir brain/<dir>`
3. `uv run scripts/lint.py --fix` generates the index and the Obsidian template.

Field types: `string`, `date`, `datetime` (ISO 8601 with offset), `url`, `enum` (`values`),
`ref` (`ref: <Type>`), `list` (`of: <type>`, optional `min`), `bool`, `int`.

## Lint checks

Errors: missing or invalid frontmatter, unknown `type`, missing required fields, wrong field
types and enums, datetimes without an offset, invalid actors, files in the wrong directory or
with the wrong name, dangling or wrongly typed refs, broken body links, out-of-date generated
content, malformed `log.md`, idea folders missing a file or reusing a number, concepts in a
project folder without a project note, invalid session notes.

Warnings: unknown fields, orphan concepts, likely duplicate entities, non-kebab tags, a meeting
listing its owning project in `projects:`.
Info: concepts past `stale_after`.
