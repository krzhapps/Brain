#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""Make a fresh copy of this template yours. Run it once, right after cloning.

    uv run scripts/init.py jdoe "Jane Doe"

Sets the owner actor (`human:<id>`) in scripts/brainlib.py and AGENTS.md, creates the owner's
Person note (brain/people/<id>.md, relationship: self), logs it, and regenerates indexes and
templates with lint.py --fix.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import brainlib as bl  # noqa: E402

PLACEHOLDER = "human:owner"
ID_RE = re.compile(r"^[a-z0-9._-]+$")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id", help="your actor id, lowercase (becomes human:<id> and people/<id>)")
    ap.add_argument("name", help="your display name")
    args = ap.parse_args()

    if not ID_RE.match(args.id):
        sys.exit(f"id {args.id!r} must be lowercase letters, digits, '.', '_' or '-'")
    lib = ROOT / "scripts" / "brainlib.py"
    if PLACEHOLDER not in lib.read_text(encoding="utf-8"):
        sys.exit("already initialized: scripts/brainlib.py no longer uses human:owner")

    actor = f"human:{args.id}"
    for p in (lib, ROOT / "AGENTS.md"):
        p.write_text(p.read_text(encoding="utf-8").replace(PLACEHOLDER, actor), encoding="utf-8")

    run = lambda *a: subprocess.run(["uv", "run", "--quiet", *a], cwd=ROOT, check=True)  # noqa: E731
    run("scripts/new.py", "person", args.name, "-d", "Owner of this brain.",
        "--set", "relationship=self", "--by", actor)
    created = ROOT / "brain" / "people" / f"{args.id}.md"
    default = created.with_name(f"{bl.slugify(args.name)}.md")
    if default.exists() and default != created:
        default.rename(created)

    log = ROOT / "brain" / "log.md"
    today = dt.date.today().isoformat()
    log.write_text(log.read_text(encoding="utf-8").rstrip("\n")
                   + f"\n\n## {today}\n* **Initialization**: Created the brain for "
                   f"[{args.name}](people/{args.id}.md).\n", encoding="utf-8")

    run("scripts/lint.py", "--fix")
    print(f"Initialized for {actor}. Next: git config core.hooksPath scripts/hooks")


if __name__ == "__main__":
    main()
