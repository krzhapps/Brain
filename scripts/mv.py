#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""Move a concept or a folder and rewrite everything that points at it.

    uv run scripts/mv.py brain/projects/website.md brain/projects/website/website.md
    uv run scripts/mv.py brain/projects/website/ideas/00003-x brain/projects/other/ideas/00003-x
    uv run scripts/mv.py ... --dry-run

Rewrites concept IDs in frontmatter (e.g. `projects: [projects/website]`) and relative markdown
links, both in the moved files and in every markdown file in the repo that links to them.
Session notes in resources/sessions/ are an audit trail and are left alone. Run
`uv run scripts/lint.py --fix` afterwards.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import brainlib as bl

INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def plan_moves(src: Path, dst: Path) -> dict[Path, Path]:
    """Map every old path (the folder itself, its subfolders and files) to its new path."""
    moves = {src: dst}
    if src.is_dir():
        moves |= {p: dst / p.relative_to(src) for p in src.rglob("*")}
    return moves


def concept_ids(moves: dict[Path, Path]) -> dict[str, str]:
    """Old concept ID -> new concept ID, for concepts that stay inside brain/."""
    ids = {}
    for old, new in moves.items():
        if old.suffix == ".md" and old.name not in bl.RESERVED and bl.BRAIN in old.parents and bl.BRAIN in new.parents:
            ids[bl.concept_id(old)] = bl.concept_id(new)
    return ids


def rewrite_ids(raw_fm: str, ids: dict[str, str]) -> str:
    for old, new in ids.items():
        raw_fm = re.sub(rf"(?<![\w/.-]){re.escape(old)}(?![\w/.-])", new, raw_fm)
    return raw_fm


def rewrite_links(text: str, old_file: Path, new_file: Path, moves: dict[Path, Path]) -> str:
    code = [m.span() for m in bl.FENCE_RE.finditer(text)] + [m.span() for m in INLINE_CODE_RE.finditer(text)]

    def sub(m: re.Match) -> str:
        if any(a <= m.start() < b for a, b in code):
            return m.group(0)
        target = m.group(3)
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
            return m.group(0)
        path, hash_, frag = target.partition("#")
        old_dst = bl.resolve_link(old_file, path)
        new_dst = moves.get(old_dst, old_dst)
        if new_dst == old_dst and new_file == old_file:
            return m.group(0)
        if path.startswith("/"):
            new_path = "/" + new_dst.relative_to(bl.BRAIN).as_posix()
        else:
            new_path = bl.rel_link(new_file, new_dst) + ("/" if path.endswith("/") else "")
        start, end = m.start(3) - m.start(), m.end(3) - m.start()
        return m.group(0)[:start] + new_path + hash_ + frag + m.group(0)[end:]

    return bl.LINK_RE.sub(sub, text)


def rewrite(text: str, old_file: Path, new_file: Path, moves: dict[Path, Path], ids: dict[str, str]) -> str:
    try:
        raw, body = bl.split_frontmatter(text)
    except bl.FrontmatterError:
        raw, body = None, text
    body = rewrite_links(body, old_file, new_file, moves)
    if raw is None:
        return body
    return f"---\n{rewrite_ids(raw, ids) if bl.BRAIN in old_file.parents else raw}---\n{body}"


def markdown_files() -> list[Path]:
    skip = (bl.SESSIONS, bl.TEMPLATES)
    return [p.resolve() for p in sorted(bl.ROOT.rglob("*.md"))
            if not any(part.startswith(".") for part in p.relative_to(bl.ROOT).parts)
            and not any(s in p.parents for s in skip)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--dry-run", "-n", action="store_true", help="print what would change, touch nothing")
    args = ap.parse_args()
    src, dst = args.src.resolve(), args.dst.resolve()
    if not src.exists():
        sys.exit(f"{args.src} does not exist")
    if dst.exists():
        sys.exit(f"{args.dst} already exists")
    if bl.ROOT not in src.parents or bl.ROOT not in dst.parents:
        sys.exit("both paths must be inside the repo")

    moves = plan_moves(src, dst)
    ids = concept_ids(moves)
    changed: dict[Path, str] = {}
    for f in markdown_files():
        text = f.read_text(encoding="utf-8")
        new_text = rewrite(text, f, moves.get(f, f), moves, ids)
        if new_text != text:
            changed[moves.get(f, f)] = new_text

    for old, new in ids.items():
        print(f"id    {old} -> {new}")
    print(f"move  {src.relative_to(bl.ROOT)} -> {dst.relative_to(bl.ROOT)}")
    for f in changed:
        print(f"edit  {f.relative_to(bl.ROOT)}")
    if args.dry_run:
        return 0

    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    for f, text in changed.items():
        f.write_text(text, encoding="utf-8")
    print("next: run `uv run scripts/lint.py --fix`", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
