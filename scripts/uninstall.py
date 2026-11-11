#!/usr/bin/env python3
"""Rollback / uninstall helper for antigravity-multiagent-setup-ja.

Removes all items newly created by `install.py`, restores all pre-existing user items from
`~/.gemini/backups/setup-baseline/`, and cleans up empty directories created during install.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from install import BASELINE_DIR_NAME, PLUGINS, SKILLS


def remove_path(p: Path) -> None:
    if not p.exists() and not p.is_symlink():
        return
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    else:
        p.unlink()


def prune_empty_parents(path: Path, stop_at: Path, keep_top_dirs: set[str]) -> None:
    cur = path.parent
    while cur != stop_at and stop_at in cur.parents:
        if cur.parent == stop_at and cur.name in keep_top_dirs:
            break
        try:
            if cur.is_dir() and not any(cur.iterdir()):
                cur.rmdir()
                cur = cur.parent
            else:
                break
        except OSError:
            break


def main() -> int:
    ap = argparse.ArgumentParser(description="Restore baseline backup and uninstall Antigravity Multi-Agent Setup")
    ap.add_argument("--target-home", type=Path, default=Path.home())
    ap.add_argument("--backup-dir", type=Path, help="Specific baseline backup directory (default: setup-baseline)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    target_home = args.target_home.expanduser().resolve()
    gemini_dir = target_home / ".gemini"
    baseline_dir = args.backup_dir or (gemini_dir / "backups" / BASELINE_DIR_NAME)
    manifest_file = baseline_dir / "manifest.json"

    removed: list[str] = []
    restored: list[str] = []

    if manifest_file.is_file():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        keep_top_dirs = set(manifest.get("preexisting_top_dirs", [".gemini", ".agent", ".claude"]))

        # 1. Remove newly created items
        for rel in manifest.get("created_items", []):
            dst = target_home / rel
            if dst.exists() or dst.is_symlink():
                removed.append(rel)
                if not args.dry_run:
                    remove_path(dst)
                    prune_empty_parents(dst, target_home, keep_top_dirs)

        # 2. Restore pre-existing items from baseline/files/
        for rel in manifest.get("preexisting_items", []):
            src = baseline_dir / "files" / rel
            dst = target_home / rel
            if src.exists():
                restored.append(rel)
                if not args.dry_run:
                    remove_path(dst)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if src.is_dir():
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)

        # 3. Remove baseline directory itself and prune empty backup/top dirs
        if not args.dry_run:
            remove_path(baseline_dir)
            prune_empty_parents(baseline_dir, target_home, keep_top_dirs)
            for top_name in (".gemini", ".agent", ".claude"):
                top_dir = target_home / top_name
                if top_name not in keep_top_dirs and top_dir.is_dir() and not any(top_dir.iterdir()):
                    top_dir.rmdir()
    else:
        # Fallback if baseline manifest was manually deleted: remove managed plugins & skills
        fallback_targets = [gemini_dir / "config" / "plugins" / p for p in PLUGINS] + [
            gemini_dir / "config" / "skills" / s for s in SKILLS
        ]
        for t in fallback_targets:
            if t.exists():
                removed.append(t.relative_to(target_home).as_posix())
                if not args.dry_run:
                    remove_path(t)

    print(
        json.dumps(
            {
                "status": "dry_run_ok" if args.dry_run else "restored_ok",
                "backup_used": str(baseline_dir) if manifest_file.is_file() or args.dry_run else None,
                "removed_created_items": removed,
                "restored_preexisting_items": restored,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
