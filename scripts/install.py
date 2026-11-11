#!/usr/bin/env python3
"""Portable installer for antigravity-multiagent-setup-ja.

Features:
- Zero third-party dependencies (Python 3.9+ standard library only)
- `--check`: Inspects target machine capabilities (Python, Node, pnpm, uv, Claude CLI, existing ~/.gemini)
- `--dry-run`: Previews all file operations without writing anything
- Persistent baseline backup (`~/.gemini/backups/setup-baseline/`) that preserves the user's true
  pre-install state across repeated `install.py` runs and tracks newly created files for clean uninstall
- Ignores `__pycache__`, `*.pyc`, `*.pyo`, and `.DS_Store` when copying or backing up
- Dynamically resolves `~` rule paths in custom agent frontmatter for target `$HOME`
- Adapts hook runner (`python3` vs `python`) on Windows if `python3` alias is absent
- Never clobbers `~/.gemini/config/config.json`
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PAYLOAD_GEMINI = REPO_ROOT / "gemini"
PAYLOAD_TOOLS = REPO_ROOT / "tools" / "antigravity-project-setup"
PAYLOAD_CLAUDE_SKILLS = REPO_ROOT / "claude" / "skills"

PLUGINS = [
    "agy-harness",
    "multi-agent-kit",
    "multi-axis-investigation-kit",
    "claude-orchestrator-kit",
]

SKILLS = [
    "grill-me",
    "grilling",
    "package-supply-chain",
    "antigravity-project-setup",
]

IGNORE_PATTERNS = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store")
BASELINE_DIR_NAME = "setup-baseline"


def detect_cli(name: str, extra_paths: list[Path] | None = None) -> str | None:
    hit = shutil.which(name)
    if hit:
        return hit
    for p in extra_paths or []:
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def inspect_environment(target_home: Path) -> dict:
    gemini_dir = target_home / ".gemini"
    config_json = gemini_dir / "config" / "config.json"
    disabled_plugins: list[str] = []
    if config_json.is_file():
        try:
            cfg = json.loads(config_json.read_text(encoding="utf-8"))
            pmap = cfg.get("plugins") or {}
            for pname in PLUGINS:
                entry = pmap.get(pname)
                if isinstance(entry, dict) and entry.get("enabled") is False:
                    disabled_plugins.append(pname)
        except Exception:
            pass

    claude_bin = detect_cli(
        "claude",
        [
            target_home / ".local" / "bin" / "claude",
            Path("/opt/homebrew/bin/claude"),
            Path("/usr/local/bin/claude"),
            target_home / "AppData" / "Local" / "Programs" / "claude" / "claude.exe",
        ],
    )
    pnpm_bin = detect_cli("pnpm", [Path("/opt/homebrew/bin/pnpm"), Path("/usr/local/bin/pnpm")])
    uv_bin = detect_cli("uv", [target_home / ".local" / "bin" / "uv", Path("/opt/homebrew/bin/uv")])
    node_bin = detect_cli("node", [Path("/opt/homebrew/bin/node"), Path("/usr/local/bin/node")])
    python3_bin = shutil.which("python3")
    python_bin = shutil.which("python")

    return {
        "os": platform.system(),
        "platform": platform.platform(),
        "python_executable": sys.executable,
        "python3_in_path": python3_bin,
        "python_in_path": python_bin,
        "node": node_bin,
        "pnpm": pnpm_bin,
        "uv": uv_bin,
        "claude_cli": claude_bin,
        "claude_fallback_mode": (
            "external_claude_cli"
            if claude_bin
            else "native_antigravity_subagents (senior-architect-advisor / carbon-verifier / mindfulness-inspector)"
        ),
        "target_home": str(target_home),
        "existing_gemini_md": (gemini_dir / "GEMINI.md").is_file(),
        "existing_plugins": [p for p in PLUGINS if (gemini_dir / "config" / "plugins" / p).exists()],
        "existing_skills": [s for s in SKILLS if (gemini_dir / "config" / "skills" / s).exists()],
        "previously_disabled_plugins": disabled_plugins,
    }


def load_or_init_baseline(baseline_root: Path, target_home: Path) -> dict:
    manifest_file = baseline_root / "manifest.json"
    if manifest_file.is_file():
        try:
            return json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "target_home": str(target_home),
        "preexisting_top_dirs": [
            d for d in (".gemini", ".agent", ".claude") if (target_home / d).exists()
        ],
        "preexisting_items": [],
        "created_items": [],
    }


def track_target_in_baseline(
    target_path: Path,
    baseline_root: Path,
    manifest: dict,
    target_home: Path,
    dry_run: bool,
) -> None:
    rel = target_path.relative_to(target_home).as_posix()
    if rel in manifest["preexisting_items"] or rel in manifest["created_items"]:
        # Already captured during the initial baseline run; never clobber baseline state
        return

    if target_path.exists():
        manifest["preexisting_items"].append(rel)
        if not dry_run:
            dst = baseline_root / "files" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if target_path.is_dir():
                shutil.copytree(target_path, dst, dirs_exist_ok=True, ignore=IGNORE_PATTERNS)
            else:
                shutil.copy2(target_path, dst)
    else:
        manifest["created_items"].append(rel)


def count_clean_files(src: Path) -> int:
    total = 0
    for p in src.rglob("*"):
        if not p.is_file():
            continue
        if "__pycache__" in p.parts or p.name == ".DS_Store" or p.suffix in (".pyc", ".pyo"):
            continue
        total += 1
    return total


def copy_tree_clean(src: Path, dst: Path, dry_run: bool) -> int:
    count = count_clean_files(src)
    if dry_run:
        return count
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, ignore=IGNORE_PATTERNS)
    return count


def make_executable(path: Path) -> None:
    if os.name == "nt" or not path.is_file():
        return
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def patch_installed_paths(gemini_dir: Path, target_home: Path, env_info: dict, dry_run: bool) -> None:
    if dry_run:
        return

    resolved_rule = (gemini_dir / "config" / "plugins" / "multi-agent-kit" / "rules" / "AGENTS.md").resolve().as_posix()
    agents_dir = gemini_dir / "config" / "plugins" / "multi-agent-kit" / "agents"
    if agents_dir.is_dir():
        for md in agents_dir.glob("*.md"):
            txt = md.read_text(encoding="utf-8")
            updated = txt.replace(
                "~/.gemini/config/plugins/multi-agent-kit/rules/AGENTS.md",
                resolved_rule,
            )
            if updated != txt:
                md.write_text(updated, encoding="utf-8")

    hooks_json = gemini_dir / "config" / "plugins" / "agy-harness" / "hooks.json"
    if hooks_json.is_file() and not env_info["python3_in_path"] and env_info["python_in_path"]:
        txt = hooks_json.read_text(encoding="utf-8")
        hooks_json.write_text(txt.replace("python3 ./scripts/", "python ./scripts/"), encoding="utf-8")

    for py_script in gemini_dir.rglob("*.py"):
        make_executable(py_script)
    for mjs_script in gemini_dir.rglob("*.mjs"):
        make_executable(mjs_script)
    setup_js = target_home / ".agent" / "antigravity-project-setup" / "setup.js"
    make_executable(setup_js)


def install_process_gate(gemini_dir: Path, dry_run: bool) -> str:
    src_mjs = PAYLOAD_GEMINI / "scripts" / "process-review-gate.mjs"
    dst_mjs = gemini_dir / "scripts" / "process-review-gate.mjs"
    opt_hooks = json.loads((PAYLOAD_GEMINI / "config" / "hooks.optional.json").read_text(encoding="utf-8"))
    resolved_mjs = dst_mjs.resolve().as_posix()
    opt_hooks["process-review-gate"]["Stop"][0]["command"] = f'node "{resolved_mjs}"'

    dst_hooks = gemini_dir / "config" / "hooks.json"
    if dry_run:
        return f"would install {dst_mjs} and merge process-review-gate into {dst_hooks}"

    dst_mjs.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_mjs, dst_mjs)
    make_executable(dst_mjs)

    merged = {}
    if dst_hooks.is_file():
        try:
            merged = json.loads(dst_hooks.read_text(encoding="utf-8"))
        except Exception:
            merged = {}
    merged.update(opt_hooks)
    dst_hooks.parent.mkdir(parents=True, exist_ok=True)
    dst_hooks.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return f"installed {dst_mjs} and updated {dst_hooks}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Install Antigravity Multi-Agent Setup (JA)")
    ap.add_argument("--check", action="store_true", help="Run environment preflight check only (JSON output)")
    ap.add_argument("--dry-run", action="store_true", help="Preview installation without writing files")
    ap.add_argument("--target-home", type=Path, default=Path.home(), help="Target home directory (default: ~)")
    ap.add_argument("--with-process-gate", action="store_true", help="Also install optional Node.js process-review-gate Stop hook")
    ap.add_argument("--with-claude-skills", action="store_true", help="Also install grilling skill into ~/.claude/skills/")
    ap.add_argument("--skip-project-bootstrapper", action="store_true", help="Skip installing ~/.agent/antigravity-project-setup")
    args = ap.parse_args()

    target_home = args.target_home.expanduser().resolve()
    env_info = inspect_environment(target_home)

    if args.check:
        print(json.dumps(env_info, indent=2, ensure_ascii=False))
        return 0

    gemini_dir = target_home / ".gemini"
    baseline_root = gemini_dir / "backups" / BASELINE_DIR_NAME
    manifest = load_or_init_baseline(baseline_root, target_home)

    # 1. Track all targets touched in this run against the persistent pre-install baseline
    managed_targets: list[Path] = [
        gemini_dir / "GEMINI.md",
        gemini_dir / "HANDOFF.md",
    ]
    managed_targets.extend(gemini_dir / "config" / "plugins" / p for p in PLUGINS)
    managed_targets.extend(gemini_dir / "config" / "skills" / s for s in SKILLS)

    enable_process_gate = bool(args.with_process_gate and env_info["node"])
    if enable_process_gate:
        managed_targets.append(gemini_dir / "scripts" / "process-review-gate.mjs")
        managed_targets.append(gemini_dir / "config" / "hooks.json")
    if not args.skip_project_bootstrapper and PAYLOAD_TOOLS.is_dir():
        managed_targets.append(target_home / ".agent" / "antigravity-project-setup")
    if args.with_claude_skills:
        managed_targets.append(target_home / ".claude" / "skills" / "grilling")

    for tgt in managed_targets:
        track_target_in_baseline(tgt, baseline_root, manifest, target_home, args.dry_run)

    # 2. Install GEMINI.md & HANDOFF.md (if absent)
    if not args.dry_run:
        gemini_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PAYLOAD_GEMINI / "GEMINI.md", gemini_dir / "GEMINI.md")
        handoff_dst = gemini_dir / "HANDOFF.md"
        if not handoff_dst.exists():
            shutil.copy2(PAYLOAD_GEMINI / "HANDOFF.template.md", handoff_dst)

    # 3. Install 4 plugins & 4 standalone skills
    installed_files = 1
    for p in PLUGINS:
        installed_files += copy_tree_clean(
            PAYLOAD_GEMINI / "config" / "plugins" / p,
            gemini_dir / "config" / "plugins" / p,
            args.dry_run,
        )
    for s in SKILLS:
        installed_files += copy_tree_clean(
            PAYLOAD_GEMINI / "config" / "skills" / s,
            gemini_dir / "config" / "skills" / s,
            args.dry_run,
        )

    # 4. Install project bootstrapper (`~/.agent/antigravity-project-setup`)
    if not args.skip_project_bootstrapper and PAYLOAD_TOOLS.is_dir():
        installed_files += copy_tree_clean(
            PAYLOAD_TOOLS,
            target_home / ".agent" / "antigravity-project-setup",
            args.dry_run,
        )

    # 5. Optional components
    process_gate_status = "skipped (pass --with-process-gate to enable)"
    if args.with_process_gate:
        if not env_info["node"]:
            process_gate_status = "skipped (node binary not found in PATH)"
        else:
            process_gate_status = install_process_gate(gemini_dir, args.dry_run)
            installed_files += 2

    claude_skill_status = "skipped (pass --with-claude-skills to enable)"
    if args.with_claude_skills:
        installed_files += copy_tree_clean(
            PAYLOAD_CLAUDE_SKILLS / "grilling",
            target_home / ".claude" / "skills" / "grilling",
            args.dry_run,
        )
        claude_skill_status = f"installed to {target_home / '.claude' / 'skills' / 'grilling'}"

    # 6. Patch target-home paths & permissions
    patch_installed_paths(gemini_dir, target_home, env_info, args.dry_run)

    # 7. Persist baseline manifest
    manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
    if not args.dry_run:
        baseline_root.mkdir(parents=True, exist_ok=True)
        (baseline_root / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    summary = {
        "status": "dry_run_ok" if args.dry_run else "installed_ok",
        "target_home": str(target_home),
        "installed_file_count": installed_files,
        "plugins_installed": PLUGINS,
        "skills_installed": SKILLS,
        "project_bootstrapper_installed": not args.skip_project_bootstrapper,
        "process_review_gate": process_gate_status,
        "claude_code_skills": claude_skill_status,
        "backup_dir": str(baseline_root),
        "backed_up_count": len(manifest["preexisting_items"]),
        "newly_created_count": len(manifest["created_items"]),
        "environment": {
            "claude_mode": env_info["claude_fallback_mode"],
            "pnpm_found": bool(env_info["pnpm"]),
            "uv_found": bool(env_info["uv"]),
            "node_found": bool(env_info["node"]),
            "previously_disabled_plugins": env_info["previously_disabled_plugins"],
        },
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
