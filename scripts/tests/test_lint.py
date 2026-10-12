#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""Tests for scripts/lint.py, new.py and mv.py. Run: uv run scripts/tests/test_lint.py"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import brainlib as bl  # noqa: E402
import lint  # noqa: E402
import mv  # noqa: E402
import new  # noqa: E402

REPO = SCRIPTS.parent
GEN = "generated: { by: human:test, at: 2026-10-07T10:00:00Z }"

ME = f"""---
type: Person
title: Me
description: The owner.
relationship: self
{GEN}
---

# About
"""

PROJECT = f"""---
type: Project
title: Website
description: The brain.
phase: active
people: [people/me]
{GEN}
---

# Goal
"""

MEETING = f"""---
type: Meeting
title: Kickoff
description: First meeting.
date: 2026-10-07T14:00:00+02:00
attendees: [people/me]
projects: [projects/website/website]
{GEN}
---

# Notes
"""

PROJECT_PATH = "brain/projects/website/website.md"
IDEA_DIR = "brain/projects/website/ideas/00000-x"
IDEA = {
    "idea.md": f"---\ntype: Idea\ntitle: X\ndescription: X.\nphase: proposed\n{GEN}\n---\n",
    "plan.md": f"---\ntype: Plan\ntitle: X plan\ndescription: X.\n{GEN}\n---\n",
    "result.md": f"---\ntype: Result\ntitle: X result\ndescription: X.\n{GEN}\n---\n",
}


class RepoTest(unittest.TestCase):
    """A throwaway repo with the real schemas, one person, one project, one meeting and one idea."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()  # macOS: /var -> /private/var
        shutil.copytree(REPO / "schemas", self.tmp / "schemas")
        for d in ("people", "organizations", "vendors", "projects", "meetings/2026", "products"):
            (self.tmp / "brain" / d).mkdir(parents=True)
        (self.tmp / "brain/log.md").write_text("# Log\n\n## 2026-10-07\n* **Initialization**: test.\n", encoding="utf-8")
        self.write("brain/people/me.md", ME)
        self.write(PROJECT_PATH, PROJECT)
        self.write("brain/meetings/2026/2026-10-07-kickoff.md", MEETING)
        for name, text in IDEA.items():
            self.write(f"{IDEA_DIR}/{name}", text)
        bl.set_root(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp)
        bl.set_root(REPO)

    def write(self, rel: str, text: str) -> None:
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def messages(self, severity: str = "error", fix: bool = True) -> list[str]:
        report = lint.lint(fix)
        return [f"{path}: {msg}" for sev, path, msg in report.items if sev == severity]

    def assertReports(self, needle: str, severity: str = "error") -> None:
        msgs = self.messages(severity)
        self.assertTrue(any(needle in m for m in msgs), f"{needle!r} not in {msgs}")


class LintTest(RepoTest):

    # --- the happy path ---

    def test_valid_bundle_is_clean_after_fix(self) -> None:
        self.assertEqual(self.messages("error"), [])
        self.assertEqual(self.messages("warning"), [])
        self.assertEqual(lint.lint(fix=False).count("error"), 0, "second run must be stable")

    def test_fix_writes_related_block_and_indexes(self) -> None:
        self.messages()
        meeting = (self.tmp / "brain/meetings/2026/2026-10-07-kickoff.md").read_text(encoding="utf-8")
        self.assertIn("- Attendees: [Me](../../people/me.md)", meeting)
        self.assertIn("- Projects: [Website](../../projects/website/website.md)", meeting)
        self.assertTrue(meeting.startswith("---\ntype: Meeting\n"), "frontmatter must be preserved")
        root = (self.tmp / "brain/index.md").read_text(encoding="utf-8")
        self.assertIn('okf_version: "0.2"', root)
        self.assertIn("[People](people/index.md)", root)
        self.assertIn("[Kickoff](2026-10-07-kickoff.md) - First meeting.",
                      (self.tmp / "brain/meetings/2026/index.md").read_text(encoding="utf-8"))

    def test_without_fix_generated_content_is_an_error(self) -> None:
        msgs = self.messages(fix=False)
        self.assertTrue(any("index.md is missing" in m for m in msgs))
        self.assertTrue(any("Related block is out of date" in m for m in msgs))

    # --- frontmatter ---

    def test_missing_frontmatter(self) -> None:
        self.write("brain/people/nobody.md", "# Nobody\n")
        self.assertReports("missing YAML frontmatter")

    def test_unknown_type(self) -> None:
        self.write("brain/people/thing.md", f"---\ntype: Gadget\ntitle: T\ndescription: T.\n{GEN}\n---\n")
        self.assertReports("no schema for type 'Gadget'")

    def test_required_field_and_enum(self) -> None:
        self.write(PROJECT_PATH, PROJECT.replace("phase: active", "phase: someday"))
        self.assertReports("`phase` is 'someday'")
        self.write(PROJECT_PATH, PROJECT.replace("phase: active\n", ""))
        self.assertReports("`phase` is required")

    def test_datetime_needs_offset(self) -> None:
        self.write("brain/meetings/2026/2026-10-07-kickoff.md",
                   MEETING.replace("2026-10-07T14:00:00+02:00", "2026-10-07T14:00:00"))
        self.assertReports("`date` must be an ISO 8601 datetime")

    def test_bad_actor(self) -> None:
        self.write("brain/people/me.md", ME.replace("human:test", "claude-code/opus"))
        self.assertReports("`generated.by` must be an actor")

    def test_unknown_field_warns(self) -> None:
        self.write("brain/people/me.md", ME.replace("relationship: self", "relationship: self\nshoe_size: 44"))
        self.assertReports("unknown field `shoe_size`", "warning")

    # --- relations and links ---

    def test_ref_must_exist(self) -> None:
        self.write(PROJECT_PATH, PROJECT.replace("people/me", "people/ghost"))
        self.assertReports("`people` → 'people/ghost' does not exist")

    def test_ref_must_have_right_type(self) -> None:
        self.write(PROJECT_PATH, PROJECT.replace("people/me", "projects/website/website"))
        self.assertReports("has type Project, expected Person")

    def test_product_vendor_must_be_a_vendor(self) -> None:
        self.write("brain/organizations/acme.md", f"---\ntype: Organization\ntitle: Acme\ndescription: A client.\n{GEN}\n---\n")
        self.write("brain/vendors/initech.md", f"---\ntype: Vendor\ntitle: Initech\ndescription: Makes TPS.\n{GEN}\n---\n")
        product = f"---\ntype: Product\ntitle: TPS\ndescription: Reports.\nvendor: VENDOR\n{GEN}\n---\n"
        self.write("brain/products/tps.md", product.replace("VENDOR", "vendors/initech"))
        self.assertFalse(any("tps.md" in m for m in self.messages()))
        self.write("brain/products/tps.md", product.replace("VENDOR", "organizations/acme"))
        self.assertReports("has type Organization, expected Vendor")

    def test_broken_body_link(self) -> None:
        self.write("brain/people/me.md", ME + "\nSee [nothing](../people/ghost.md).\n")
        self.assertReports("broken link → ../people/ghost.md")

    def test_links_in_code_are_ignored(self) -> None:
        self.write("brain/people/me.md", ME + "\n```\n[x](missing.md)\n```\nand `[y](missing.md)`\n")
        self.assertEqual(self.messages(), [])

    def test_orphan_warning(self) -> None:
        self.write("brain/products/obsidian.md", f"---\ntype: Product\ntitle: Obsidian\ndescription: Notes app.\n{GEN}\n---\n")
        self.assertReports("orphan", "warning")

    # --- paths ---

    def test_meeting_filename_must_match_date(self) -> None:
        (self.tmp / "brain/meetings/2026/2026-10-07-kickoff.md").rename(
            self.tmp / "brain/meetings/2026/2026-10-08-kickoff.md")
        self.assertReports("path must match meetings/")

    def test_concept_in_wrong_directory(self) -> None:
        (self.tmp / "brain/people/me.md").rename(self.tmp / "brain/products/me.md")
        self.assertReports("Person concepts belong in brain/people/")

    # --- ideas, logs, sessions ---

    def test_idea_needs_all_files(self) -> None:
        (self.tmp / IDEA_DIR / "result.md").unlink()
        self.assertReports("result.md: missing")

    def test_idea_dir_name(self) -> None:
        (self.tmp / IDEA_DIR).rename(self.tmp / "brain/projects/website/ideas/my-idea")
        self.assertReports("NNNNN-kebab-slug")

    def test_idea_file_must_have_matching_type(self) -> None:
        self.write(f"{IDEA_DIR}/plan.md", IDEA["result.md"])
        self.assertReports("plan.md: path must match ideas/{seq}-{slug}/result.md")

    def test_idea_numbers_are_global(self) -> None:
        self.write("brain/projects/other/other.md", PROJECT.replace("title: Website", "title: Other"))
        for name, text in IDEA.items():
            self.write(f"brain/projects/other/ideas/00000-y/{name}", text)
        self.assertReports("idea number 00000 is also used by projects/other/ideas/00000-y")

    # --- project folders ---

    def test_project_must_be_a_folder_note(self) -> None:
        self.write("brain/projects/website/notes.md", PROJECT)
        self.assertReports("path must match projects/{slug}/{slug}.md")

    def test_meeting_in_project_folder(self) -> None:
        (self.tmp / "brain/meetings/2026/2026-10-07-kickoff.md").unlink()
        self.write("brain/projects/website/meetings/2026/2026-10-07-kickoff.md",
                   MEETING.replace("projects: [projects/website/website]\n", ""))
        self.assertEqual(self.messages(), [])
        self.assertEqual(self.messages("warning"), [])
        meeting = (self.tmp / "brain/projects/website/meetings/2026/2026-10-07-kickoff.md").read_text(encoding="utf-8")
        self.assertIn("- Project: [Website](../../website.md)", meeting)
        self.assertIn("* [Website](website.md)", (self.tmp / "brain/projects/website/index.md").read_text(encoding="utf-8"))
        self.assertIn("* [Website](website/index.md) - The brain.", (self.tmp / "brain/projects/index.md").read_text(encoding="utf-8"))

    def test_project_lists_its_ideas_and_counts_ideas_only(self) -> None:
        self.messages()
        project = (self.tmp / PROJECT_PATH).read_text(encoding="utf-8")
        self.assertIn("- Ideas: [X](ideas/00000-x/idea.md)", project)
        self.assertIn("* [Ideas](ideas/index.md) - 1 idea", (self.tmp / "brain/projects/website/index.md").read_text(encoding="utf-8"))
        self.assertIn("* [2026](2026/index.md) - 1 meeting", (self.tmp / "brain/meetings/index.md").read_text(encoding="utf-8"))

    def test_meeting_repeating_owner_warns(self) -> None:
        self.write("brain/projects/website/meetings/2026/2026-10-08-sync.md",
                   MEETING.replace("2026-10-07T", "2026-10-08T"))
        self.assertReports("`projects` repeats projects/website/website", "warning")

    def test_project_folder_needs_project_note(self) -> None:
        self.write("brain/projects/ghost/meetings/2026/2026-10-07-kickoff.md", MEETING)
        self.assertReports("project folder has no project note (expected brain/projects/ghost/ghost.md")

    def test_root_only_type_in_project_folder(self) -> None:
        self.write("brain/projects/website/people/me.md", ME)
        self.assertReports("Person concepts belong in brain/people/")

    def test_project_only_type_at_root(self) -> None:
        self.write("brain/ideas/00001-z/idea.md", IDEA["idea.md"])
        self.assertReports("Idea concepts belong in brain/projects/<project>/ideas/")

    def test_log_headings_are_dates(self) -> None:
        self.write("brain/log.md", "# Log\n\n## Yesterday\n* stuff\n")
        self.assertReports("log heading 'Yesterday'")

    def test_session_note_type(self) -> None:
        self.write("resources/sessions/2026/10/x.md", "---\ntype: Chat\n---\n")
        self.assertReports("type: Agent Session")


class ScriptTest(RepoTest):
    """new.py and mv.py against the same fixture repo."""

    def new(self, kind: str, title: str, **kw) -> Path:
        args = argparse.Namespace(**{"description": "D.", "set": None, "tags": None, "slug": None,
                                     "project": None, "by": "human:test", **kw})
        with redirect_stdout(StringIO()):
            if kind == "idea":
                return new.new_idea(title, args)
            return new.new_concept(bl.load_schemas().by_name(kind), title, args)

    def mv(self, src: str, dst: str) -> None:
        argv = ["mv.py", str(self.tmp / src), str(self.tmp / dst)]
        with mock.patch.object(sys, "argv", argv), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(mv.main(), 0)

    def test_new_project_is_a_folder_note(self) -> None:
        self.assertEqual(self.new("project", "Garden", set=["phase=idea"]),
                         self.tmp / "brain/projects/garden/garden.md")

    def test_new_meeting_in_project(self) -> None:
        fields = ["date=2026-10-09T10:00:00+02:00", "attendees=people/me"]
        self.assertEqual(self.new("meeting", "Sync", set=fields, project="website"),
                         self.tmp / "brain/projects/website/meetings/2026/2026-10-09-sync.md")
        self.assertEqual(self.new("meeting", "Coffee", set=fields),
                         self.tmp / "brain/meetings/2026/2026-10-09-coffee.md")
        self.assertEqual(self.messages(), [])

    def test_new_idea_in_project_takes_next_global_number(self) -> None:
        self.assertEqual(self.new("idea", "Tags", project="website"),
                         self.tmp / "brain/projects/website/ideas/00001-tags")
        self.assertEqual(self.messages(), [])

    def test_new_idea_without_project_creates_one(self) -> None:
        d = self.new("idea", "Better search")
        self.assertEqual(d, self.tmp / "brain/projects/better-search/ideas/00001-better-search")
        project = (self.tmp / "brain/projects/better-search/better-search.md").read_text(encoding="utf-8")
        self.assertIn("phase: idea", project)
        self.assertEqual(self.messages(), [])

    def test_new_rejects_wrong_scope(self) -> None:
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            self.new("person", "Jane", project="website")
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            self.new("meeting", "Sync", set=["date=2026-10-09T10:00:00+02:00"], project="ghost")

    def test_mv_rewrites_ids_and_links(self) -> None:
        self.write("brain/people/me.md", ME + "\nWorks on [Website](../projects/website/website.md).\n")
        self.mv("brain/projects/website", "brain/projects/brain")
        self.mv("brain/projects/brain/website.md", "brain/projects/brain/brain.md")
        meeting = (self.tmp / "brain/meetings/2026/2026-10-07-kickoff.md").read_text(encoding="utf-8")
        self.assertIn("projects: [projects/brain/brain]", meeting)
        self.assertIn("[Website](../projects/brain/brain.md)", (self.tmp / "brain/people/me.md").read_text(encoding="utf-8"))
        self.assertEqual(self.messages(), [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
