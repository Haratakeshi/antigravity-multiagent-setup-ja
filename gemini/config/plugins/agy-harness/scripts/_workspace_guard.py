"""Shared safe temp/scratch/workspace path validation helpers for agy-harness PreToolUse hooks."""
import fnmatch
import os
import re
import sys

IS_DARWIN = sys.platform == "darwin"

FORBIDDEN_TMP_CHARS = frozenset("$`\n\r\x00~*?[]{}()\\|;&<> \t")
FORBIDDEN_WS_CHARS = frozenset("$`\n\r\x00~*?[]{}()\\|;&<>")

SCRATCH_ROOT_RE = re.compile(
    r"^((?:/Users|/home)/[A-Za-z0-9._-]+/\.gemini/antigravity/brain/[0-9a-fA-F-]{8,}/scratch)(?:/(.*))?$",
    re.IGNORECASE if IS_DARWIN else 0,
)

FORBIDDEN_WORKSPACE_EXACT = frozenset({
    "/", "/Users", "/Volumes", "/tmp", "/private", "/private/tmp",
    "/var", "/private/var", "/private/var/folders", "/home",
})
FORBIDDEN_WORKSPACE_PREFIXES = (
    "/System", "/Library", "/Applications", "/etc", "/private/etc",
    "/usr", "/bin", "/sbin", "/opt", "/tmp", "/private/tmp", "/dev", "/cores",
)
PRIVATE_HOME_BLOCKED_SUBDIRS = frozenset({
    "desktop", "documents", "downloads", "pictures", "movies", "music",
    "library", ".ssh", ".gnupg", ".aws", ".kube", ".docker", ".claude",
    ".config", ".trash", ".local",
})
PROTECTED_WS_COMPONENTS = frozenset({".git", ".worktrees"})
PROTECTED_WS_LEAF_EXACT = frozenset({
    ".git", ".worktrees", ".env", ".npmrc", ".netrc", ".pypirc",
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
})
SAFE_ENV_TEMPLATE_NAMES = frozenset({".env.example", ".env.sample", ".env.template"})
PROTECTED_WS_SECRET_EXTS = (".pem", ".p12", ".pfx", ".keystore", ".jks")
MAX_GLOB_SCAN_ENTRIES = 20000
MAX_ANCHOR_SCAN_ENTRIES = 50000
MAX_SUBDIR_ANCHOR_COUNT = 10000
MAX_SUBDIR_ENTRY_SCAN = 20000


def norm_cmp_path(path: str) -> str:
    """Normalize path string for platform case semantics (case-insensitive on macOS APFS/HFS+)."""
    return path.lower() if IS_DARWIN else path


def same_or_under_path(path: str, prefix: str) -> bool:
    """Return True iff path equals prefix or lies strictly beneath prefix + '/' (platform case-aware)."""
    p_cmp = norm_cmp_path(path.rstrip("/"))
    pfx_cmp = norm_cmp_path(prefix.rstrip("/"))
    return bool(pfx_cmp) and (p_cmp == pfx_cmp or p_cmp.startswith(pfx_cmp + "/"))


def strip_leading_dotslash(rel: str) -> str:
    """Strip repeated leading './' prefixes without altering '.hidden' names."""
    cur = rel
    while cur.startswith("./"):
        cur = cur[2:]
    return cur


def is_valid_env_tmpdir(env_tmpdir: str) -> bool:
    """Return True iff env_tmpdir resolves strictly within macOS/POSIX temp directories."""
    if not env_tmpdir:
        return False
    tmp_root_real = os.path.realpath("/tmp")
    try:
        et_real = os.path.realpath(env_tmpdir)
    except OSError:
        return False
    et_cmp = norm_cmp_path(et_real)
    tmp_cmp = norm_cmp_path(tmp_root_real)
    return (
        et_cmp == tmp_cmp
        or et_cmp.startswith(tmp_cmp + "/")
        or et_cmp.startswith(norm_cmp_path("/var/folders/"))
        or et_cmp.startswith(norm_cmp_path("/private/var/folders/"))
    )


def _walk_components_no_symlink_escape(
    base_dir: str,
    real_base: str,
    check_parts: list[str],
    allow_leaf_symlink: bool = False,
) -> bool:
    """Verify check_parts under base_dir contain no symlinks and resolve strictly within real_base."""
    cur = base_dir
    deepest = base_dir
    n_parts = len(check_parts)
    for idx, part in enumerate(check_parts):
        cur = cur + "/" + part
        is_last = idx == n_parts - 1
        if os.path.islink(cur):
            if not (allow_leaf_symlink and is_last):
                return False
            continue
        if os.path.exists(cur):
            deepest = cur
    if os.path.exists(deepest):
        try:
            real_d = os.path.realpath(deepest)
        except OSError:
            return False
        if not same_or_under_path(real_d, real_base):
            return False
    return True


def is_safe_tmp_target(raw_path: str | None) -> bool:
    """Allow only concrete non-symlink paths strictly inside /tmp/<name> or /private/tmp/<name>."""
    if not raw_path or any(c in FORBIDDEN_TMP_CHARS for c in raw_path):
        return False
    raw_cmp = norm_cmp_path(raw_path)
    if not (raw_cmp.startswith("/tmp/") or raw_cmp.startswith("/private/tmp/")):
        return False
    parts = [p for p in raw_path.split("/") if p != ""]
    if "." in parts or ".." in parts:
        return False
    p0 = norm_cmp_path(parts[0])
    if p0 == "tmp":
        if len(parts) < 2:
            return False
        root_prefix = "/tmp"
        sub_parts = parts[1:]
    elif len(parts) >= 3 and p0 == "private" and norm_cmp_path(parts[1]) == "tmp":
        root_prefix = "/private/tmp"
        sub_parts = parts[2:]
    else:
        return False
    return _walk_components_no_symlink_escape(root_prefix, os.path.realpath("/tmp"), sub_parts)


def is_safe_scratch_target(scratch_root: str, rel_subpath: str) -> bool:
    """Validate rel_subpath strictly inside Antigravity scratch_root with no symlinks escaping."""
    if not rel_subpath or any(c in FORBIDDEN_TMP_CHARS for c in rel_subpath):
        return False
    parts = [p for p in rel_subpath.split("/") if p != ""]
    if not parts or "." in parts or ".." in parts:
        return False
    if os.path.islink(scratch_root):
        return False
    return _walk_components_no_symlink_escape(scratch_root, os.path.realpath(scratch_root), parts)


def validate_rel_subpath_under_root(
    root_dir: str,
    subpath: str,
    allow_glob: bool = False,
    check_symlinks: bool = True,
) -> bool:
    """Validate rel subpath strictly under root_dir (non-empty, no .., no symlink escape, bounded leaf glob)."""
    if not subpath or "$" in subpath or subpath.startswith("/"):
        return False
    clean = strip_leading_dotslash(subpath)
    if not clean:
        return False
    has_glob = "*" in clean or "?" in clean
    if has_glob and not allow_glob:
        return False
    if has_glob:
        if clean.endswith("/"):
            return False
        last_slash = clean.rfind("/")
        parent_rel = clean[:last_slash] if last_slash >= 0 else ""
        leaf = clean[last_slash + 1 :] if last_slash >= 0 else clean
        if any(c in parent_rel for c in FORBIDDEN_TMP_CHARS):
            return False
        parent_parts = [p for p in parent_rel.split("/") if p != ""]
        if "." in parent_parts or ".." in parent_parts:
            return False
        if not leaf or leaf.startswith(".") or not re.fullmatch(r"[A-Za-z0-9._*?-]+", leaf):
            return False
        if not parent_rel and not re.match(r"^[A-Za-z0-9_-]{2,}", leaf):
            return False
        check_parts = parent_parts
    else:
        clean_no_trail = clean.rstrip("/")
        if not clean_no_trail or any(c in FORBIDDEN_TMP_CHARS for c in clean_no_trail):
            return False
        parts = [p for p in clean_no_trail.split("/") if p != ""]
        if not parts or "." in parts or ".." in parts:
            return False
        check_parts = parts
    if check_symlinks and root_dir:
        return _walk_components_no_symlink_escape(root_dir, os.path.realpath(root_dir), check_parts)
    return True


def _has_forbidden_workspace_prefix(norm_path: str) -> bool:
    cmp_p = norm_cmp_path(norm_path)
    if cmp_p in {norm_cmp_path(x) for x in FORBIDDEN_WORKSPACE_EXACT}:
        return True
    for pfx in FORBIDDEN_WORKSPACE_PREFIXES:
        cp = norm_cmp_path(pfx)
        if cmp_p == cp or cmp_p.startswith(cp + "/"):
            return True
    if cmp_p.startswith(norm_cmp_path("/private/var/")) and not cmp_p.startswith(norm_cmp_path("/private/var/folders/")):
        return True
    if cmp_p.startswith(norm_cmp_path("/var/")) and not cmp_p.startswith(norm_cmp_path("/var/folders/")):
        return True
    return False


def normalize_safe_workspace_roots(workspace_paths: object) -> list[tuple[str, str]]:
    """Validate and return deduplicated (raw_norm, real_norm) pairs for concrete project workspace roots."""
    if not isinstance(workspace_paths, list) or not workspace_paths:
        return []
    home_raw = os.path.normpath(os.path.expanduser("~")).rstrip("/")
    home_real = os.path.realpath(os.path.expanduser("~")).rstrip("/")
    home_raw_cmp = norm_cmp_path(home_raw)
    home_real_cmp = norm_cmp_path(home_real)
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for ws in workspace_paths:
        if not isinstance(ws, str) or not ws or any(c in "\x00\n\r" for c in ws):
            continue
        if not ws.startswith("/"):
            continue
        if ".." in [p for p in ws.split("/") if p]:
            continue
        raw_trimmed = ws.rstrip("/")
        if not raw_trimmed or os.path.islink(raw_trimmed) or not os.path.isdir(raw_trimmed):
            continue
        raw_norm = os.path.normpath(raw_trimmed).rstrip("/")
        try:
            real_norm = os.path.realpath(raw_norm).rstrip("/")
        except OSError:
            continue
        if not raw_norm or not real_norm:
            continue
        if _has_forbidden_workspace_prefix(raw_norm) or _has_forbidden_workspace_prefix(real_norm):
            continue
        real_parts = [p for p in real_norm.split("/") if p]
        raw_parts = [p for p in raw_norm.split("/") if p]
        if len(real_parts) < 2 or len(raw_parts) < 2:
            continue
        if real_parts[0].lower() in ("users", "home") and len(real_parts) <= 2:
            continue
        if raw_parts[0].lower() in ("users", "home") and len(raw_parts) <= 2:
            continue
        if real_parts[0].lower() == "volumes" and len(real_parts) <= 2:
            continue
        if raw_parts[0].lower() == "volumes" and len(raw_parts) <= 2:
            continue
        if [p.lower() for p in real_parts[:3]] == ["private", "var", "folders"] and len(real_parts) < 5:
            continue
        raw_cmp = norm_cmp_path(raw_norm)
        real_cmp = norm_cmp_path(real_norm)
        if raw_cmp == home_raw_cmp or real_cmp == home_real_cmp:
            continue
        for hm_path, hm_cmp in ((home_real, home_real_cmp), (home_raw, home_raw_cmp)):
            for cand_path, cand_cmp in ((real_norm, real_cmp), (raw_norm, raw_cmp)):
                if cand_cmp.startswith(hm_cmp + "/"):
                    home_rel = cand_path[len(hm_path) + 1 :]
                    top = home_rel.split("/", 1)[0].lower()
                    if top in PRIVATE_HOME_BLOCKED_SUBDIRS:
                        cand_path = ""
                        break
                    if top == ".gemini":
                        home_rel_low = home_rel.lower()
                        if not home_rel_low.startswith(".gemini/antigravity/scratch/") or len(home_rel.split("/")) < 4:
                            cand_path = ""
                            break
            else:
                continue
            raw_norm = ""
            break
        if not raw_norm:
            continue
        pair = (raw_norm, real_norm)
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out


def is_protected_ws_leaf(name: str) -> bool:
    """Return True if a leaf name is git metadata, .env secret, npm/netrc auth, or key/keystore."""
    low = name.lower()
    if low in PROTECTED_WS_LEAF_EXACT:
        return True
    if low.startswith(".env.") and low not in SAFE_ENV_TEMPLATE_NAMES:
        return True
    if low.endswith(PROTECTED_WS_SECRET_EXTS):
        return True
    return False


def dir_holds_protected_anchor(dir_path: str) -> bool:
    """Return True if dir_path or any descendant dir holds .git/.worktrees/.env.*/secret files (bounded fail-closed)."""
    if not dir_path or os.path.islink(dir_path) or not os.path.isdir(dir_path):
        return False
    for anchor in (".git", ".worktrees", ".env", ".env.local", ".env.production", ".npmrc"):
        if os.path.exists(os.path.join(dir_path, anchor)):
            return True
    stack = [dir_path]
    seen_dirs: set[tuple[int, int]] = set()
    scanned_entries = 0
    subdir_count = 0
    try:
        root_st = os.stat(dir_path, follow_symlinks=False)
        seen_dirs.add((root_st.st_dev, root_st.st_ino))
    except OSError:
        return True
    while stack:
        cur_dir = stack.pop()
        try:
            with os.scandir(cur_dir) as it:
                for entry in it:
                    scanned_entries += 1
                    if scanned_entries > MAX_ANCHOR_SCAN_ENTRIES:
                        return True
                    if is_protected_ws_leaf(entry.name):
                        return True
                    if not entry.is_symlink() and entry.is_dir(follow_symlinks=False):
                        subdir_count += 1
                        if subdir_count > MAX_SUBDIR_ANCHOR_COUNT:
                            return True
                        try:
                            st = entry.stat(follow_symlinks=False)
                        except OSError:
                            return True
                        inode_key = (st.st_dev, st.st_ino)
                        if inode_key not in seen_dirs:
                            seen_dirs.add(inode_key)
                            stack.append(entry.path)
        except OSError:
            return True
    return False


def has_protected_ws_component(parts: list[str], allow_worktree_branch_dir: bool = False) -> bool:
    """Return True if path components touch .git or disallowed .worktrees levels."""
    low_parts = [p.lower() for p in parts]
    if ".git" in low_parts:
        return True
    if ".worktrees" in low_parts:
        if low_parts[0] != ".worktrees" or ".worktrees" in low_parts[1:]:
            return True
        min_len = 2 if allow_worktree_branch_dir else 3
        if len(low_parts) < min_len:
            return True
    return False


def validate_workspace_rel_subpath(
    ws_real: str,
    subpath: str,
    allow_glob: bool = False,
    check_protected_anchors: bool = True,
) -> bool:
    """Validate a workspace-relative deletion (or cd) target strictly inside ws_real."""
    if not ws_real or not subpath or "$" in subpath or subpath.startswith("/"):
        return False
    clean = strip_leading_dotslash(subpath)
    if not clean:
        return False

    has_glob = "*" in clean or "?" in clean
    if has_glob and not allow_glob:
        return False

    if has_glob:
        if clean.endswith("/"):
            return False
        last_slash = clean.rfind("/")
        parent_rel = clean[:last_slash] if last_slash >= 0 else ""
        leaf = clean[last_slash + 1 :] if last_slash >= 0 else clean
        if any(c in FORBIDDEN_WS_CHARS for c in parent_rel):
            return False
        parent_parts = [p for p in parent_rel.split("/") if p != ""]
        if "." in parent_parts or ".." in parent_parts:
            return False
        if has_protected_ws_component(parent_parts, allow_worktree_branch_dir=True):
            return False
        if not leaf or leaf.startswith(".") or not re.fullmatch(r"[A-Za-z0-9._*?-]+", leaf):
            return False
        if (
            len(parent_parts) == 2
            and parent_parts[0].lower() == ".worktrees"
            and any(
                fnmatch.fnmatchcase(prot, leaf.lower())
                for prot in (PROTECTED_WS_LEAF_EXACT | {".env.local", ".env.production"})
            )
        ):
            return False
        if not parent_rel and not re.match(r"^[A-Za-z0-9_-]{2,}", leaf):
            return False
        check_parts = parent_parts
    else:
        clean_no_trail = clean.rstrip("/")
        if not clean_no_trail or any(c in FORBIDDEN_WS_CHARS for c in clean_no_trail):
            return False
        parts = [p for p in clean_no_trail.split("/") if p != ""]
        if not parts or "." in parts or ".." in parts:
            return False
        if has_protected_ws_component(parts, allow_worktree_branch_dir=not check_protected_anchors):
            return False
        if check_protected_anchors and is_protected_ws_leaf(parts[-1]):
            return False
        check_parts = parts

    if not _walk_components_no_symlink_escape(ws_real, ws_real, check_parts):
        return False

    if not check_protected_anchors:
        return True

    if has_glob:
        parent_abs = os.path.join(ws_real, *check_parts) if check_parts else ws_real
        if os.path.isdir(parent_abs):
            try:
                leaf_low = leaf.lower()
                with os.scandir(parent_abs) as it:
                    for cnt, entry in enumerate(it, 1):
                        if cnt > MAX_GLOB_SCAN_ENTRIES:
                            return False
                        if fnmatch.fnmatchcase(entry.name.lower(), leaf_low):
                            if entry.is_symlink():
                                return False
                            if is_protected_ws_leaf(entry.name):
                                return False
                            if entry.is_dir(follow_symlinks=False) and dir_holds_protected_anchor(entry.path):
                                return False
            except OSError:
                return False
    else:
        full_abs = os.path.join(ws_real, *check_parts)
        if dir_holds_protected_anchor(full_abs):
            return False

    return True


def match_workspace_path(abs_path: str, ws_roots: list[tuple[str, str]]) -> tuple[str, str] | None:
    """If abs_path lies strictly beneath a normalized workspace root, return (ws_real, rel_subpath)."""
    if not abs_path or not ws_roots or not abs_path.startswith("/"):
        return None
    abs_cmp = norm_cmp_path(abs_path)
    for raw_norm, real_norm in ws_roots:
        for base in (raw_norm, real_norm):
            base_cmp = norm_cmp_path(base)
            if abs_cmp.startswith(base_cmp + "/"):
                rel = abs_path[len(base) + 1 :].lstrip("/")
                if rel:
                    return real_norm, rel
                return None
    return None


def is_safe_workspace_target(
    abs_path: str,
    ws_roots: list[tuple[str, str]],
    allow_glob: bool = False,
    check_protected_anchors: bool = True,
) -> bool:
    """Return True iff abs_path resolves to a safe deletable target inside an active workspace root."""
    matched = match_workspace_path(abs_path, ws_roots)
    if matched is None:
        return False
    ws_real, rel = matched
    return validate_workspace_rel_subpath(
        ws_real,
        rel,
        allow_glob=allow_glob,
        check_protected_anchors=check_protected_anchors,
    )


def validate_initial_cwd_in_workspace(
    initial_cwd: str,
    ws_roots: list[tuple[str, str]],
) -> tuple[str, str] | None:
    """Return (ws_real, cwd_real) iff initial_cwd is a concrete non-symlink dir inside an active ws root."""
    if not initial_cwd or not ws_roots or not initial_cwd.startswith("/"):
        return None
    if any(c in "\x00\n\r" for c in initial_cwd):
        return None
    raw_cwd_norm = os.path.normpath(initial_cwd).rstrip("/")
    if not raw_cwd_norm or not os.path.isdir(raw_cwd_norm):
        return None
    try:
        cwd_real = os.path.realpath(raw_cwd_norm).rstrip("/")
    except OSError:
        return None

    cwd_real_cmp = norm_cmp_path(cwd_real)
    raw_cwd_cmp = norm_cmp_path(raw_cwd_norm)
    for raw_norm, real_norm in ws_roots:
        real_cmp = norm_cmp_path(real_norm)
        raw_cmp = norm_cmp_path(raw_norm)
        if not same_or_under_path(cwd_real, real_norm):
            continue
        if cwd_real_cmp != real_cmp:
            real_rel = cwd_real[len(real_norm) + 1 :]
            if has_protected_ws_component([p for p in real_rel.split("/") if p], allow_worktree_branch_dir=True):
                continue
        for base, base_cmp in ((raw_norm, raw_cmp), (real_norm, real_cmp)):
            if raw_cwd_cmp == base_cmp:
                return real_norm, cwd_real
            if raw_cwd_cmp.startswith(base_cmp + "/"):
                rel_raw = raw_cwd_norm[len(base) + 1 :]
                parts = [p for p in rel_raw.split("/") if p]
                if not _walk_components_no_symlink_escape(base, real_norm, parts):
                    return None
                return real_norm, cwd_real
        if not os.path.islink(raw_cwd_norm):
            return real_norm, cwd_real
    return None


def ws_relpath(path: str, ws_real: str) -> str | None:
    """Return path relative to ws_real ('' if equal) respecting platform case semantics, or None if outside."""
    if not same_or_under_path(path, ws_real):
        return None
    p_t = path.rstrip("/")
    w_t = ws_real.rstrip("/")
    if not p_t or not w_t:
        return None
    if len(p_t) == len(w_t):
        return ""
    return p_t[len(w_t) + 1 :].lstrip("/")
