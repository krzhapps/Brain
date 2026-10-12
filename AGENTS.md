# AGENTS.md

This repo is a personal knowledge base, a "digital brain", owned by `human:owner`.
It is git-versioned, opened in Obsidian, and follows [OKF 0.2](resources/specs/OKF-SPEC.md).

## Map

| Path | What |
|------|------|
| `brain/` | The OKF bundle. **All knowledge lives here.** One markdown file per concept. |
| `brain/<section>/index.md` | Generated listings. Start at `brain/index.md` and descend. |
| `brain/log.md` | Newest-first change log. You add entries by hand. |
| `brain/projects/<slug>/` | One folder per project: `<slug>.md` (the Project), `ideas/`, `meetings/`. |
| `schemas/*.yaml` | Frontmatter definition per entity type (`_common.yaml` applies to all). |
| `resources/sessions/` | Agent session notes, recorded automatically for Claude Code. |
| `resources/specs/` | Reference specs. |
| `scripts/` | `lint.py`, `new.py`, `mv.py`, `record_session.py`, tests. |
| `templates/` | Obsidian templates, generated from `schemas/`. Don't edit. |

Commit or push only when asked.

## Reading

- Navigate via `index.md` files, or search frontmatter: `grep -rl "^type: Person" brain/`.
- A **concept ID** is the path under `brain/` without `.md`, e.g. `people/jane-doe`.
  A project is a folder note, so its ID repeats the slug: `projects/website/website`.
- People, organizations, vendors and products are top-level and shared across projects.
  `organizations/` holds the ones the owner works and collaborates with (employers, clients,
  partners). Companies that only make products the owner uses are `vendors/`, the target of
  `Product.vendor`. If a vendor becomes a collaborator, add an Organization note that links to
  the Vendor note instead of moving it.
- Ideas and project meetings live in the project's folder, which also makes it their owner (no
  `project:` field). Meetings that belong to no project go in `brain/meetings/`.
- Relations live in frontmatter as concept IDs (`attendees: [people/jane-doe]`).
- `status` is the *document* lifecycle (draft/stable/deprecated). `phase` is the real-world state of a project.

## Writing

1. **Find before you create.** Search `title:` and `aliases:` for an existing entity first.
2. **Use the schema.** Read `schemas/<type>.yaml`, then scaffold:
   `uv run scripts/new.py <type> "Title" -d "One sentence." --set field=value --by agent:<your-model>`
   (list fields take comma-separated values; meetings need `--set date=<ISO datetime with offset>`;
   add `--project <slug>` to put a meeting in a project's folder).
3. **Record who wrote it.** `generated: { by: <actor>, at: <ISO datetime with offset> }`. Actors are
   `human:owner` (the owner), `agent:<model>` (e.g. `agent:claude-opus-5-5`) or `process:<name>`.
   Update `generated` when you meaningfully change a concept.
4. **Link.** Put typed relations in frontmatter. Use relative markdown links in the body,
   e.g. `[Jane](../people/jane-doe.md)`, never `[[wikilinks]]`.
5. **Fix and check.** Run `uv run scripts/lint.py --fix`, then fix every remaining error.
   Warnings (orphans, unknown fields) need a reason if you leave them.
6. **Log it.** Add a line under today's `## YYYY-MM-DD` in `brain/log.md`
   (`* **Creation**:` / `**Update**:` / `**Deprecation**:` + link).

## Moving and renaming

Use `uv run scripts/mv.py <old> <new>` (add `-n` for a dry run). It rewrites frontmatter IDs and
body links everywhere. Renaming in Obsidian fixes body links only, so lint will fail afterwards.

## New entity types

Never invent a `type` ad hoc. Propose a schema to the user first; once it's agreed, add
`schemas/<type>.yaml` (copy an existing one), `mkdir brain/<dir>`, and run lint. A type that
lives in project folders sets `scopes: [project]` (or `[root, project]`) in its schema.

## Ideas

Every idea belongs to a project. `uv run scripts/new.py idea "Title" -d "..." --project <slug>`
creates `brain/projects/<slug>/ideas/NNNNN-slug/` with `idea.md`, `plan.md` and `result.md`.
Without `--project` it first creates a new Project (`phase: idea`) for the idea. Numbers are
global across projects. Keep `plan.md` current while working and fill in `result.md` when done.
Update `phase` in `idea.md` (proposed → planned → in-progress → done | dropped), and the
project's `phase` when the work becomes active.

## Sessions

Claude Code sessions are written to `resources/sessions/YYYY/MM/` by hooks, so don't write
them yourself. **Other agents:** before finishing, write
`resources/sessions/YYYY/MM/<YYYY-MM-DDTHHMMZ>-<short-id>.md` with frontmatter
`type: Agent Session`, `title`, `description`, `agent: agent:<model>`, `started`, `ended`,
`files_touched: [...]`. In the body, summarize the user's requests and what you did.

## Don't

- Edit `index.md` files, `templates/`, or the `<!-- related:start -->` blocks by hand. They are generated.
- Put knowledge outside `brain/`, or non-concept markdown inside it.
- Write secrets (tokens, keys, passwords) anywhere. The pre-commit hook rejects them.
- Rename or move concepts without updating every reference. Lint will tell you what broke.
