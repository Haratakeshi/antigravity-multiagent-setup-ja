#!/usr/bin/env python3
# agy-harness: run_command ガード (PreToolUse)
# 禁止コマンド・保護対象削除・破壊的操作を非ブロッキング deny（代替案または [DEFER_HUMAN_GATE] 付き）として返し、
# 停止せずゴールまで進めて最後に Human Gate へ集約できるようにする。
# 安全な /tmp・/private/tmp・$TMPDIR・mktemp・brain/scratch・有効なアクティブ workspacePaths 内を対象とする rm のみ allow。
import ast
from dataclasses import dataclass
import fnmatch
import json
import os
import re
import shlex
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
from _workspace_guard import (
    FORBIDDEN_TMP_CHARS,
    FORBIDDEN_WS_CHARS,
    SCRATCH_ROOT_RE,
    is_safe_scratch_target,
    is_safe_tmp_target,
    is_safe_workspace_target,
    is_valid_env_tmpdir,
    match_workspace_path,
    norm_cmp_path,
    normalize_safe_workspace_roots,
    same_or_under_path,
    strip_leading_dotslash,
    validate_initial_cwd_in_workspace,
    validate_rel_subpath_under_root,
    validate_workspace_rel_subpath,
    ws_relpath,
)

_normalize_safe_workspace_roots = normalize_safe_workspace_roots
_is_safe_workspace_target = is_safe_workspace_target
_validate_initial_cwd_in_workspace = validate_initial_cwd_in_workspace
RM_WORD_RE = re.compile(
    r"""(^|[\s;&|('"`{/\\=!$])\\?['"]?r['"\\]*m['"]?(?=\s|$|[;&|)'"`},])""",
    re.IGNORECASE,
)
SHELL_PREFIX_KEYWORDS = frozenset({
    "!", "{", "}", "if", "then", "elif", "else", "fi",
    "while", "until", "do", "done", "case", "esac",
    "coproc", "noglob", "nocorrect",
})
REDIR_ONLY_RE = re.compile(r"^\d*(<<<|[<>]{1,2}|>&|<&|&>>?|<>|>\|)$")
REDIR_INLINE_RE = re.compile(r"^\d*(<<<|[<>]{1,2}|>&|<&|&>>?|<>|>\|).+")
PREFIX_WRAPPER_VAL_OPTS: dict[str, frozenset[str]] = {
    "command": frozenset(),
    "builtin": frozenset(),
    "nohup": frozenset(),
    "time": frozenset({"-o", "--output", "-f", "--format"}),
    "exec": frozenset({"-a"}),
    "doas": frozenset({"-u", "-C"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "stdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
    "arch": frozenset({"-arch"}),
    "caffeinate": frozenset({"-t", "-w"}),
    "sudo": frozenset({
        "-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-T", "-U", "-R",
        "--user", "--group", "--close-from", "--chdir", "--host", "--prompt",
        "--role", "--type", "--command-timeout", "--other-user", "--chroot",
    }),
    "timeout": frozenset({"-k", "--kill-after", "-s", "--signal"}),
    "gtimeout": frozenset({"-k", "--kill-after", "-s", "--signal"}),
}
ENV_VAL_OPTS = frozenset({
    "-u", "-C", "-P", "--unset", "--chdir",
    "--default-signal", "--ignore-signal", "--block-signal",
})
SHELL_PROGS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish"})
XARGS_VALUE_OPTS = frozenset({
    "-a", "-d", "-E", "-I", "-J", "-L", "-n", "-P", "-R", "-S", "-s",
    "--arg-file", "--delimiter",
    "--max-args", "--max-procs", "--max-chars", "--process-slot-var",
})
XARGS_SHORT_VAL_CHARS = frozenset("adEIJLnPRSs")
NODE_VAL_OPTS = frozenset({
    "-r", "--require", "--loader", "--import", "--env-file", "--env-file-if-exists",
    "-C", "--conditions", "--input-type", "--title", "--heapsnapshot-signal",
    "--diagnostic-dir", "--redirect-warnings", "--openssl-config", "--icu-data-dir",
    "--max-http-header-size", "--inspect-wait",
})
RUBY_VAL_OPTS = frozenset({
    "-r", "-I", "-C", "-E", "-F", "-W",
    "--external-encoding", "--internal-encoding", "--encoding",
})
PARALLEL_VALUE_OPTS = frozenset({
    "-j", "--jobs", "-I", "--replace-str", "-C", "--colsep", "-S", "--sshlogin",
    "--results", "--res", "--joblog", "-N", "-n", "-L", "-l",
    "--delay", "--timeout", "--arg-file", "-a", "--max-args", "--tagstring", "-E",
    "--wd", "--workdir", "--env", "--tmpdir", "--header", "--basefile",
    "--transferfile", "--return", "--rpl", "--block", "--block-size",
    "--recstart", "--recend",
})
ECHO_PRINTF_ESC_RE = re.compile(
    r"\\(?:x([0-9a-fA-F]{1,2})|u([0-9a-fA-F]{1,4})|U([0-9a-fA-F]{1,8})|"
    r"0([0-7]{1,3})|([0-7]{1,3})|([abefnrtvE\\'\"?]))"
)
ANSI_C_ESC_RE = re.compile(
    r"\\(?:x([0-9a-fA-F]{1,2})|u([0-9a-fA-F]{1,4})|U([0-9a-fA-F]{1,8})|"
    r"([0-7]{1,3})|([abefnrtvE\\'\"?]))"
)
ANSI_C_SIMPLE_MAP = {
    "a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b",
    "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
    "\\": "\\", "'": "'", '"': '"', "?": "?",
}
GIT_GLOBAL_VAL_OPTS = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env",
})
GIT_READONLY_GLOBAL_FLAGS = frozenset({
    "--no-pager", "-P", "--paginate", "-p",
    "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs", "--icase-pathspecs",
    "--no-optional-locks",
})
GIT_READONLY_GLOBAL_VAL_OPTS = frozenset({"-C", "--git-dir", "--work-tree", "--namespace"})
GIT_READONLY_SUBCMDS = frozenset({"grep", "ls-files"})
FIND_UNSAFE_FLAGS = frozenset({
    "-delete", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls",
})
FIND_READONLY_EXEC_CMDS = frozenset({
    "grep", "egrep", "fgrep", "ag", "ack", "wc", "head", "cat", "file", "stat", "ls",
})
READONLY_PREFIX_WRAPPERS = frozenset({"timeout", "gtimeout", "nice", "command", "nohup", "env"})
READONLY_SEARCH_CMDS = frozenset({"grep", "egrep", "fgrep", "ag", "ack"})
ALLOWED_RM_LONG_OPTS = frozenset({"--recursive", "--force", "--dir", "--verbose"})


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.exit(0)


def _split_shell_ops(command: str, delimiters: str = ";|&\n()") -> list[str] | None:
    """Split shell command on unquoted delimiters (preserving >&, <&, &> redirections and skipping # comments). Returns None on unbalanced quotes."""
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    escaped = False
    in_comment = False
    n = len(command)
    for idx, ch in enumerate(command):
        if in_comment:
            if ch == "\n":
                in_comment = False
                if "\n" in delimiters:
                    seg = "".join(buf).strip()
                    if seg:
                        segments.append(seg)
                    buf = []
            continue
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\" and quote != "'":
            buf.append(ch)
            escaped = True
            continue
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            continue
        if ch == "#" and (idx == 0 or command[idx - 1] in " \t\n;|&()"):
            in_comment = True
            continue
        if ch == "&" and "&" in delimiters:
            prev_ch = command[idx - 1] if idx > 0 else ""
            next_ch = command[idx + 1] if idx + 1 < n else ""
            if prev_ch in (">", "<") or next_ch == ">":
                buf.append(ch)
                continue
        if ch == "|" and "|" in delimiters:
            prev_ch = command[idx - 1] if idx > 0 else ""
            if prev_ch == ">":
                buf.append(ch)
                continue
        if ch in delimiters:
            seg = "".join(buf).strip()
            if seg:
                segments.append(seg)
            buf = []
            continue
        buf.append(ch)
    if quote is not None or escaped:
        return None
    tail = "".join(buf).strip()
    if tail:
        segments.append(tail)
    return segments


def _is_safe_tmp_target(raw_path: str) -> bool:
    """Allow only concrete non-symlink paths strictly inside /tmp/<name> or /private/tmp/<name>."""
    return is_safe_tmp_target(raw_path)


def _expand_simple_braces(word: str, max_variants: int = 64) -> list[str]:
    """Expand comma brace groups up to max_variants so brace-obfuscated rm words are caught."""
    variants = [word]
    for _ in range(4):
        next_variants: list[str] = []
        expanded_any = False
        for v in variants:
            m = re.search(r"\{([^{}]*,[^{}]*)\}", v)
            if not m:
                next_variants.append(v)
                continue
            expanded_any = True
            prefix = v[: m.start()]
            suffix = v[m.end() :]
            for opt in m.group(1).split(","):
                next_variants.append(prefix + opt + suffix)
                if len(next_variants) >= max_variants:
                    return next_variants
        variants = next_variants
        if not expanded_any:
            break
    return variants


def _is_rm_cmd_word(word: str, _depth: int = 0) -> bool:
    if _depth > 4:
        return False
    cleaned = word.lstrip("=$")
    cleaned_low = cleaned.lower()
    if "{" in cleaned_low:
        if re.search(r"\{[^}]*(?:^|[,/])rm(?=[,}/])", cleaned_low):
            return True
        if "," in cleaned_low:
            expanded = _expand_simple_braces(cleaned_low)
            if expanded != [cleaned_low]:
                for cand in expanded:
                    if cand != cleaned_low and _is_rm_cmd_word(cand, _depth + 1):
                        return True
    base = os.path.basename(cleaned_low)
    if base == "rm":
        return True
    if base and any(c in base for c in "*?[") and not (set(base) <= set("*?")) and fnmatch.fnmatchcase("rm", base):
        return True
    return False


def _strip_leading_shell_tokens_with_env(tokens: list[str]) -> tuple[list[str], list[str]]:
    """Drop leading VAR=val assignments, shell keywords, and redirections, returning (remaining, env_values)."""
    out = list(tokens)
    env_vals: list[str] = []
    while out:
        t0 = out[0]
        t0_low = t0.lower()
        m_env = re.match(r"^[A-Za-z_][A-Za-z0-9_]*=(.*)$", t0, re.S)
        if m_env:
            env_vals.append(m_env.group(1))
            out.pop(0)
            continue
        if t0_low in SHELL_PREFIX_KEYWORDS:
            out.pop(0)
            continue
        if REDIR_ONLY_RE.match(t0):
            out.pop(0)
            if out:
                out.pop(0)
            continue
        if REDIR_INLINE_RE.match(t0):
            out.pop(0)
            continue
        break
    return out, env_vals


def _strip_leading_shell_tokens(tokens: list[str]) -> list[str]:
    """Drop leading VAR=val assignments, shell keywords, and redirection tokens."""
    stripped, _ = _strip_leading_shell_tokens_with_env(tokens)
    return stripped


def _find_exec_end(args: list[str], sub_start: int) -> int:
    """Return exclusive end index for find -exec/-execdir/-ok/-okdir (only treat '+' as terminator right after '{}')."""
    sub_end = sub_start
    n_args = len(args)
    while sub_end < n_args:
        tok = args[sub_end]
        if tok == ";":
            return sub_end
        if tok == "+" and sub_end > sub_start and args[sub_end - 1] == "{}":
            return sub_end
        sub_end += 1
    return sub_end


def _skip_xargs_opts(args: list[str]) -> list[str]:
    """Advance past xargs option flags (handling short flag clusters carefully) and return remaining tokens."""
    idx = 0
    n_args = len(args)
    while idx < n_args:
        tok = args[idx]
        if tok == "--":
            idx += 1
            break
        if not tok.startswith("-") or tok == "-":
            break
        if tok in XARGS_VALUE_OPTS:
            idx += 2
            continue
        if tok.startswith("--"):
            idx += 1
            continue
        consumed_next = False
        for c_i, ch in enumerate(tok[1:]):
            if ch in XARGS_SHORT_VAL_CHARS:
                idx += 1 if c_i + 1 < len(tok) - 1 else 2
                consumed_next = True
                break
        if not consumed_next:
            idx += 1
    return args[idx:]


def _extract_shell_exec_inputs(args: list[str]) -> list[str]:
    """Extract command strings passed via -c (including clusters like -cl/-ce and `-c -- cmd`) or <<<."""
    out: list[str] = []
    idx = 0
    n_args = len(args)
    saw_c = False
    after_opts = False
    while idx < n_args:
        tok = args[idx]
        if after_opts:
            if saw_c:
                out.append(tok)
                saw_c = False
            break
        if tok == "--":
            after_opts = True
            idx += 1
            continue
        if tok in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            idx += 2
            continue
        m_hs = re.match(r"^\d*<<<(.*)$", tok)
        if m_hs:
            if m_hs.group(1):
                out.append(m_hs.group(1))
                idx += 1
            elif idx + 1 < n_args:
                out.append(args[idx + 1])
                idx += 2
            else:
                idx += 1
            continue
        if REDIR_ONLY_RE.match(tok):
            idx += 2
            continue
        if REDIR_INLINE_RE.match(tok):
            idx += 1
            continue
        if tok.startswith("-") and not tok.startswith("--") and "c" in tok[1:]:
            saw_c = True
            idx += 1
            continue
        if tok.startswith("-") or (tok.startswith("+") and len(tok) > 1):
            idx += 1
            continue
        after_opts = True
        if saw_c:
            out.append(tok)
            saw_c = False
        break
    for j in range(idx, n_args):
        m_hs = re.match(r"^\d*<<<(.*)$", args[j])
        if m_hs:
            if m_hs.group(1):
                out.append(m_hs.group(1))
            elif j + 1 < n_args:
                out.append(args[j + 1])
    return out


def _collapse_script_strings(code: str) -> str:
    """Decode array-joins, JS fromCharCode, AWK/Perl printf %c, and mixed-quote splits in inline scripts."""
    def _collapse_join(m: re.Match[str]) -> str:
        parts = re.findall(r"""(['"])([^'"\\]*)\1""", m.group(1))
        return '"' + "".join(p[1] for p in parts) + '"' if parts else m.group(0)

    def _collapse_fromchar(m: re.Match[str]) -> str:
        nums = [int(x) for x in re.findall(r"\b\d+\b", m.group(1))]
        if not nums or any(n > 0x10FFFF for n in nums):
            return m.group(0)
        return '"' + "".join(chr(n) for n in nums) + '"'

    def _collapse_fmt_chars(m: re.Match[str]) -> str:
        fmt = m.group(2)
        if "%c" not in fmt:
            return m.group(0)
        nums = [int(x) for x in re.findall(r"\b\d+\b", m.group(3))]
        ni = 0

        def _sub_c(mc: re.Match[str]) -> str:
            nonlocal ni
            if mc.group(0) == "%%":
                return "%"
            if ni < len(nums) and 0 <= nums[ni] <= 0x10FFFF:
                ch = chr(nums[ni])
                ni += 1
                return ch
            return mc.group(0)

        rendered = re.sub(r"%%|%c", _sub_c, fmt)
        return '"' + rendered + '"'

    s = re.sub(r"""\[([^\[\]]+)\]\.join\(\s*(['"])\2\s*\)""", _collapse_join, code)
    s = re.sub(r"""String\.fromCharCode\s*\(\s*([0-9,\s]+)\s*\)""", _collapse_fromchar, s)
    s = re.sub(r"""\bs?printf\s*\(?\s*(['"])((?:\\.|[^'"])*?)\1\s*,\s*([0-9,\s]+)\s*\)?""", _collapse_fmt_chars, s)
    return re.sub(r"""['"`](?:[ \t]*(?:\+|\.|&)[ \t]*|[ \t]*)['"`]""", "", s)


def _contains_obfuscated_rm_in_script(code: str) -> bool:
    """Detect rm words or obfuscated string splits/char-codes inside inline scripts."""
    if RM_WORD_RE.search(code):
        return True
    collapsed = _collapse_script_strings(code)
    return bool(collapsed != code and RM_WORD_RE.search(collapsed))


def _osascript_has_unsafe_shell(code: str) -> bool:
    """Return True if AppleScript code contains obfuscated rm or dynamic `do shell script` expression."""
    if _contains_obfuscated_rm_in_script(code):
        return True
    collapsed = _collapse_script_strings(code)
    return bool(re.search(r"\bdo\s+shell\s+script\s+(?![\"'][^\"']*[\"'](?:\s|$|\)))", collapsed, re.IGNORECASE))


def _extract_herestring_texts(args: list[str]) -> list[str]:
    """Return all <<< here-string payloads (including fd-prefixed 0<<<) from args."""
    out: list[str] = []
    n_args = len(args)
    for j, tok in enumerate(args):
        m_hs = re.match(r"^\d*<<<(.*)$", tok)
        if m_hs:
            if m_hs.group(1):
                out.append(m_hs.group(1))
            elif j + 1 < n_args:
                out.append(args[j + 1])
    return out


def _has_herestring_rm(args: list[str], obfuscated_script: bool = False) -> bool:
    """Return True if any <<< here-string payload in args matches RM_WORD_RE or inline script obfuscation."""
    for hs in _extract_herestring_texts(args):
        if RM_WORD_RE.search(hs):
            return True
        if obfuscated_script and _contains_obfuscated_rm_in_script(hs):
            return True
    return False


def _interp_reads_code_from_stdin(base: str, args: list[str]) -> bool:
    """Return True iff python*/node/perl/ruby/osascript runs stdin/proc-subst as code (no -c/-e/-m or file)."""
    if not base:
        return False
    b_low = base.lower()
    _stdin_paths = ("-", "/dev/stdin", "/dev/fd/0")
    if re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", b_low):
        if _scan_python_cli_c_flag(args, lambda t: t) is not None:
            return False
        pi = 0
        while pi < len(args):
            a = args[pi]
            if REDIR_ONLY_RE.match(a):
                pi += 2
                continue
            if REDIR_INLINE_RE.match(a):
                pi += 1
                continue
            if a == "--":
                pi += 1
                if pi < len(args) and args[pi] not in _stdin_paths and not args[pi].startswith("<("):
                    return False
                return True
            if a in ("-m", "--help", "-h", "--version", "-V"):
                return False
            if a in ("-W", "-X", "-Q", "--check-hash-based-pycs"):
                pi += 2
                continue
            if a.startswith("-"):
                pi += 1
                continue
            return a in _stdin_paths or a.startswith("<(")
        return True
    if b_low == "node":
        ni = 0
        while ni < len(args):
            a = args[ni]
            if REDIR_ONLY_RE.match(a):
                ni += 2
                continue
            if REDIR_INLINE_RE.match(a):
                ni += 1
                continue
            if a == "--":
                ni += 1
                return ni >= len(args) or args[ni] in _stdin_paths or args[ni].startswith("<(")
            if a in ("-e", "--eval", "-p", "--print", "-h", "--help", "-v", "--version"):
                return False
            if a.startswith(("--eval=", "--print=")):
                return False
            if a.startswith("-") and not a.startswith("--") and any(c in a[1:] for c in ("e", "p")):
                return False
            if a in NODE_VAL_OPTS:
                ni += 2
                continue
            if a.startswith("-"):
                ni += 1
                continue
            return a in _stdin_paths or a.startswith("<(")
        return True
    if b_low in ("perl", "ruby"):
        val_opts = (RUBY_VAL_OPTS | frozenset({"-C", "-F", "-x"})) if b_low == "ruby" else frozenset({"-I", "-F", "-M", "-m", "-x"})
        attached_pfxs = ("-I", "-r", "-C", "-E", "-F", "-W", "-x", "-0", "-K") if b_low == "ruby" else ("-I", "-M", "-m", "-C", "-D", "-d", "-F", "-i", "-x", "-0")
        ri = 0
        while ri < len(args):
            a = args[ri]
            if REDIR_ONLY_RE.match(a):
                ri += 2
                continue
            if REDIR_INLINE_RE.match(a):
                ri += 1
                continue
            if a == "--":
                ri += 1
                return ri >= len(args) or args[ri] in _stdin_paths or args[ri].startswith("<(")
            if a in val_opts:
                ri += 2
                continue
            if len(a) > 2 and a.startswith(attached_pfxs):
                ri += 1
                continue
            if a in ("-h", "--help", "-v", "--version"):
                return False
            if a in ("-e",) or (b_low == "perl" and a == "-E") or a.startswith("-e") or (b_low == "perl" and a.startswith("-E")):
                return False
            if a.startswith("-") and not a.startswith("--"):
                for pos, ch in enumerate(a[1:], 1):
                    if ("-" + ch) in attached_pfxs:
                        break
                    if ch == "e" or (b_low == "perl" and ch == "E"):
                        return False
                ri += 1
                continue
            if a.startswith("--"):
                ri += 1
                continue
            return a in _stdin_paths or a.startswith("<(")
        return True
    if b_low == "osascript":
        oi = 0
        while oi < len(args):
            a = args[oi]
            if REDIR_ONLY_RE.match(a):
                oi += 2
                continue
            if REDIR_INLINE_RE.match(a):
                oi += 1
                continue
            if a == "--":
                oi += 1
                return oi >= len(args) or args[oi] in _stdin_paths or args[oi].startswith("<(")
            if a in ("-l", "-s"):
                oi += 2
                continue
            if a == "-e" or (a.startswith("-e") and len(a) > 2):
                return False
            if a.startswith("-"):
                oi += 1
                continue
            return a in _stdin_paths or a.startswith("<(")
        return True
    return False


def _decode_ansi_c_quotes(command: str, shlex_quote_decoded: bool) -> str:
    """Decode $'...' ANSI-C escapes so hex/octal-obfuscated commands can be inspected cleanly."""
    def _decode_esc(m: re.Match[str]) -> str:
        if m.group(1) is not None:
            return chr(int(m.group(1), 16))
        if m.group(2) is not None:
            return chr(int(m.group(2), 16))
        if m.group(3) is not None:
            cp = int(m.group(3), 16)
            return chr(cp) if cp <= 0x10FFFF else ""
        if m.group(4) is not None:
            return chr(int(m.group(4), 8) & 0xFF)
        return ANSI_C_SIMPLE_MAP.get(m.group(5), m.group(5))

    def _repl(m: re.Match[str]) -> str:
        inner = ANSI_C_ESC_RE.sub(_decode_esc, m.group(1))
        return shlex.quote(inner) if shlex_quote_decoded else inner

    return re.sub(r"\$'((?:\\.|[^'\\])*)'", _repl, command)


def _scan_python_cli_c_flag(tokens: list, get_val) -> tuple[int, int] | None:
    """Return (code_idx, c_pos_in_tok) for python* `-c` / `-uc` flags while skipping `-W*`, `-X*`, `-Q*` args."""
    idx = 0
    n = len(tokens)
    while idx < n:
        tok = get_val(tokens[idx])
        if tok in ("--", "-m", "-"):
            break
        if tok == "--check-hash-based-pycs":
            idx += 2
            continue
        if tok.startswith("--"):
            idx += 1
            continue
        if tok in ("-W", "-X", "-Q"):
            idx += 2
            continue
        if not tok.startswith("-"):
            break
        pos = 1
        consumed_next = False
        while pos < len(tok):
            ch = tok[pos]
            if ch in ("W", "X", "Q"):
                idx += 1 if pos + 1 < len(tok) else 2
                consumed_next = True
                break
            if ch == "m":
                return None
            if ch == "c":
                if pos + 1 < len(tok):
                    return idx, pos
                if idx + 1 < n:
                    return idx + 1, -1
                return None
            pos += 1
        if not consumed_next:
            idx += 1
    return None


def _unwrap_pkg_manager_invocation(
    tokens: list,
    idx: int,
    get_val,
    is_flag_opt,
) -> tuple[int, bool]:
    """Advance idx past uv/uvx/pnpm/pnpx/npx/yarn/bun/bunx wrapper into invoked executable tokens if applicable."""
    n = len(tokens)
    base = os.path.basename(get_val(tokens[idx]).lstrip("=$")).lower()
    if base in ("uvx", "pnpx", "npx", "bunx"):
        j = idx + 1
        while j < n:
            tv = get_val(tokens[j])
            if tv == "--":
                j += 1
                break
            if tv in UV_WRAPPER_VAL_OPTS | PNPM_WRAPPER_VAL_OPTS:
                j += 2
                continue
            if is_flag_opt(tokens[j], tv):
                j += 1
                continue
            return j, True
        return (j, True) if j < n else (idx, False)
    if base == "uv":
        j = idx + 1
        while j < n:
            tv = get_val(tokens[j])
            if tv in UV_WRAPPER_VAL_OPTS:
                j += 2
                continue
            if is_flag_opt(tokens[j], tv):
                j += 1
                continue
            break
        if j < n and get_val(tokens[j]) == "tool" and j + 1 < n and get_val(tokens[j + 1]) == "run":
            j += 1
        if j < n and get_val(tokens[j]) == "run":
            j += 1
            while j < n:
                tv = get_val(tokens[j])
                if tv == "--":
                    j += 1
                    break
                if tv in UV_WRAPPER_VAL_OPTS:
                    j += 2
                    continue
                if tv == "-m":
                    return idx, False
                if is_flag_opt(tokens[j], tv):
                    j += 1
                    continue
                break
            if j < n:
                return j, True
        return idx, False
    if base in ("pnpm", "yarn", "bun", "npm"):
        val_opts = (NPM_VAL_OPTS | PNPM_WRAPPER_VAL_OPTS) if base == "npm" else PNPM_WRAPPER_VAL_OPTS
        if base == "npm":
            allowed_sub = {"exec", "x"}
        elif base in ("pnpm", "yarn"):
            allowed_sub = {"exec", "dlx", "x"}
        else:
            allowed_sub = {"exec", "x", "run"}
        j = idx + 1
        while j < n:
            tv = get_val(tokens[j])
            if tv in val_opts:
                j += 2
                continue
            if is_flag_opt(tokens[j], tv):
                j += 1
                continue
            break
        if j < n and get_val(tokens[j]) in allowed_sub:
            j += 1
            while j < n:
                tv = get_val(tokens[j])
                if tv == "--":
                    j += 1
                    break
                if tv in val_opts or (base == "npm" and tv in ("-c", "--call")):
                    j += 2
                    continue
                if is_flag_opt(tokens[j], tv):
                    j += 1
                    continue
                break
            if j < n:
                return j, True
        return idx, False
    return idx, False


UV_WRAPPER_VAL_OPTS = frozenset({
    "--directory", "--project", "--python", "-p", "--with", "-w", "--from",
    "--with-editable", "--with-requirements", "--index-url", "--find-links",
    "-f", "--config-file", "--cache-dir", "-C",
})
PNPM_WRAPPER_VAL_OPTS = frozenset({
    "-C", "--dir", "--filter", "-F", "--prefix", "--store-dir", "--cache-dir",
    "--loglevel", "--package", "-p", "--cwd",
})
NPM_VAL_OPTS = frozenset({
    "--prefix", "-C", "--userconfig", "--cache", "--registry", "--workspace", "-w",
    "--loglevel", "--location", "--script-shell", "--tag", "--otp",
    "--fetch-retries", "--maxsockets",
})


def _extract_cmd_substitutions(command: str) -> tuple[list[str], bool]:
    """Extract unquoted/double-quoted $(...), `...`, and unquoted <(...)/>(...) process substitutions."""
    subs: list[str] = []
    quote: str | None = None
    escaped = False
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            i += 1
            continue
        if quote == "'":
            if ch == "'":
                quote = None
            i += 1
            continue
        if ch == "'" and quote is None:
            quote = "'"
            i += 1
            continue
        if ch == '"':
            quote = None if quote == '"' else '"'
            i += 1
            continue
        if quote is None and ch == "#" and (i == 0 or command[i - 1] in " \t\n;|&()"):
            while i < n and command[i] != "\n":
                i += 1
            continue
        if ch == "`":
            j = i + 1
            esc2 = False
            while j < n:
                c2 = command[j]
                if esc2:
                    esc2 = False
                    j += 1
                    continue
                if c2 == "\\":
                    esc2 = True
                    j += 1
                    continue
                if c2 == "`":
                    break
                j += 1
            if j >= n:
                return subs, True
            subs.append(command[i + 1 : j].replace("\\`", "`"))
            i = j + 1
            continue
        if (ch == "$" and i + 1 < n and command[i + 1] == "(") or (
            quote is None and i + 1 < n and command[i : i + 2] in ("<(", ">(")
        ):
            sub_end = _scan_cmd_subst_end(command, i)
            if sub_end < 0:
                return subs, True
            subs.append(command[i + 2 : sub_end - 1])
            i = sub_end
            continue
        i += 1
    return subs, False


def _unwrap_prefix_args(
    base: str,
    args: list[str],
    depth: int,
    env_vals_out: list[str] | None = None,
) -> tuple[list[str] | None, bool, bool]:
    """Skip options for prefix wrappers. Returns (remaining_tokens, immediate_rm_found, shell_mode)."""
    n_args = len(args)
    idx = 0
    if base == "env":
        split_strs: list[str] = []
        while idx < n_args:
            tok = args[idx]
            if tok == "--":
                idx += 1
                break
            if not tok.startswith("-") and "=" in tok:
                if env_vals_out is not None:
                    env_vals_out.append(tok.split("=", 1)[1])
                idx += 1
                continue
            if tok in ("-S", "--split-string"):
                if idx + 1 >= n_args:
                    return None, True, False
                split_strs.append(args[idx + 1])
                idx += 2
                continue
            if tok.startswith("--split-string="):
                split_strs.append(tok.split("=", 1)[1])
                idx += 1
                continue
            if tok.startswith("-") and not tok.startswith("--") and "S" in tok:
                pos = tok.index("S")
                if pos + 1 < len(tok):
                    split_strs.append(tok[pos + 1 :])
                    idx += 1
                    continue
                if idx + 1 >= n_args:
                    return None, True, False
                split_strs.append(args[idx + 1])
                idx += 2
                continue
            if tok in ENV_VAL_OPTS:
                idx += 2
                continue
            if tok.startswith("-") and tok != "-":
                idx += 1
                continue
            break
        rem = args[idx:]
        if split_strs:
            combined_head: list[str] = []
            for s_val in split_strs:
                if contains_rm_command(s_val, depth + 1):
                    return None, True, False
                try:
                    combined_head.extend(shlex.split(s_val, posix=True))
                except ValueError:
                    return None, True, False
            rem = combined_head + rem
        return rem, False, False

    val_opts = PREFIX_WRAPPER_VAL_OPTS[base]
    shell_mode = False
    while idx < n_args:
        tok = args[idx]
        if tok == "--":
            idx += 1
            break
        if base == "sudo":
            if tok in ("--shell", "--login") or (
                tok.startswith("-") and not tok.startswith("--") and any(c in tok[1:] for c in ("s", "i"))
            ):
                shell_mode = True
            if tok in val_opts:
                idx += 2
                continue
            if tok.startswith("-") and not tok.startswith("--"):
                consumed_next = False
                for c_i, ch in enumerate(tok[1:]):
                    if ch in "ugCDhprtTUR":
                        idx += 1 if c_i + 1 < len(tok) - 1 else 2
                        consumed_next = True
                        break
                if not consumed_next:
                    idx += 1
                continue
        if tok in val_opts:
            idx += 2
            continue
        if tok.startswith("-") and tok != "-":
            idx += 1
            continue
        break
    if base == "sudo":
        while idx < n_args and not args[idx].startswith("-") and "=" in args[idx]:
            if env_vals_out is not None:
                env_vals_out.append(args[idx].split("=", 1)[1])
            idx += 1
    if base in ("timeout", "gtimeout") and idx < n_args and not _is_rm_cmd_word(args[idx]):
        idx += 1
    return args[idx:], False, shell_mode


def _tokens_contain_rm(
    tokens: list[str],
    _depth: int = 0,
    _inherited_env: list[str] | None = None,
) -> bool:
    """Semantically inspect command tokens to detect rm invocations without false-positiving on search globs."""
    if _depth > 4:
        return True
    cur, stripped_env = _strip_leading_shell_tokens_with_env(tokens)
    env_vals = list(_inherited_env or []) + stripped_env
    if not cur:
        return False
    if _is_rm_cmd_word(cur[0]):
        return True

    base = os.path.basename(cur[0].lstrip("=$")).lower()
    args = cur[1:]

    if base == "env" or base in PREFIX_WRAPPER_VAL_OPTS:
        rem, found, shell_mode = _unwrap_prefix_args(base, args, _depth, env_vals_out=env_vals)
        if found:
            return True
        if not rem:
            return False
        if shell_mode and (
            contains_rm_command(" ".join(rem), _depth + 1)
            or any(contains_rm_command(arg, _depth + 1) for arg in rem)
        ):
            return True
        return _tokens_contain_rm(rem, _depth + 1, env_vals)

    if base in ("uv", "uvx", "pnpm", "pnpx", "npx", "yarn", "bun", "bunx", "npm"):
        if base == "npm":
            ni = 0
            while ni < len(args):
                if args[ni] in ("-c", "--call") and ni + 1 < len(args):
                    if RM_WORD_RE.search(args[ni + 1]) or contains_rm_command(args[ni + 1], _depth + 1):
                        return True
                    ni += 2
                    continue
                if args[ni].startswith("--call="):
                    val_c = args[ni].split("=", 1)[1]
                    if RM_WORD_RE.search(val_c) or contains_rm_command(val_c, _depth + 1):
                        return True
                ni += 1
        next_idx, unwrapped = _unwrap_pkg_manager_invocation(
            cur, 0, lambda t: t, lambda t, tv: tv.startswith("-") and tv != "-"
        )
        if unwrapped and next_idx < len(cur):
            return _tokens_contain_rm(cur[next_idx:], _depth + 1, env_vals)

    if base == "find":
        idx = 0
        n_args = len(args)
        while idx < n_args:
            if args[idx] in ("-exec", "-execdir", "-ok", "-okdir"):
                sub_start = idx + 1
                sub_end = _find_exec_end(args, sub_start)
                sub = args[sub_start:sub_end]
                if sub and _tokens_contain_rm(sub, _depth + 1):
                    return True
                idx = sub_end + 1
                continue
            idx += 1
        return False

    if base in ("fd", "fdfind"):
        idx = 0
        n_args = len(args)
        while idx < n_args:
            tok = args[idx]
            if tok == "--":
                break
            if tok in ("-x", "--exec", "-X", "--exec-batch"):
                sub_start = idx + 1
                sub_end = sub_start
                while sub_end < n_args and args[sub_end] != ";":
                    sub_end += 1
                sub = args[sub_start:sub_end]
                if sub:
                    if len(sub) == 1 and re.search(r"\s", sub[0]):
                        if contains_rm_command(sub[0], _depth + 1):
                            return True
                    elif _tokens_contain_rm(sub, _depth + 1):
                        return True
                idx = sub_end + 1
                continue
            if tok.startswith(("--exec=", "--exec-batch=")):
                val = tok.split("=", 1)[1]
                if _is_rm_cmd_word(val) or contains_rm_command(val, _depth + 1):
                    return True
            elif tok.startswith("-") and not tok.startswith("--") and ("x" in tok[1:] or "X" in tok[1:]):
                pos_x = tok.find("x", 1)
                pos_big = tok.find("X", 1)
                pos = min(p for p in (pos_x, pos_big) if p >= 0)
                if pos + 1 < len(tok):
                    val = tok[pos + 1 :]
                    sub = [val] + args[idx + 1 :]
                    if _tokens_contain_rm(sub, _depth + 1) or contains_rm_command(val, _depth + 1):
                        return True
                    break
                sub_start = idx + 1
                sub_end = sub_start
                while sub_end < n_args and args[sub_end] != ";":
                    sub_end += 1
                sub = args[sub_start:sub_end]
                if sub:
                    if len(sub) == 1 and re.search(r"\s", sub[0]):
                        if contains_rm_command(sub[0], _depth + 1):
                            return True
                    elif _tokens_contain_rm(sub, _depth + 1):
                        return True
                idx = sub_end + 1
                continue
            idx += 1
        return False

    if base == "rg":
        idx = 0
        n_args = len(args)
        while idx < n_args:
            tok = args[idx]
            if tok == "--":
                break
            if tok == "--pre":
                if idx + 1 >= n_args:
                    return True
                val = args[idx + 1]
                if _is_rm_cmd_word(val) or contains_rm_command(val, _depth + 1):
                    return True
                idx += 2
                continue
            if tok.startswith("--pre="):
                val = tok.split("=", 1)[1]
                if _is_rm_cmd_word(val) or contains_rm_command(val, _depth + 1):
                    return True
            idx += 1
        return False

    if base in ("ag", "ack"):
        idx = 0
        n_args = len(args)
        while idx < n_args:
            tok = args[idx]
            if tok == "--":
                break
            if tok == "--pager":
                if idx + 1 >= n_args:
                    return True
                val = args[idx + 1]
                if _is_rm_cmd_word(val) or contains_rm_command(val, _depth + 1):
                    return True
                idx += 2
                continue
            if tok.startswith("--pager="):
                val = tok.split("=", 1)[1]
                if _is_rm_cmd_word(val) or contains_rm_command(val, _depth + 1):
                    return True
            idx += 1
        return False

    if base == "xargs":
        rem = _skip_xargs_opts(args)
        return bool(rem and _tokens_contain_rm(rem, _depth + 1))

    if base == "parallel":
        colon_idx = next((i for i, t in enumerate(args) if t.startswith(":::")), len(args))
        cmd_part = args[:colon_idx]
        after_colons = [t for t in args[colon_idx:] if not t.startswith(":::")]
        repl_str = "{}"
        idx = 0
        while idx < len(cmd_part):
            tok = cmd_part[idx]
            if tok == "--":
                idx += 1
                break
            if not tok.startswith("-") or tok == "-":
                break
            if tok in ("-I", "--replace-str") and idx + 1 < len(cmd_part):
                repl_str = cmd_part[idx + 1]
                idx += 2
                continue
            if tok.startswith("-I") and len(tok) > 2:
                repl_str = tok[2:]
                idx += 1
                continue
            if tok.startswith("--replace-str="):
                repl_str = tok.split("=", 1)[1]
                idx += 1
                continue
            if tok in PARALLEL_VALUE_OPTS:
                idx += 2
                continue
            if tok.startswith("--") and "=" in tok:
                idx += 1
                continue
            idx += 1
        rem = cmd_part[idx:]
        if not rem:
            return any(contains_rm_command(t, _depth + 1) for t in after_colons[:50])
        if re.search(r"\s", rem[0]) and contains_rm_command(rem[0], _depth + 1):
            return True
        if _tokens_contain_rm(rem, _depth + 1):
            return True
        has_placeholder = any(
            (repl_str and repl_str in t) or re.search(r"\{(?:\d+|[./#]+)?\}", t)
            for t in rem
        )
        if has_placeholder and after_colons:
            for inp in after_colons[:50]:
                sub_tokens = [
                    re.sub(r"\{(?:\d+|[./#]+)?\}", inp, t.replace(repl_str, inp) if repl_str else t)
                    for t in rem
                ]
                if _tokens_contain_rm(sub_tokens, _depth + 1) or contains_rm_command(" ".join(sub_tokens), _depth + 1):
                    return True
        return False

    if base in SHELL_PROGS:
        for shell_cmd in _extract_shell_exec_inputs(args):
            if contains_rm_command(shell_cmd, _depth + 1):
                return True
        return False

    if base == "eval":
        eval_args = list(args)
        while eval_args and eval_args[0] == "--":
            eval_args.pop(0)
        return bool(eval_args and contains_rm_command(" ".join(eval_args), _depth + 1))

    if base == "watch":
        idx = 0
        n_args = len(args)
        while idx < n_args:
            tok = args[idx]
            if tok == "--":
                idx += 1
                break
            if not tok.startswith("-") or tok == "-":
                break
            if tok in ("-n", "--interval"):
                idx += 2
                continue
            idx += 1
        rem = args[idx:]
        if not rem:
            return False
        if _tokens_contain_rm(rem, _depth + 1):
            return True
        return contains_rm_command(" ".join(rem), _depth + 1)

    if base in ("awk", "gawk", "nawk", "mawk"):
        for tok in args:
            if re.search(
                r"""\bsystem\s*\(|\|\s*getline\b|(?<!\|)\|(?!\|)\s*(?:['"(A-Za-z_$])""",
                tok,
            ):
                sc_awk = _collapse_script_strings(tok)
                if _contains_obfuscated_rm_in_script(tok) or re.search(
                    r"""\bsystem\s*\(\s*(?!(?:['"][^'"]*['"])\s*\))""", sc_awk
                ):
                    return True
        return _has_herestring_rm(args, obfuscated_script=True)

    if base == "osascript":
        idx = 0
        n_args = len(args)
        osa_scripts: list[str] = []
        while idx < n_args:
            tok = args[idx]
            if tok == "--":
                break
            if tok in ("-l", "-s"):
                idx += 2
                continue
            if tok == "-e" and idx + 1 < n_args:
                osa_scripts.append(args[idx + 1])
                if _osascript_has_unsafe_shell(args[idx + 1]):
                    return True
                idx += 2
                continue
            if tok.startswith("-e") and len(tok) > 2:
                osa_scripts.append(tok[2:])
                if _osascript_has_unsafe_shell(tok[2:]):
                    return True
                idx += 1
                continue
            if not tok.startswith("-"):
                break
            idx += 1
        if osa_scripts and _osascript_has_unsafe_shell("\n".join(osa_scripts)):
            return True
        for hs in _extract_herestring_texts(args):
            if _interp_reads_code_from_stdin(base, args) and _osascript_has_unsafe_shell(hs):
                return True
            if RM_WORD_RE.search(hs):
                return True
        return False

    if base == "script":
        s_idx = 0
        while s_idx < len(args):
            st = args[s_idx]
            if st == "--":
                s_idx += 1
                break
            if st in ("-t", "--timing"):
                s_idx += 2
                continue
            if st in ("-c", "--command") and s_idx + 1 < len(args):
                if contains_rm_command(args[s_idx + 1], _depth + 1):
                    return True
                s_idx += 2
                continue
            if st.startswith("--command="):
                if contains_rm_command(st.split("=", 1)[1], _depth + 1):
                    return True
                s_idx += 1
                continue
            if st.startswith("-"):
                s_idx += 1
                continue
            break
        s_rem = args[s_idx:]
        if s_rem:
            if _tokens_contain_rm(s_rem, _depth + 1):
                return True
            if len(s_rem) >= 2 and _tokens_contain_rm(s_rem[1:], _depth + 1):
                return True
        return False

    if re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", base):
        found_c = _scan_python_cli_c_flag(args, lambda t: t)
        if found_c is not None:
            c_idx, c_pos = found_c
            py_src = args[c_idx][c_pos + 1 :] if c_pos >= 0 else args[c_idx]
            if RM_WORD_RE.search(py_src):
                return True
            if _depth < 2:
                py_rm, py_cmds, _, _ = _inspect_python_code(py_src, _depth=_depth + 1)
                if py_rm or any(contains_rm_command(pc, _depth + 1) for pc in py_cmds):
                    return True
                return False
            return _contains_obfuscated_rm_in_script(py_src)
        for hs in _extract_herestring_texts(args):
            if RM_WORD_RE.search(hs):
                return True
            if _interp_reads_code_from_stdin(base, args):
                if _depth < 2:
                    py_rm, py_cmds, _, _ = _inspect_python_code(hs, _depth=_depth + 1)
                    if py_rm or any(contains_rm_command(pc, _depth + 1) for pc in py_cmds):
                        return True
                elif _contains_obfuscated_rm_in_script(hs):
                    return True
        return False

    if base == "node":
        idx = 0
        n_args = len(args)
        node_chunks: list[str] = []
        while idx < n_args:
            tok = args[idx]
            if tok == "--":
                break
            if tok in NODE_VAL_OPTS:
                idx += 2
                continue
            if (tok in ("-e", "--eval", "-p", "--print") or (
                tok.startswith("-") and not tok.startswith("--") and any(c in tok[1:] for c in ("e", "p"))
            )) and idx + 1 < n_args:
                node_chunks.append(args[idx + 1])
                if _script_stmt_has_native_del(args[idx + 1], "node"):
                    return True
                idx += 2
                continue
            if tok.startswith(("--eval=", "--print=")):
                val_n = tok.split("=", 1)[1]
                node_chunks.append(val_n)
                if _script_stmt_has_native_del(val_n, "node"):
                    return True
            if not tok.startswith("-"):
                break
            idx += 1
        if node_chunks and _script_stmt_has_native_del("\n".join(node_chunks), "node"):
            return True
        for hs in _extract_herestring_texts(args):
            if RM_WORD_RE.search(hs) or _script_stmt_has_native_del(hs, "node"):
                return True
        return False

    if base in ("perl", "ruby"):
        val_opts = (RUBY_VAL_OPTS | frozenset({"-C", "-F", "-x"})) if base == "ruby" else frozenset({"-I", "-F", "-M", "-m", "-x"})
        attached_pfxs = ("-I", "-r", "-C", "-E", "-F", "-W", "-x", "-0", "-K") if base == "ruby" else ("-I", "-M", "-m", "-C", "-D", "-d", "-F", "-i", "-x", "-0")
        idx = 0
        n_args = len(args)
        script_chunks: list[str] = []
        while idx < n_args:
            tok = args[idx]
            if tok == "--":
                break
            if tok in val_opts:
                idx += 2
                continue
            if len(tok) > 2 and tok.startswith(attached_pfxs):
                idx += 1
                continue
            if tok in ("-e",) or (base == "perl" and tok == "-E"):
                if idx + 1 < n_args:
                    script_chunks.append(args[idx + 1])
                    idx += 2
                    continue
                idx += 1
                continue
            if tok.startswith("-e") or (base == "perl" and tok.startswith("-E")):
                script_chunks.append(tok[2:])
                idx += 1
                continue
            if tok.startswith("-") and not tok.startswith("--"):
                consumed_next = False
                for pos, ch in enumerate(tok[1:], 1):
                    if ("-" + ch) in attached_pfxs:
                        break
                    if ch == "e" or (base == "perl" and ch == "E"):
                        if pos + 1 < len(tok):
                            script_chunks.append(tok[pos + 1 :])
                        elif idx + 1 < n_args:
                            script_chunks.append(args[idx + 1])
                            consumed_next = True
                        break
                idx += 2 if consumed_next else 1
                continue
            if not tok.startswith("-"):
                break
            idx += 1
        if script_chunks:
            if any(_script_stmt_has_native_del(sc, base) for sc in script_chunks) or _script_stmt_has_native_del("\n".join(script_chunks), base):
                return True
        for hs in _extract_herestring_texts(args):
            if RM_WORD_RE.search(hs) or _script_stmt_has_native_del(hs, base):
                return True
        return _has_herestring_rm(args, obfuscated_script=True)

    if base == "git":
        has_global_cfg = False
        subcmd: str | None = None
        sub_args: list[str] = []
        idx = 0
        n_args = len(args)
        while idx < n_args:
            tok = args[idx]
            if tok == "--":
                idx += 1
                if idx < n_args:
                    subcmd = args[idx]
                    sub_args = args[idx + 1 :]
                break
            if not tok.startswith("-") or tok == "-":
                subcmd = tok
                sub_args = args[idx + 1 :]
                break
            if tok in ("-c", "--config-env") or tok.startswith("-c") or tok.startswith("--config-env="):
                has_global_cfg = True
                idx += 2 if tok in ("-c", "--config-env") else 1
                continue
            if tok in GIT_GLOBAL_VAL_OPTS:
                idx += 2
                continue
            idx += 1
        if has_global_cfg:
            if any(RM_WORD_RE.search(t) for t in args) or any(
                RM_WORD_RE.search(v) or contains_rm_command(v, _depth + 1) for v in env_vals
            ):
                return True
        if any(t == "--exec" or t.startswith("--exec=") for t in args):
            if any(RM_WORD_RE.search(t) for t in args):
                return True
        if subcmd == "rebase":
            j = 0
            while j < len(sub_args):
                st = sub_args[j]
                if st == "--":
                    break
                if st == "-x":
                    if j + 1 >= len(sub_args) or contains_rm_command(sub_args[j + 1], _depth + 1):
                        return True
                    j += 2
                    continue
                if st.startswith("-x") and len(st) > 2:
                    if contains_rm_command(st[2:], _depth + 1):
                        return True
                j += 1
        if subcmd == "grep":
            j = 0
            while j < len(sub_args):
                st = sub_args[j]
                if st == "--":
                    break
                if st in ("-O", "--open-files-in-pager"):
                    if j + 1 < len(sub_args):
                        val = sub_args[j + 1]
                        if _is_rm_cmd_word(val) or contains_rm_command(val, _depth + 1):
                            return True
                    j += 2
                    continue
                if st.startswith("--open-files-in-pager="):
                    val = st.split("=", 1)[1]
                    if _is_rm_cmd_word(val) or contains_rm_command(val, _depth + 1):
                        return True
                elif st.startswith("-O") and len(st) > 2:
                    val = st[2:]
                    if _is_rm_cmd_word(val) or contains_rm_command(val, _depth + 1):
                        return True
                j += 1
        if subcmd == "bisect" and len(sub_args) >= 2 and sub_args[0] == "run":
            if _tokens_contain_rm(sub_args[1:], _depth + 1) or contains_rm_command(" ".join(sub_args[1:]), _depth + 1):
                return True
        if subcmd == "submodule":
            j = 0
            while j < len(sub_args) and sub_args[j].startswith("-"):
                j += 1
            if j < len(sub_args) and sub_args[j] == "foreach":
                j += 1
                while j < len(sub_args):
                    st = sub_args[j]
                    if st == "--":
                        j += 1
                        break
                    if st.startswith("-"):
                        j += 1
                        continue
                    break
                rem_fe = sub_args[j:]
                if rem_fe and (
                    _tokens_contain_rm(rem_fe, _depth + 1)
                    or any(contains_rm_command(x, _depth + 1) for x in rem_fe)
                    or contains_rm_command(" ".join(rem_fe), _depth + 1)
                ):
                    return True
        return False

    return False


DANGEROUS_PY_SHELL_FUNCS = frozenset({
    "system", "popen", "getoutput", "getstatusoutput", "create_subprocess_shell",
})
DANGEROUS_PY_ARGV_FUNCS = frozenset({
    "Popen", "check_output", "check_call", "create_subprocess_exec",
})
DANGEROUS_PY_OS_EXEC_FUNCS = frozenset({
    "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
    "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
    "posix_spawn", "posix_spawnp",
})
DANGEROUS_PY_ATTR_NAMES = (
    DANGEROUS_PY_SHELL_FUNCS
    | DANGEROUS_PY_ARGV_FUNCS
    | DANGEROUS_PY_OS_EXEC_FUNCS
    | frozenset({
        "run", "call", "spawn", "eval", "exec", "compile", "import_module",
        "setattr", "delattr", "getattr", "globals", "locals", "vars", "__import__",
        "remove", "unlink", "rmdir", "removedirs", "rmtree",
    })
)
SAFE_PY_INSPECT_MODS = frozenset({
    "ast", "asyncio", "base64", "bisect", "builtins", "calendar", "collections",
    "copy", "csv", "dataclasses", "datetime", "decimal", "difflib", "enum",
    "fnmatch", "fractions", "functools", "glob", "gzip", "hashlib", "heapq",
    "hmac", "html", "http", "io", "ipaddress", "itertools", "json", "logging",
    "math", "operator", "os", "pathlib", "posix", "posixpath", "pprint", "pty",
    "re", "shlex", "shutil", "socket", "sqlite3", "ssl", "stat", "statistics",
    "string", "struct", "subprocess", "sys", "tarfile", "tempfile", "textwrap",
    "time", "tomllib", "typing", "unicodedata", "urllib", "uuid", "xml",
    "zipfile", "zlib",
})
SAFE_PY_DUNDERS = frozenset({
    "__name__", "__file__", "__doc__", "__version__", "__len__", "__str__", "__repr__",
    "__enter__", "__exit__", "__setitem__", "__getitem__", "__delitem__", "__contains__",
    "__iter__", "__next__", "__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__",
    "__hash__", "__bool__", "__int__", "__float__", "__index__", "__fspath__",
})
PY_MUTATING_METHODS = frozenset({
    "append", "extend", "insert", "pop", "remove", "clear",
    "reverse", "sort", "update", "setdefault", "__setitem__", "__delitem__",
})


@dataclass
class ShellToken:
    value: str
    expandable: list[bool]
    had_quote_or_escape: bool
    raw: str
    raw_start: int
    raw_end: int
    has_cmd_subst: bool


@dataclass
class SafeVarInfo:
    kind: str  # "mktemp_file" | "mktemp_dir" | "tmp_path" | "tmp_root" | "scratch_root" | "scratch_subpath" | "ws_root" | "ws_subpath"
    base_path: str
    chain_only: bool
    ws_real: str = ""


def _strip_shell_line_continuations(command: str) -> str:
    """Remove unquoted or double-quoted backslash-newline line continuations cleanly per POSIX shell rules."""
    out: list[str] = []
    quote: str | None = None
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if quote == "'":
            out.append(ch)
            if ch == "'":
                quote = None
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            if command[i + 1] == "\n":
                i += 2
                continue
            if command[i + 1] == "\r" and i + 2 < n and command[i + 2] == "\n":
                i += 3
                continue
            out.append(ch)
            out.append(command[i + 1])
            i += 2
            continue
        if ch in ("'", '"'):
            if quote is None:
                quote = ch
            elif quote == ch:
                quote = None
        out.append(ch)
        i += 1
    return "".join(out)


_REDIR_OPS_ORDERED = (
    "&>>", "<<<", "<<-", "&>", "<<", ">>", ">&", "<&", "<>", ">|", ">", "<",
)


def _match_redir_op_at(s: str, i: int) -> str | None:
    """Return unquoted redirection operator starting at s[i] (skipping <(...) / >(...) proc-subst)."""
    if s[i : i + 2] in ("<(", ">("):
        return None
    for op in _REDIR_OPS_ORDERED:
        if s.startswith(op, i):
            return op
    return None


def _space_unquoted_redirs(command: str) -> str:
    """Separate unquoted attached redirections (e.g. rm</dev/null, rm&>/dev/null, cat<<<'x') for shlex splitting."""
    out: list[str] = []
    n = len(command)
    i = 0
    quote: str | None = None
    escaped = False
    word_digits_only = False
    in_word = False

    while i < n:
        ch = command[i]
        if escaped:
            out.append(ch)
            escaped = False
            in_word = True
            word_digits_only = False
            i += 1
            continue
        if ch == "\\" and quote != "'":
            out.append(ch)
            escaped = True
            in_word = True
            word_digits_only = False
            i += 1
            continue
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            out.append(ch)
            quote = ch
            in_word = True
            word_digits_only = False
            i += 1
            continue
        if ch == "#" and (i == 0 or command[i - 1] in " \t\n;|&()"):
            end_c = command.find("\n", i)
            if end_c < 0:
                out.append(command[i:])
                break
            out.append(command[i:end_c])
            i = end_c
            in_word = False
            word_digits_only = False
            continue
        if ch == "$" and i + 1 < n and command[i + 1] == "(":
            sub_end = _scan_cmd_subst_end(command, i)
            if sub_end < 0:
                out.append(command[i:])
                break
            out.append(command[i:sub_end])
            i = sub_end
            in_word = True
            word_digits_only = False
            continue
        if ch == "`":
            bt_end = _scan_backtick_end(command, i)
            if bt_end < 0:
                out.append(command[i:])
                break
            out.append(command[i:bt_end])
            i = bt_end
            in_word = True
            word_digits_only = False
            continue
        if ch.isspace() or ch in ";|&()":
            if ch == "&" and command[i : i + 2] == "&>":
                pass
            else:
                out.append(ch)
                in_word = False
                word_digits_only = False
                i += 1
                continue
        if ch in ("<", ">") or command[i : i + 2] == "&>":
            op = _match_redir_op_at(command, i)
            if op:
                if in_word and not word_digits_only:
                    out.append(" ")
                out.append(op)
                i += len(op)
                if op in (">&", "<&"):
                    while i < n and (command[i].isdigit() or command[i] == "-"):
                        out.append(command[i])
                        i += 1
                if i < n and not command[i].isspace() and command[i] not in ";|&()":
                    out.append(" ")
                in_word = False
                word_digits_only = False
                continue
        out.append(ch)
        if not in_word:
            in_word = True
            word_digits_only = ch.isdigit()
        elif word_digits_only and not ch.isdigit():
            word_digits_only = False
        i += 1
    return "".join(out)


def _split_tokens_with_quote_info(stmt: str) -> list[ShellToken] | None:
    """Tokenize a shell statement while preserving per-char expandability, raw offsets, and command substitution markers."""
    tokens: list[ShellToken] = []
    n = len(stmt)
    i = 0
    while i < n:
        while i < n:
            if stmt[i].isspace():
                i += 1
            elif stmt[i : i + 2] == "\\\n":
                i += 2
            elif stmt[i : i + 3] == "\\\r\n":
                i += 3
            else:
                break
        if i >= n:
            break
        if stmt[i] == "#":
            while i < n and stmt[i] != "\n":
                i += 1
            continue
        start = i
        val_chars: list[str] = []
        exp_flags: list[bool] = []
        had_qe = False
        has_subst = False
        quote: str | None = None

        while i < n:
            ch = stmt[i]
            if quote is None and ch.isspace():
                break
            if quote == "'":
                if ch == "'":
                    quote = None
                    i += 1
                    continue
                val_chars.append(ch)
                exp_flags.append(False)
                i += 1
                continue
            if ch == "\\" and i + 1 < n:
                if stmt[i + 1] == "\n":
                    i += 2
                    continue
                if stmt[i + 1] == "\r" and i + 2 < n and stmt[i + 2] == "\n":
                    i += 3
                    continue
                had_qe = True
                val_chars.append(stmt[i + 1])
                exp_flags.append(False)
                i += 2
                continue
            if ch == "\\":
                return None
            if quote == '"':
                if ch == '"':
                    quote = None
                    i += 1
                    continue
                if ch == "$" and i + 1 < n and stmt[i + 1] == "(":
                    has_subst = True
                    sub_end = _scan_cmd_subst_end(stmt, i)
                    if sub_end < 0:
                        return None
                    piece = stmt[i:sub_end]
                    val_chars.extend(piece)
                    exp_flags.extend([True] * len(piece))
                    i = sub_end
                    continue
                if ch == "`":
                    has_subst = True
                    bt_end = _scan_backtick_end(stmt, i)
                    if bt_end < 0:
                        return None
                    piece = stmt[i:bt_end]
                    val_chars.extend(piece)
                    exp_flags.extend([True] * len(piece))
                    i = bt_end
                    continue
                val_chars.append(ch)
                exp_flags.append(True)
                i += 1
                continue
            if ch in ("'", '"'):
                had_qe = True
                quote = ch
                i += 1
                continue
            if ch == "$" and i + 1 < n and stmt[i + 1] == "(":
                has_subst = True
                sub_end = _scan_cmd_subst_end(stmt, i)
                if sub_end < 0:
                    return None
                piece = stmt[i:sub_end]
                val_chars.extend(piece)
                exp_flags.extend([True] * len(piece))
                i = sub_end
                continue
            if ch == "`":
                has_subst = True
                bt_end = _scan_backtick_end(stmt, i)
                if bt_end < 0:
                    return None
                piece = stmt[i:bt_end]
                val_chars.extend(piece)
                exp_flags.extend([True] * len(piece))
                i = bt_end
                continue
            if ch in ("<", ">") or (ch == "&" and stmt[i : i + 2] == "&>"):
                op = _match_redir_op_at(stmt, i)
                if op:
                    if val_chars and not (not had_qe and "".join(val_chars).isdigit()):
                        break
                    val_chars.extend(op)
                    exp_flags.extend([False] * len(op))
                    i += len(op)
                    if op in (">&", "<&") and i < n and (stmt[i].isdigit() or stmt[i] == "-"):
                        while i < n and (stmt[i].isdigit() or stmt[i] == "-"):
                            val_chars.append(stmt[i])
                            exp_flags.append(False)
                            i += 1
                    break
            val_chars.append(ch)
            exp_flags.append(True)
            i += 1

        if quote is not None:
            return None
        tokens.append(ShellToken(
            value="".join(val_chars),
            expandable=exp_flags,
            had_quote_or_escape=had_qe,
            raw=stmt[start:i],
            raw_start=start,
            raw_end=i,
            has_cmd_subst=has_subst,
        ))
    return tokens


def _scan_cmd_subst_end(s: str, dollar_idx: int) -> int:
    """Given s[dollar_idx:dollar_idx+2] == '$(', return exclusive index of matching ')'. Returns -1 if unclosed."""
    n = len(s)
    j = dollar_idx + 2
    depth = 1
    q: str | None = None
    esc = False
    while j < n and depth > 0:
        c = s[j]
        if esc:
            esc = False
            j += 1
            continue
        if c == "\\" and q != "'":
            esc = True
            j += 1
            continue
        if q:
            if c == q:
                q = None
            j += 1
            continue
        if c in ("'", '"'):
            q = c
            j += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return -1


def _scan_backtick_end(s: str, tick_idx: int) -> int:
    """Given s[tick_idx] == '`', return exclusive index of closing '`'. Returns -1 if unclosed."""
    n = len(s)
    j = tick_idx + 1
    esc = False
    while j < n:
        c = s[j]
        if esc:
            esc = False
            j += 1
            continue
        if c == "\\":
            esc = True
            j += 1
            continue
        if c == "`":
            return j + 1
        j += 1
    return -1


def _split_top_level_statements(command: str) -> list[dict] | None:
    """Split shell command into top-level statements preserving prev/next connectors and parenthesis depth."""
    stmts: list[dict] = []
    buf: list[str] = []
    n = len(command)
    i = 0
    quote: str | None = None
    escaped = False
    paren_depth = 0
    saw_comment = False
    conn_prev: str | None = None

    def flush(conn_next: str | None) -> None:
        nonlocal conn_prev
        txt = "".join(buf).strip()
        buf.clear()
        if txt:
            stmts.append({
                "text": txt,
                "conn_prev": conn_prev,
                "conn_next": conn_next,
                "paren_depth": paren_depth,
            })
        conn_prev = conn_next

    while i < n:
        ch = command[i]
        if saw_comment:
            if ch == "\n":
                saw_comment = False
                flush("\n")
            i += 1
            continue
        if escaped:
            buf.append(ch)
            escaped = False
            i += 1
            continue
        if ch == "\\" and quote != "'":
            buf.append(ch)
            escaped = True
            i += 1
            continue
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "#" and (i == 0 or command[i - 1] in " \t\n;|&()"):
            saw_comment = True
            i += 1
            continue
        if ch == "$" and i + 1 < n and command[i + 1] == "(":
            sub_end = _scan_cmd_subst_end(command, i)
            if sub_end < 0:
                return None
            buf.append(command[i:sub_end])
            i = sub_end
            continue
        if ch == "`":
            bt_end = _scan_backtick_end(command, i)
            if bt_end < 0:
                return None
            buf.append(command[i:bt_end])
            i = bt_end
            continue
        if ch == "(" or ch == ")":
            # Skip <(...) / >(...) as non-subshell process substitutions if needed, or handle subshell parens
            prev_ch = command[i - 1] if i > 0 else ""
            if ch == "(" and prev_ch in ("<", ">"):
                sub_end = _scan_cmd_subst_end(command, i - 1)
                if sub_end < 0:
                    return None
                buf.append(command[i:sub_end])
                i = sub_end
                continue
            flush(ch)
            if ch == "(":
                paren_depth += 1
            else:
                paren_depth -= 1
            i += 1
            continue
        if ch == "\n":
            flush("\n")
            i += 1
            continue
        if ch == ";":
            if i + 1 < n and command[i + 1] == ";":
                flush(";;")
                i += 2
                continue
            flush(";")
            i += 1
            continue
        if ch == "&":
            prev_ch = command[i - 1] if i > 0 else ""
            next_ch = command[i + 1] if i + 1 < n else ""
            if prev_ch in (">", "<") or next_ch == ">":
                buf.append(ch)
                i += 1
                continue
            if next_ch == "&":
                flush("&&")
                i += 2
                continue
            flush("&")
            i += 1
            continue
        if ch == "|":
            prev_ch = command[i - 1] if i > 0 else ""
            if prev_ch == ">":
                buf.append(ch)
                i += 1
                continue
            next_ch = command[i + 1] if i + 1 < n else ""
            if next_ch == "|":
                flush("||")
                i += 2
                continue
            if next_ch == "&":
                flush("|&")
                i += 2
                continue
            flush("|")
            i += 1
            continue
        buf.append(ch)
        i += 1

    if quote is not None or escaped:
        return None
    flush(None)
    return stmts


def _strip_group_wrappers(tokens: list[ShellToken]) -> list[ShellToken]:
    """Strip unquoted leading/trailing subshell/group delimiters '(' '{' ')' '}' cleanly."""
    out = list(tokens)
    while out:
        first = out[0]
        if first.value in ("(", "{") and not first.had_quote_or_escape:
            out.pop(0)
            continue
        if first.raw[:1] in ("(", "{") and len(first.value) == len(first.raw) and first.value[:1] in ("(", "{"):
            nv = first.value[1:]
            nr = first.raw[1:]
            if not nv:
                out.pop(0)
                continue
            out[0] = ShellToken(
                value=nv,
                expandable=first.expandable[1:],
                had_quote_or_escape=first.had_quote_or_escape,
                raw=nr,
                raw_start=first.raw_start + 1,
                raw_end=first.raw_end,
                has_cmd_subst=first.has_cmd_subst,
            )
            continue
        break
    while out:
        last = out[-1]
        if last.value in (")", "}") and not last.had_quote_or_escape:
            out.pop()
            continue
        if last.raw[-1:] in (")", "}") and last.value[-1:] in (")", "}"):
            nv = last.value[:-1]
            nr = last.raw[:-1]
            if not nv:
                out.pop()
                continue
            out[-1] = ShellToken(
                value=nv,
                expandable=last.expandable[:-1],
                had_quote_or_escape=last.had_quote_or_escape,
                raw=nr,
                raw_start=last.raw_start,
                raw_end=last.raw_end - 1,
                has_cmd_subst=last.has_cmd_subst,
            )
            continue
        break
    return out


def _advance_cmd_subst_wrapper_tokens(toks: list[ShellToken], idx: int) -> tuple[int, bool]:
    """Advance idx past one prefix command wrapper (env/command/sudo/xargs/parallel/etc.).

    Returns (new_idx, immediate_obfuscation_detected).
    """
    n = len(toks)
    t = toks[idx]
    base = os.path.basename(t.value.lstrip("=$")).lower()
    if base == "env":
        idx += 1
        while idx < n:
            tv = toks[idx].value
            tr = toks[idx].raw
            if tv == "--":
                idx += 1
                break
            if (not tv.startswith("-") and "=" in tr) or tv in ("-i", "-0"):
                idx += 1
                continue
            if tv in ("-S", "--split-string"):
                if idx + 1 >= n:
                    return idx, True
                if toks[idx + 1].has_cmd_subst or contains_rm_command(toks[idx + 1].value):
                    return idx, True
                idx += 2
                continue
            if tv.startswith("--split-string="):
                sv = tv.split("=", 1)[1]
                if toks[idx].has_cmd_subst or contains_rm_command(sv):
                    return idx, True
                idx += 1
                continue
            if tv in ENV_VAL_OPTS:
                idx += 2
                continue
            if tv.startswith("-") and tv != "-":
                idx += 1
                continue
            break
        return idx, False
    if base in PREFIX_WRAPPER_VAL_OPTS:
        val_opts = PREFIX_WRAPPER_VAL_OPTS[base]
        idx += 1
        while idx < n:
            tv = toks[idx].value
            if tv == "--":
                idx += 1
                break
            if tv in val_opts:
                idx += 2
                continue
            if base == "sudo" and tv.startswith("-") and not tv.startswith("--"):
                consumed_next = False
                for c_i, ch in enumerate(tv[1:]):
                    if ch in "ugCDhprtTUR":
                        idx += 1 if c_i + 1 < len(tv) - 1 else 2
                        consumed_next = True
                        break
                if not consumed_next:
                    idx += 1
                continue
            if tv.startswith("-") and tv != "-":
                idx += 1
                continue
            break
        if base == "sudo":
            while idx < n and not toks[idx].value.startswith("-") and "=" in toks[idx].raw:
                idx += 1
        if base in ("timeout", "gtimeout") and idx < n and not toks[idx].has_cmd_subst and not _is_rm_cmd_word(toks[idx].value):
            idx += 1
        return idx, False
    if base == "xargs":
        rem_vals = _skip_xargs_opts([tk.value for tk in toks[idx + 1 :]])
        return n - len(rem_vals), False
    if base == "parallel":
        idx += 1
        while idx < n:
            tv = toks[idx].value
            if tv == "--":
                idx += 1
                break
            if tv.startswith(":::"):
                return n, False
            if tv in PARALLEL_VALUE_OPTS:
                idx += 2
                continue
            if tv.startswith("-") and tv != "-":
                idx += 1
                continue
            break
        return idx, False
    return idx, False


def _has_cmd_word_subst_obfuscation(command: str) -> bool:
    """Detect obfuscated command words formed via $(...) / `...` including behind prefix/xargs wrappers."""
    stmts = _split_top_level_statements(command)
    if stmts is None:
        return bool(RM_WORD_RE.search(command))
    for st in stmts:
        toks = _split_tokens_with_quote_info(st["text"])
        if toks is None:
            if RM_WORD_RE.search(st["text"]):
                return True
            continue
        toks = _strip_group_wrappers(toks)
        idx = 0
        n = len(toks)
        while idx < n:
            t = toks[idx]
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t.raw):
                idx += 1
                continue
            if t.value.lower() in SHELL_PREFIX_KEYWORDS:
                idx += 1
                continue
            if REDIR_ONLY_RE.match(t.value):
                idx += 2
                continue
            if REDIR_INLINE_RE.match(t.value):
                idx += 1
                continue
            if t.has_cmd_subst:
                return True
            base = os.path.basename(t.value.lstrip("=$")).lower()
            if base == "env" or base in PREFIX_WRAPPER_VAL_OPTS or base in ("xargs", "parallel"):
                next_idx, found = _advance_cmd_subst_wrapper_tokens(toks, idx)
                if found:
                    return True
                if next_idx > idx:
                    idx = next_idx
                    continue
            break
    return False


def _reconstruct_echo_printf_text(base: str, args: list[str]) -> str:
    """Reconstruct emitted string from echo/printf/print arguments for pipeline-consumer inspection."""
    if not args:
        return ""
    idx = 0
    if base in ("echo", "print"):
        while idx < len(args) and re.fullmatch(r"-[neE]+", args[idx]):
            idx += 1
        joined = " ".join(args[idx:])
    else:
        fmt = args[0]
        fargs = args[1:]
        if not fargs:
            joined = fmt
        else:
            arg_pos = 0
            def _sub_fmt(m: re.Match[str]) -> str:
                nonlocal arg_pos
                if m.group(0) == "%%":
                    return "%"
                if arg_pos < len(fargs):
                    val = fargs[arg_pos]
                    arg_pos += 1
                    return val
                return ""
            try:
                formatted = re.sub(r"%%|%[-+0 #]*\d*(?:\.\d+)?[sdifuxXob]", _sub_fmt, fmt)
            except Exception:
                formatted = " ".join(args)
            if arg_pos < len(fargs):
                formatted = formatted + " " + " ".join(fargs[arg_pos:])
            joined = formatted
    if "\\" in joined:
        # Decode common ANSI/hex/octal escapes used in echo -e / printf (%b / \0NNN)
        def _unesc(m: re.Match[str]) -> str:
            if m.group(1) is not None:
                return chr(int(m.group(1), 16))
            if m.group(2) is not None:
                return chr(int(m.group(2), 16))
            if m.group(3) is not None:
                return chr(int(m.group(3), 16))
            if m.group(4) is not None:
                return chr(int(m.group(4), 8))
            if m.group(5) is not None:
                return chr(int(m.group(5), 8))
            return ANSI_C_SIMPLE_MAP.get(m.group(6) or "", m.group(0))
        try:
            return ECHO_PRINTF_ESC_RE.sub(_unesc, joined)
        except Exception:
            return joined
    return joined


def _script_stmt_has_native_del(text: str, lang: str = "") -> bool:
    """Detect obfuscated rm or native file-deletion/unsafe-exec APIs in node/perl/ruby/osascript text."""
    sc_c = _collapse_script_strings(text)
    if lang == "osascript":
        return _osascript_has_unsafe_shell(text)
    if _contains_obfuscated_rm_in_script(text):
        return True
    if lang in ("", "node"):
        if re.search(r"\b(?:rmSync|unlinkSync|rmdirSync)\s*\(|\bfs\s*\.\s*(?:promises\s*\.\s*)?(?:rm|unlink|rmdir)\s*\(", sc_c):
            return True
        if re.search(r"""(?:['"](?:node:)?fs/promises['"]|\.promises\b)""", sc_c) and re.search(
            r"\.\s*(?:rm|rmSync|unlink|unlinkSync|rmdir|rmdirSync)\s*\(", sc_c
        ):
            return True
        if re.search(
            r"""(?:\b(?:fs|fsp|fsPromises|cp)\b|(?:require|import)\s*\(\s*['"](?:node:)?(?:fs(?:/promises)?|child_process)['"]\s*\))\s*\[""",
            sc_c,
        ):
            return True
        if re.search(r"(?:child_process|fs(?:/promises)?)", sc_c) and (
            re.search(r"\[(?![\s\d]+\])[^\]\n]+\]\s*\(", sc_c)
            or re.search(r"""\brequire\s*\(\s*(?!['"][^'"]+['"]\s*\))""", sc_c)
        ):
            return True
        if lang == "node":
            if re.search(r"""\b(?:execSync|execFileSync|execFile|spawnSync|spawn)\s*\(\s*(?!(?:['"][^'"]*['"])\s*(?:,|\)))""", sc_c):
                return True
            if ("child_process" in sc_c or "cp." in sc_c) and re.search(
                r"""\bexec\s*\(\s*(?!(?:['"][^'"]*['"])\s*(?:,|\)))""", sc_c
            ):
                return True
    if lang in ("", "perl"):
        if re.search(r"\b(?:unlink|rmdir|rmtree|remove_tree)\b", sc_c):
            return True
        if re.search(r"""\bsystem\s*\(\s*(?!(?:'[^']*'|"(?:[^"\\$@]|\\.)*")\s*(?:,|\)))""", sc_c):
            return True
        if lang == "perl":
            if re.search(r"""\bsystem\s+(?:(?![("'])|"[^"]*[$@])""", sc_c):
                return True
            if re.search(r"""\bexec\s*\(\s*(?!(?:'[^']*'|"(?:[^"\\$@]|\\.)*")\s*(?:,|\)))""", sc_c):
                return True
            if re.search(r"""\bexec\s+(?:(?![("'])|"[^"]*[$@])""", sc_c):
                return True
            if re.search(r"""\bqx\b[^\n]*[$@]|`[^`\n]*[$@][^`\n]*`|\bopen\s*\([^)]*\|""", sc_c):
                return True
    if lang in ("", "ruby"):
        if re.search(
            r"\b(?:FileUtils\.(?:rm|rm_r|rm_rf|remove_dir|remove_entry|safe_unlink)|File\.(?:delete|unlink)|Dir\.(?:rmdir|delete|unlink))\b",
            sc_c,
        ):
            return True
        if re.search(r"\bPathname\b", sc_c) and re.search(r"\.(?:unlink|delete|rmtree|rmdir)\b", sc_c):
            return True
        if re.search(r"""\bsystem\s*\(\s*(?!(?:'[^']*'|"(?:[^"\\#]|#(?!\{)|\\.)*")\s*(?:,|\)))""", sc_c):
            return True
        if lang == "ruby":
            if re.search(r"\b(?:FileUtils|File|Dir|Pathname|Kernel|IO|Process|Open3)\b", sc_c) and re.search(
                r"\.(?:public_send|__send__|send)\b", sc_c
            ):
                return True
            if re.search(
                r"\.(?:public_send|__send__|send)\s*(?:\(\s*|\s+):?(?:rm|rm_r|rm_rf|remove_dir|remove_entry|safe_unlink|delete|unlink|rmdir|rmtree|system|exec|spawn|popen)\b",
                sc_c,
            ):
                return True
            if re.search(
                r"""\b(?:Process\.spawn|IO\.popen|Open3\.(?:capture\w*|popen\w*)|exec)\s*\(\s*(?!(?:'[^']*'|"(?:[^"\\#]|#(?!\{)|\\.)*")\s*(?:,|\)))""",
                sc_c,
            ):
                return True
            if re.search(r"""\b(?:Process\.spawn|IO\.popen|system|exec)\s+(?:(?![("'])|"[^"]*#\{)""", sc_c):
                return True
            if re.search(r"""`[^`\n]*#\{[^`\n]*`|%x\s*[^\w\s][^\n]*#\{""", sc_c):
                return True
    return False


def _cmd_generator_outputs_rm(sub_cmd: str, _depth: int, target_lang: str = "sh") -> bool:
    """Return True if a command substitution / generator fragment emits rm or obfuscated code into a shell/interpreter."""
    if _depth > 3 or not sub_cmd:
        return True
    if target_lang in ("node", "perl", "ruby", "osascript"):
        if _script_stmt_has_native_del(sub_cmd, target_lang):
            return True
    elif contains_rm_command(sub_cmd, _depth + 1) or RM_WORD_RE.search(sub_cmd):
        return True
    sub_stmts = _split_top_level_statements(sub_cmd) or []
    emitted_parts: list[str] = []
    for ss in sub_stmts:
        stoks = _split_tokens_with_quote_info(ss["text"])
        if not stoks:
            continue
        s_kept, _ = _strip_all_redirs_collect_targets(stoks)
        s_kept = _strip_group_wrappers(s_kept)
        s_strs = _strip_leading_shell_tokens([t.value for t in s_kept])
        ub, uargs = _unwrap_runner_tokens(s_strs)
        if ub in ("echo", "printf", "print"):
            emitted = _reconstruct_echo_printf_text(ub, uargs)
            if emitted:
                emitted_parts.append(emitted)
                if target_lang in ("node", "perl", "ruby", "osascript"):
                    if _script_stmt_has_native_del(emitted, target_lang):
                        return True
                elif re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", target_lang):
                    if RM_WORD_RE.search(emitted):
                        return True
                    if _depth < 2:
                        py_rm, py_cmds, _, _ = _inspect_python_code(emitted, _depth=_depth + 1)
                        if py_rm or any(contains_rm_command(pc, _depth + 1) for pc in py_cmds):
                            return True
                    elif _contains_obfuscated_rm_in_script(emitted):
                        return True
                elif contains_rm_command(emitted, _depth + 1) or RM_WORD_RE.search(emitted):
                    return True
        elif (ub == "base64" and any(a in ("-d", "-D", "--decode") for a in uargs)) or (ub == "xxd" and "-r" in uargs) or ub == "rev":
            return True
        elif re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", ub):
            found_c = _scan_python_cli_c_flag(uargs, lambda t: t)
            if found_c is not None:
                c_idx, c_pos = found_c
                py_src = uargs[c_idx][c_pos + 1 :] if c_pos >= 0 else uargs[c_idx]
                if _depth < 2:
                    py_rm, py_cmds, _, _ = _inspect_python_code(py_src, _depth=_depth + 1, capture_stdout=True)
                    if py_rm or any(contains_rm_command(pc, _depth + 1) for pc in py_cmds):
                        return True
                elif _contains_obfuscated_rm_in_script(py_src):
                    return True
            else:
                for hs in _extract_herestring_texts(uargs):
                    if _depth < 2:
                        py_rm, py_cmds, _, _ = _inspect_python_code(hs, _depth=_depth + 1, capture_stdout=True)
                        if py_rm or any(contains_rm_command(pc, _depth + 1) for pc in py_cmds):
                            return True
        elif ub in ("awk", "gawk", "nawk", "mawk"):
            sc_c = _collapse_script_strings(ss["text"])
            if _contains_obfuscated_rm_in_script(ss["text"]) or RM_WORD_RE.search(sc_c):
                return True
            if "%c" in ss["text"] or re.search(r"\bprintf?\b", ss["text"]):
                return True
        elif ub in ("node", "perl", "ruby", "osascript"):
            if _script_stmt_has_native_del(ss["text"], ub if ub == "osascript" else ""):
                return True
    if len(emitted_parts) > 1:
        combined_em = "".join(emitted_parts)
        if target_lang in ("node", "perl", "ruby", "osascript"):
            if _script_stmt_has_native_del(combined_em, target_lang):
                return True
        elif re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", target_lang):
            if RM_WORD_RE.search(combined_em):
                return True
            if _depth < 2:
                py_rm, py_cmds, _, _ = _inspect_python_code(combined_em, _depth=_depth + 1)
                if py_rm or any(contains_rm_command(pc, _depth + 1) for pc in py_cmds):
                    return True
            elif _contains_obfuscated_rm_in_script(combined_em):
                return True
        elif contains_rm_command(combined_em, _depth + 1) or RM_WORD_RE.search(combined_em):
            return True
    return False


def _pipeline_stdin_contains_rm(cmd_norm: str, _depth: int) -> bool:
    """Detect rm commands fed via echo/printf/<<</<(...)/base64 into downstream shell/eval/xargs/interpreter consumers."""
    for _, _, stages in _split_line_pipelines_with_spans(cmd_norm):
        if len(stages) < 2:
            continue
        parsed: list[tuple[str, list[str], list[str], set[str], str]] = []
        for _, _, s_txt in stages:
            c_bases, _, _ = _extract_stage_consumer_bases_and_targets(s_txt)
            toks = _split_tokens_with_quote_info(s_txt)
            if not toks:
                parsed.append(("", [], [], c_bases, s_txt))
                continue
            kept, _ = _strip_all_redirs_collect_targets(toks)
            kept = _strip_group_wrappers(kept)
            raw_strs = _strip_leading_shell_tokens([t.value for t in kept])
            base, args = _unwrap_runner_tokens(raw_strs)
            tok_vals = [t.value for t in toks]
            parsed.append((base, args, tok_vals, c_bases, s_txt))
        for j in range(1, len(parsed)):
            d_base, d_args, d_raw, d_bases, _ = parsed[j]
            is_shell_consumer = any(b in SHELL_PROGS or b in ("eval", "source", ".") for b in d_bases)
            is_python_consumer = any(re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", b) for b in d_bases) and _interp_reads_code_from_stdin(d_base, d_args)
            is_script_consumer = any(b in ("node", "perl", "ruby", "osascript") for b in d_bases) and _interp_reads_code_from_stdin(d_base, d_args)
            is_xargs_consumer = bool(d_raw and os.path.basename(d_raw[0].lstrip("=$")).lower() == "xargs")
            if not (is_shell_consumer or is_python_consumer or is_script_consumer or is_xargs_consumer):
                continue
            target_lang = "osascript" if "osascript" in d_bases else (d_base if is_script_consumer else ("python3" if is_python_consumer else "sh"))
            for i in range(j):
                u_base, u_args, u_toks, _, u_txt = parsed[i]
                if (u_base == "base64" and any(a in ("-d", "-D", "--decode") for a in u_args)) or (u_base == "xxd" and "-r" in u_args) or u_base == "rev":
                    return True
                if u_base and (
                    re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", u_base)
                    or u_base in ("awk", "gawk", "nawk", "mawk", "node", "perl", "ruby", "osascript")
                ):
                    if _cmd_generator_outputs_rm(u_txt, _depth + 1, target_lang):
                        return True
                candidates: list[str] = []
                if "<(" in u_txt or "$(" in u_txt:
                    up_subs, _ = _extract_cmd_substitutions(u_txt)
                    if any(_cmd_generator_outputs_rm(sc, _depth + 1, target_lang) for sc in up_subs):
                        return True
                    if len(up_subs) > 1:
                        sub_emits: list[str] = []
                        for sc in up_subs:
                            for ss in _split_top_level_statements(sc) or []:
                                sst = _split_tokens_with_quote_info(ss["text"])
                                if not sst:
                                    continue
                                sk, _ = _strip_all_redirs_collect_targets(sst)
                                sk = _strip_group_wrappers(sk)
                                s_str = _strip_leading_shell_tokens([t.value for t in sk])
                                s_b, s_a = _unwrap_runner_tokens(s_str)
                                if s_b in ("echo", "printf", "print"):
                                    em = _reconstruct_echo_printf_text(s_b, s_a)
                                    if em:
                                        sub_emits.append(em)
                        if sub_emits:
                            candidates.append("".join(sub_emits))
                            candidates.append(" ".join(sub_emits))
                inner_txt = _unwrap_compound_stage_delims(u_txt)
                is_compound_u = inner_txt != u_txt.strip() or ";" in inner_txt or "\n" in inner_txt
                if is_compound_u:
                    # Handle compound stages like `{ echo -n r; echo 'm -rf /'; }`
                    em_pieces: list[str] = []
                    for sub_st in _split_top_level_statements(inner_txt) or []:
                        stoks = _split_tokens_with_quote_info(sub_st["text"])
                        if not stoks:
                            continue
                        s_kept, _ = _strip_all_redirs_collect_targets(stoks)
                        s_strs = _strip_leading_shell_tokens([t.value for t in s_kept])
                        sb_u, sa_u = _unwrap_runner_tokens(s_strs)
                        if sb_u in ("echo", "printf", "print"):
                            em = _reconstruct_echo_printf_text(sb_u, sa_u)
                            if em:
                                em_pieces.append(em)
                                candidates.append(em)
                        elif sb_u and contains_rm_command(sub_st["text"], _depth + 1):
                            return True
                    if len(em_pieces) > 1:
                        candidates.append("".join(em_pieces))
                elif u_base in ("echo", "printf", "print"):
                    candidates.append(_reconstruct_echo_printf_text(u_base, u_args))
                for k, tok in enumerate(u_toks):
                    m_hs = re.match(r"^\d*<<<(.*)$", tok)
                    if m_hs:
                        if m_hs.group(1):
                            candidates.append(m_hs.group(1))
                        elif k + 1 < len(u_toks):
                            candidates.append(u_toks[k + 1])
                for cand in candidates:
                    if not cand:
                        continue
                    if is_shell_consumer and (
                        contains_rm_command(cand, _depth + 1) or RM_WORD_RE.search(cand)
                    ):
                        return True
                    if is_python_consumer:
                        if RM_WORD_RE.search(cand):
                            return True
                        if _depth < 2:
                            py_rm, py_cmds, _, _ = _inspect_python_code(cand, _depth=_depth + 1)
                            if py_rm or any(contains_rm_command(pc, _depth + 1) for pc in py_cmds):
                                return True
                        elif _contains_obfuscated_rm_in_script(cand):
                            return True
                    if is_script_consumer and _script_stmt_has_native_del(cand, target_lang):
                        return True
                    if is_xargs_consumer and (
                        _is_rm_cmd_word(cand.strip()) or contains_rm_command(cand, _depth + 1)
                    ):
                        rem_x = _skip_xargs_opts(d_raw[1:]) if d_raw else []
                        if not rem_x or any("{}" in t for t in rem_x) or d_base in SHELL_PROGS:
                            return True
    return False


def _proc_subst_exec_contains_rm(cmd_norm: str, _depth: int) -> bool:
    """Detect process substitutions <(...) feeding SHELL_PROGS/source/./interpreters dynamically."""
    if _depth > 3 or ("<(" not in cmd_norm and ">(" not in cmd_norm and "$(" not in cmd_norm):
        return False
    stmts = _split_top_level_statements(cmd_norm)
    if not stmts:
        return False
    for st in stmts:
        txt = st["text"]
        if "<(" not in txt and "$(" not in txt:
            continue
        toks = _split_tokens_with_quote_info(txt)
        if not toks:
            continue
        kept, _ = _strip_all_redirs_collect_targets(toks)
        kept = _strip_group_wrappers(kept)
        str_toks = _strip_leading_shell_tokens([t.value for t in kept])
        base, _ = _unwrap_runner_tokens(str_toks, unwrap_pkg=False)
        is_py_t = bool(base and re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", base))
        is_sc_t = base in ("node", "perl", "ruby", "osascript")
        if base not in SHELL_PROGS and base not in ("source", ".", "eval") and not is_py_t and not is_sc_t:
            continue
        target_lang = "osascript" if base == "osascript" else (base if (is_py_t or is_sc_t) else "sh")
        subs, _ = _extract_cmd_substitutions(txt)
        for sub_cmd in subs:
            if _cmd_generator_outputs_rm(sub_cmd, _depth + 1, target_lang):
                return True
    return False


def _strip_bash_locale_quotes(command: str) -> str:
    """Strip Bash $\"...\" locale translation prefix outside single quotes/escapes cleanly."""
    out: list[str] = []
    n = len(command)
    i = 0
    quote: str | None = None
    escaped = False
    while i < n:
        ch = command[i]
        if escaped:
            out.append(ch)
            escaped = False
            i += 1
            continue
        if ch == "\\" and quote != "'":
            out.append(ch)
            escaped = True
            i += 1
            continue
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == "'" :
            quote = "'"
            out.append(ch)
            i += 1
            continue
        if ch == "$" and i + 1 < n and command[i + 1] == '"':
            quote = '"'
            out.append('"')
            i += 2
            continue
        if ch == '"':
            quote = '"'
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _var_subst_shell_exec_contains_rm(cmd_norm: str, _depth: int) -> bool:
    """Detect `a=r; b=m; sh -c "$a$b"`, `$a$b -rf /`, and `x=$(generator); sh -c "$x"` / `$x` patterns."""
    if _depth > 3 or "$" not in cmd_norm:
        return False
    stmts = _split_top_level_statements(cmd_norm)
    if not stmts:
        return False
    var_subs: dict[str, list[str]] = {}
    var_texts: dict[str, str] = {}

    def _expand_vt(s: str) -> str:
        if "$" not in s or not var_texts:
            return s
        return re.sub(
            r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\})",
            lambda m: var_texts.get(m.group(1) or m.group(2), m.group(0)),
            s,
        )

    for st in stmts:
        toks = _split_tokens_with_quote_info(st["text"])
        if not toks:
            continue
        non_assign_idx = 0
        while non_assign_idx < len(toks):
            tk = toks[non_assign_idx]
            m_eq = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", tk.raw, re.S)
            if not m_eq:
                break
            vn = m_eq.group(1)
            rhs_raw = m_eq.group(2)
            eq_v_idx = tk.value.find("=")
            rhs_val = tk.value[eq_v_idx + 1 :] if eq_v_idx >= 0 else ""
            if "$(" in rhs_raw or "`" in rhs_raw:
                rhs_subs, _ = _extract_cmd_substitutions(rhs_raw)
                var_subs.setdefault(vn, []).extend(rhs_subs)
                var_texts.pop(vn, None)
            else:
                # Propagate taint if rhs references an existing generator var
                refs_sub = [
                    k for k in var_subs
                    if re.search(rf"\$(?:\{{{re.escape(k)}\}}|{re.escape(k)}\b)", rhs_raw)
                ]
                if refs_sub:
                    for rk in refs_sub:
                        var_subs.setdefault(vn, []).extend(var_subs[rk])
                    var_texts.pop(vn, None)
                else:
                    var_texts[vn] = _expand_vt(rhs_val)
                    var_subs.pop(vn, None)
            non_assign_idx += 1
        rem_toks = toks[non_assign_idx:]
        if not rem_toks or (not var_subs and not var_texts):
            continue
        kept, _ = _strip_all_redirs_collect_targets(rem_toks)
        kept = _strip_group_wrappers(kept)
        if not kept:
            continue
        # Strip leading shell control-flow words (`do`, `then`, `else`, `time`, `!`) preserving ShellToken
        k_start = 0
        while k_start < len(kept) and not kept[k_start].had_quote_or_escape and kept[k_start].value.lower() in (
            SHELL_PREFIX_KEYWORDS | frozenset({"!", "time", "exec"})
        ):
            k_start += 1
        cmd_toks = kept[k_start:]
        if not cmd_toks:
            continue
        first_tok = cmd_toks[0]
        if "$" in first_tok.raw and not first_tok.raw.startswith("'"):
            for vn, rhs_subs in var_subs.items():
                if re.search(rf"\$(?:\{{{re.escape(vn)}\}}|{re.escape(vn)}\b)", first_tok.value):
                    if any(_cmd_generator_outputs_rm(rs, _depth + 1, "sh") for rs in rhs_subs):
                        return True
            exp_first = _expand_vt(first_tok.value)
            if exp_first != first_tok.value:
                exp_args = [_expand_vt(t.value) for t in cmd_toks[1:]]
                rebuilt = " ".join([exp_first] + exp_args)
                if contains_rm_command(rebuilt, _depth + 1) or RM_WORD_RE.search(rebuilt):
                    return True
        str_toks = _strip_leading_shell_tokens([t.value for t in kept])
        base, args = _unwrap_runner_tokens(str_toks, unwrap_pkg=False)
        exec_inputs: list[str] = []
        if base in SHELL_PROGS:
            exec_inputs = _extract_shell_exec_inputs(args)
        elif base == "eval":
            ev = [a for a in args if a != "--"]
            if ev:
                exec_inputs = [" ".join(ev)]
        for sc in exec_inputs:
            for vn, rhs_subs in var_subs.items():
                if re.search(rf"\$(?:\{{{re.escape(vn)}\}}|{re.escape(vn)}\b)", sc):
                    if any(_cmd_generator_outputs_rm(rs, _depth + 1, "sh") for rs in rhs_subs):
                        return True
            exp_sc = _expand_vt(sc)
            if exp_sc != sc and (contains_rm_command(exp_sc, _depth + 1) or RM_WORD_RE.search(exp_sc)):
                return True
    return False


def contains_rm_command(command: str, _depth: int = 0) -> bool:
    """Detect any direct or indirect rm invocation without false-positiving on search globs or $(mktemp)."""
    if _depth > 4:
        return True
    cmd_norm = _strip_shell_line_continuations(command)
    if "$'" in cmd_norm or '$"' in cmd_norm:
        if RM_WORD_RE.search(cmd_norm):
            return True
    if '$"' in cmd_norm:
        stripped_locale = _strip_bash_locale_quotes(cmd_norm)
        if stripped_locale != cmd_norm:
            if RM_WORD_RE.search(stripped_locale) or contains_rm_command(stripped_locale, _depth + 1):
                return True
    subs, has_unclosed = _extract_cmd_substitutions(cmd_norm)
    if has_unclosed and RM_WORD_RE.search(cmd_norm):
        return True
    for sub_cmd in subs:
        if contains_rm_command(sub_cmd, _depth + 1) or RM_WORD_RE.search(sub_cmd):
            return True
    if _has_cmd_word_subst_obfuscation(cmd_norm):
        return True
    if _pipeline_stdin_contains_rm(cmd_norm, _depth):
        return True
    if _proc_subst_exec_contains_rm(cmd_norm, _depth):
        return True
    if _var_subst_shell_exec_contains_rm(cmd_norm, _depth):
        return True
    if "$'" in cmd_norm:
        decoded_raw = _decode_ansi_c_quotes(cmd_norm, shlex_quote_decoded=False)
        if decoded_raw != cmd_norm:
            if RM_WORD_RE.search(decoded_raw):
                return True
            decoded_quoted = _decode_ansi_c_quotes(cmd_norm, shlex_quote_decoded=True)
            if contains_rm_command(decoded_quoted, _depth + 1):
                return True
    segments = _split_shell_ops(cmd_norm)
    if segments is None:
        return bool(RM_WORD_RE.search(cmd_norm))

    for seg in segments:
        try:
            tokens = shlex.split(_space_unquoted_redirs(seg), posix=True)
        except ValueError:
            if RM_WORD_RE.search(seg):
                return True
            continue
        if _tokens_contain_rm(tokens, _depth):
            return True
    return False


def _strip_all_redirs(tokens: list[ShellToken]) -> tuple[list[ShellToken], list[str], list[str]]:
    """Strip redirection tokens while returning (kept_tokens, output_targets, input_targets)."""
    kept: list[ShellToken] = []
    targets: list[str] = []
    inputs: list[str] = []
    idx = 0
    n = len(tokens)
    while idx < n:
        tok = tokens[idx]
        if not tok.had_quote_or_escape and REDIR_ONLY_RE.match(tok.value):
            if idx + 1 < n and not tok.value.endswith("&"):
                if ">" in tok.value:
                    targets.append(tokens[idx + 1].value)
                elif "<" in tok.value and "<<" not in tok.value:
                    inputs.append(tokens[idx + 1].value)
            idx += 2
            continue
        m_inline = re.match(r"^(\d*)(<<<|<<-?|>>?|>&|<&|&>>?|<>|>\|)(.+)$", tok.value)
        if not tok.had_quote_or_escape and m_inline:
            op = m_inline.group(2)
            if ">" in op and not op.endswith("&"):
                targets.append(m_inline.group(3))
            elif "<" in op and "<<" not in op and not op.endswith("&"):
                inputs.append(m_inline.group(3))
            idx += 1
            continue
        kept.append(tok)
        idx += 1
    return kept, targets, inputs


def _strip_all_redirs_collect_targets(tokens: list[ShellToken]) -> tuple[list[ShellToken], list[str]]:
    """Strip redirection tokens from a segment while collecting '>' output target paths."""
    kept, targets, _ = _strip_all_redirs(tokens)
    return kept, targets


def _seg_command_base_and_args(seg: str) -> tuple[str, list[str], list[str]]:
    """Return (unwrapped_base, unwrapped_args, redir_output_targets) for a single pipe stage."""
    toks = _split_tokens_with_quote_info(seg)
    if not toks:
        return "", [], []
    kept, targets = _strip_all_redirs_collect_targets(toks)
    kept = _strip_group_wrappers(kept)
    str_toks = _strip_leading_shell_tokens([t.value for t in kept])
    if not str_toks:
        return "", [], targets
    base, args = _unwrap_runner_tokens(str_toks)
    if base == "tee":
        for a in args:
            if not a.startswith("-"):
                targets.append(a)
    return base, args, targets


def _is_group_brace_open(line: str, i: int) -> bool:
    """Return True if '{' at line[i] opens a POSIX brace group (delimited word, not ${...} or {a,b})."""
    if line[i] != "{":
        return False
    if i > 0 and line[i - 1] not in " \t\n\r;|&(){":
        return False
    nxt = line[i + 1] if i + 1 < len(line) else ""
    return nxt in ("", " ", "\t", "\n", "\r", ";")


def _is_group_brace_close(line: str, i: int) -> bool:
    """Return True if '}' at line[i] closes a POSIX brace group."""
    if line[i] != "}":
        return False
    if i > 0 and line[i - 1] not in " \t\n\r;|&(){":
        return False
    nxt = line[i + 1] if i + 1 < len(line) else ""
    return nxt in ("", " ", "\t", "\n", "\r", ";", "|", "&", ")", "}")


_PIPE_KW_OPEN = frozenset({"while", "for", "until", "if"})
_PIPE_KW_MID = frozenset({"then", "do", "else", "elif"})
_PIPE_KW_CLOSE = frozenset({"done", "fi"})


def _split_line_pipelines_with_spans(line: str) -> list[tuple[int, int, list[tuple[int, int, str]]]]:
    """Split shell text into top-level pipelines and stages, preserving compound groups and multiline pipes."""
    pipelines: list[tuple[int, int, list[tuple[int, int, str]]]] = []
    cur_stages: list[tuple[int, int, str]] = []
    p_start = 0
    s_start = 0
    n = len(line)
    i = 0
    quote: str | None = None
    escaped = False
    paren_depth = 0
    brace_depth = 0
    kw_depth = 0
    after_pipe = False
    at_cmd_pos = True
    last_code_end = 0

    def flush_stage(end_idx: int) -> None:
        nonlocal s_start
        raw_sl = line[s_start:end_idx]
        txt = raw_sl.strip()
        if txt:
            lpad = len(raw_sl) - len(raw_sl.lstrip())
            cur_stages.append((s_start + lpad, s_start + lpad + len(txt), txt))
        s_start = end_idx

    def flush_pipeline(end_idx: int) -> None:
        nonlocal p_start, cur_stages, after_pipe, at_cmd_pos
        flush_stage(end_idx)
        if cur_stages:
            pipelines.append((p_start, end_idx, cur_stages))
            cur_stages = []
        p_start = end_idx
        after_pipe = False
        at_cmd_pos = True

    while i < n:
        ch = line[i]
        if escaped:
            escaped = False
            after_pipe = False
            last_code_end = i + 1
            i += 1
            continue
        if ch == "\\" and quote != "'":
            if i + 1 < n and line[i + 1] == "\n":
                last_code_end = i + 2
                i += 2
                continue
            escaped = True
            after_pipe = False
            last_code_end = i + 1
            i += 1
            continue
        if quote:
            if ch == quote:
                quote = None
            last_code_end = i + 1
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            after_pipe = False
            at_cmd_pos = False
            last_code_end = i + 1
            i += 1
            continue
        if ch == "#" and (i == 0 or line[i - 1] in " \t\n;|&(){"):
            in_compound = paren_depth > 0 or brace_depth > 0 or kw_depth > 0
            if not in_compound and not after_pipe:
                flush_pipeline(i)
            while i < n and line[i] != "\n":
                i += 1
            if i < n and line[i] == "\n":
                i += 1
            if not in_compound and not after_pipe:
                s_start = i
                p_start = i
                at_cmd_pos = True
            elif after_pipe and not in_compound:
                s_start = i
                at_cmd_pos = True
            continue
        if ch == "$" and i + 1 < n and line[i + 1] == "(":
            sub_end = _scan_cmd_subst_end(line, i)
            if sub_end < 0:
                break
            after_pipe = False
            at_cmd_pos = False
            last_code_end = sub_end
            i = sub_end
            continue
        if line[i : i + 2] in ("<(", ">("):
            sub_end = _scan_cmd_subst_end(line, i)
            if sub_end < 0:
                break
            after_pipe = False
            at_cmd_pos = False
            last_code_end = sub_end
            i = sub_end
            continue
        if ch == "`":
            bt_end = _scan_backtick_end(line, i)
            if bt_end < 0:
                break
            after_pipe = False
            at_cmd_pos = False
            last_code_end = bt_end
            i = bt_end
            continue

        in_compound = paren_depth > 0 or brace_depth > 0 or kw_depth > 0

        if ch in (" ", "\t", "\r"):
            i += 1
            continue
        if ch == "\n":
            if in_compound or after_pipe:
                i += 1
                if after_pipe and not in_compound:
                    s_start = i
                at_cmd_pos = True
                continue
            flush_pipeline(i)
            i += 1
            s_start = i
            p_start = i
            continue
        if ch == "(":
            paren_depth += 1
            after_pipe = False
            at_cmd_pos = True
            last_code_end = i + 1
            i += 1
            continue
        if ch == ")":
            paren_depth = max(0, paren_depth - 1)
            at_cmd_pos = False
            last_code_end = i + 1
            i += 1
            continue
        if _is_group_brace_open(line, i):
            brace_depth += 1
            after_pipe = False
            at_cmd_pos = True
            last_code_end = i + 1
            i += 1
            continue
        if brace_depth > 0 and _is_group_brace_close(line, i):
            brace_depth = max(0, brace_depth - 1)
            at_cmd_pos = False
            last_code_end = i + 1
            i += 1
            continue
        if ch == ";":
            if line[i : i + 2] == ";;":
                if not in_compound:
                    flush_pipeline(i)
                    i += 2
                    s_start = i
                    p_start = i
                    continue
                at_cmd_pos = True
                last_code_end = i + 2
                i += 2
                continue
            if in_compound:
                at_cmd_pos = True
                last_code_end = i + 1
                i += 1
                continue
            flush_pipeline(i)
            i += 1
            s_start = i
            p_start = i
            continue
        if ch == "&":
            prev_ch = line[i - 1] if i > 0 else ""
            next_ch = line[i + 1] if i + 1 < n else ""
            if prev_ch in (">", "<") or next_ch == ">":
                last_code_end = i + 1
                i += 1
                continue
            if in_compound:
                at_cmd_pos = True
                step = 2 if next_ch == "&" else 1
                last_code_end = i + step
                i += step
                continue
            flush_pipeline(i)
            i += 2 if next_ch == "&" else 1
            s_start = i
            p_start = i
            continue
        if ch == "|":
            prev_ch = line[i - 1] if i > 0 else ""
            if prev_ch == ">":
                last_code_end = i + 1
                i += 1
                continue
            next_ch = line[i + 1] if i + 1 < n else ""
            if next_ch == "|":
                if in_compound:
                    at_cmd_pos = True
                    last_code_end = i + 2
                    i += 2
                    continue
                flush_pipeline(i)
                i += 2
                s_start = i
                p_start = i
                continue
            if in_compound:
                at_cmd_pos = True
                step = 2 if next_ch == "&" else 1
                last_code_end = i + step
                i += step
                continue
            flush_stage(i)
            i += 2 if next_ch == "&" else 1
            s_start = i
            after_pipe = True
            at_cmd_pos = True
            last_code_end = i
            continue

        # Read word boundary cleanly for POSIX command-position keywords (while/for/until/if/done/fi)
        if ch.isalpha() or ch in ("_", "!"):
            w_start = i
            while i < n and (line[i].isalnum() or line[i] in ("_", "-", ".", "/", "!")):
                i += 1
            word = line[w_start:i]
            after_pipe = False
            last_code_end = i
            if word == "!" and at_cmd_pos:
                at_cmd_pos = True
            elif at_cmd_pos and word in _PIPE_KW_OPEN:
                kw_depth += 1
                at_cmd_pos = True
            elif at_cmd_pos and word in _PIPE_KW_MID:
                at_cmd_pos = True
            elif at_cmd_pos and word in _PIPE_KW_CLOSE and kw_depth > 0:
                kw_depth = max(0, kw_depth - 1)
                at_cmd_pos = False
            elif "=" in word:
                # Leading VAR=val doesn't consume command position
                pass
            else:
                at_cmd_pos = False
            continue

        after_pipe = False
        at_cmd_pos = False
        last_code_end = i + 1
        i += 1

    flush_pipeline(last_code_end if after_pipe and last_code_end >= p_start else i)
    return pipelines


def _unwrap_compound_stage_delims(txt: str) -> str:
    """Strip balanced outer (...) or {...} wrappers cleanly from a compound stage string."""
    s = txt.strip()
    for _ in range(4):
        if len(s) >= 2 and s[0] == "(" and s[-1] == ")":
            s = s[1:-1].strip()
        elif len(s) >= 2 and s[0] == "{" and s[-1] == "}":
            s = s[1:-1].strip()
        else:
            break
    return s


def _extract_stage_consumer_bases_and_targets(stage_txt: str, _depth: int = 0) -> tuple[set[str], list[str], list[tuple[str, list[str], str, list[str]]]]:
    """Inspect a possibly compound stage (e.g. (true; sh), { cat; sh; }, while read l; do eval "$l"; done).

    Returns (consumer_bases, redir_and_tee_targets, inner_stage_entries).
    """
    bases: set[str] = set()
    targets: list[str] = []
    entries: list[tuple[str, list[str], str, list[str]]] = []
    if _depth > 4 or not stage_txt.strip():
        return bases, targets, entries

    base, args, tgts = _seg_command_base_and_args(stage_txt)
    if base:
        bases.add(base)
    targets.extend(tgts)

    toks = _split_tokens_with_quote_info(stage_txt)
    if toks:
        kept, _, in_r = _strip_all_redirs(toks)
        kept = _strip_group_wrappers(kept)
        targets_outer = _strip_all_redirs_collect_targets(toks)[1]
        for t in targets_outer:
            if t not in targets:
                targets.append(t)
        str_k = _strip_leading_shell_tokens([t.value for t in kept])
        first_tok = str_k[0] if str_k else ""
        entries.append((base, args, first_tok, in_r))

    if base in ("awk", "gawk", "nawk", "mawk"):
        for a in args:
            if re.search(r"""\bsystem\s*\(|\|\s*getline\b|(?<!\|)\|(?!\|)\s*(?:['"(A-Za-z_$])""", a):
                bases.add("sh")

    # Recurse into inner compound statements when delimiters or loop keywords are present
    stripped = _unwrap_compound_stage_delims(stage_txt)
    if stripped != stage_txt.strip() or any(
        kw in stage_txt for kw in (";", "\n", "|", "&", "while ", "for ", "until ", "if ")
    ):
        stmts = _split_top_level_statements(stripped)
        if stmts and (len(stmts) > 1 or stripped != stage_txt.strip()):
            for st in stmts:
                sub_txt = st["text"].strip()
                if not sub_txt:
                    continue
                # Strip leading shell control-flow words (`do`, `then`, `else`, `elif`, `while`, `for`, `until`, `if`)
                sub_toks = _split_tokens_with_quote_info(sub_txt)
                if sub_toks:
                    k_i = 0
                    while k_i < len(sub_toks) and sub_toks[k_i].value.lower() in (
                        SHELL_PREFIX_KEYWORDS | _PIPE_KW_OPEN | _PIPE_KW_MID | _PIPE_KW_CLOSE
                    ):
                        k_i += 1
                    rem_sub = sub_toks[k_i:]
                    if rem_sub:
                        rem_txt = sub_txt[rem_sub[0].raw_start :]
                        if rem_txt.strip() != stage_txt.strip():
                            sb, st_tgts, sub_ents = _extract_stage_consumer_bases_and_targets(rem_txt, _depth + 1)
                            bases.update(sb)
                            targets.extend(st_tgts)
                            entries.extend(sub_ents)
                if "|" in sub_txt and not sub_txt.strip() == stage_txt.strip():
                    for _, _, sub_stages in _split_line_pipelines_with_spans(sub_txt):
                        if len(sub_stages) > 1:
                            for _, _, ss in sub_stages:
                                sb, st_tgts, sub_ents = _extract_stage_consumer_bases_and_targets(ss, _depth + 1)
                                bases.update(sb)
                                targets.extend(st_tgts)
                                entries.extend(sub_ents)

    return bases, targets, entries


def _find_owner_and_downstream(logical_line: str, op_offset: int) -> tuple[str, list[str], bool]:
    """Locate the stage owning '<<' at op_offset, downstream pipe stages on same line, and whether pipeline ends in '|'."""
    pls = _split_line_pipelines_with_spans(logical_line)
    for p_start, p_end, stages in pls:
        if p_start <= op_offset <= p_end:
            for idx, (s_start, s_end, s_txt) in enumerate(stages):
                if s_start <= op_offset <= s_end:
                    downstream = [s[2] for s in stages[idx + 1 :]]
                    stripped = logical_line[:p_end].rstrip()
                    trailing_pipe = bool(stripped.endswith(("|", "|&")) and not stripped.endswith(("||", ">|")))
                    return s_txt, downstream, trailing_pipe
    return logical_line, [], False


def _extract_heredocs(command: str) -> tuple[str, list[dict]]:
    """Separate POSIX/Bash/Zsh heredoc bodies from shell command text while recording owner segments & pipes."""
    raw_lines = command.splitlines(keepends=True)
    shell_lines: list[str] = []
    heredocs: list[dict] = []
    quote: str | None = None
    idx = 0
    n_lines = len(raw_lines)

    while idx < n_lines:
        logical_parts: list[str] = []
        pending_specs: list[dict] = []

        while idx < n_lines:
            ln = raw_lines[idx]
            base_off = sum(len(p) for p in logical_parts)
            logical_parts.append(ln)
            j = 0
            ln_len = len(ln)
            esc = False
            in_comment = False

            while j < ln_len:
                ch = ln[j]
                if in_comment:
                    j += 1
                    continue
                if esc:
                    esc = False
                    j += 1
                    continue
                if ch == "\\" and quote != "'":
                    esc = True
                    j += 1
                    continue
                if quote == "'":
                    if ch == "'":
                        quote = None
                    j += 1
                    continue
                if quote == '"':
                    if ch == '"':
                        quote = None
                    j += 1
                    continue
                if ch in ("'", '"'):
                    quote = ch
                    j += 1
                    continue
                if ch == "#" and (j == 0 or ln[j - 1] in " \t;|&()\n"):
                    in_comment = True
                    j += 1
                    continue
                if ln[j : j + 3] == "$((" :
                    # Skip arithmetic $(( ... )) so bitwise << is not treated as heredoc
                    k_arith = j + 3
                    depth_a = 2
                    while k_arith < ln_len and depth_a > 0:
                        if ln[k_arith] == "(":
                            depth_a += 1
                        elif ln[k_arith] == ")":
                            depth_a -= 1
                        k_arith += 1
                    j = k_arith
                    continue
                if ln[j : j + 2] == "<<" and (j + 2 >= ln_len or ln[j + 2] != "<"):
                    # Distinguish (( x << 1 )) arithmetic on same line
                    if j + 2 < ln_len:
                        k = j + 2
                        strip_tabs = False
                        if k < ln_len and ln[k] == "-":
                            strip_tabs = True
                            k += 1
                        while k < ln_len and ln[k] in (" ", "\t"):
                            k += 1
                        delim_chars: list[str] = []
                        is_quoted_delim = False
                        dq: str | None = None
                        desc = False
                        saw_delim = False
                        while k < ln_len:
                            dc = ln[k]
                            if desc:
                                delim_chars.append(dc)
                                desc = False
                                saw_delim = True
                                k += 1
                                continue
                            if dc == "\\" and dq != "'":
                                is_quoted_delim = True
                                desc = True
                                saw_delim = True
                                k += 1
                                continue
                            if dq:
                                if dc == dq:
                                    dq = None
                                else:
                                    delim_chars.append(dc)
                                k += 1
                                continue
                            if dc in ("'", '"'):
                                is_quoted_delim = True
                                saw_delim = True
                                dq = dc
                                k += 1
                                continue
                            if dc in (" ", "\t", "\r", "\n", ";", "|", "&", "<", ">", "(", ")"):
                                break
                            delim_chars.append(dc)
                            saw_delim = True
                            k += 1
                        if saw_delim and dq is None and not desc:
                            pending_specs.append({
                                "delim": "".join(delim_chars),
                                "is_quoted_delim": is_quoted_delim,
                                "strip_tabs": strip_tabs,
                                "op_offset": base_off + j,
                            })
                        j = k
                        continue
                j += 1

            # Check if physical line continues via unescaped trailing backslash outside single quote
            ln_no_eol = ln.rstrip("\r\n")
            back_count = 0
            p_b = len(ln_no_eol) - 1
            while p_b >= 0 and ln_no_eol[p_b] == "\\":
                back_count += 1
                p_b -= 1
            has_line_cont = (quote != "'") and (back_count % 2 == 1)
            idx += 1
            if quote is None and not has_line_cont:
                break

        logical_line = "".join(logical_parts)
        shell_lines.append(logical_line)

        if pending_specs:
            created_hds: list[dict] = []
            for spec in pending_specs:
                owner_seg, downstream_pipes, trailing_pipe = _find_owner_and_downstream(
                    logical_line, spec["op_offset"]
                )
                body_lines: list[str] = []
                unterminated = True
                while idx < n_lines:
                    body_ln = raw_lines[idx]
                    idx += 1
                    cand = body_ln.rstrip("\r\n")
                    if spec["strip_tabs"]:
                        cand = cand.lstrip("\t")
                    if cand == spec["delim"]:
                        unterminated = False
                        break
                    body_lines.append(body_ln)
                hd_obj = {
                    "owner_line": logical_line,
                    "owner_seg": owner_seg,
                    "downstream_pipes": list(downstream_pipes),
                    "trailing_pipe": trailing_pipe,
                    "is_quoted_delim": spec["is_quoted_delim"],
                    "body": "".join(body_lines),
                    "unterminated": unterminated,
                }
                created_hds.append(hd_obj)
                heredocs.append(hd_obj)

            # If pipeline ended in trailing '|' (e.g. `cat << 'EOF' |\n...\nEOF\n{\nsh\n}`), collect balanced multiline block
            if any(h["trailing_pipe"] for h in created_hds):
                look = idx
                while look < n_lines:
                    stripped_ln = raw_lines[look].strip()
                    if not stripped_ln or stripped_ln.startswith("#"):
                        look += 1
                        continue
                    break
                if look < n_lines:
                    end_look = min(n_lines, look + 20)
                    collected: list[str] = []
                    k_ln = look
                    bal_p, bal_b, bal_kw = 0, 0, 0
                    while k_ln < end_look:
                        ln_s = raw_lines[k_ln]
                        collected.append(ln_s)
                        for ch_l in ln_s:
                            if ch_l == "(":
                                bal_p += 1
                            elif ch_l == ")":
                                bal_p = max(0, bal_p - 1)
                        words = re.findall(r"(?<![A-Za-z0-9_])(?:\{|\}|while|for|until|if|done|fi)(?![A-Za-z0-9_])", ln_s)
                        for w_l in words:
                            if w_l == "{":
                                bal_b += 1
                            elif w_l == "}":
                                bal_b = max(0, bal_b - 1)
                            elif w_l in _PIPE_KW_OPEN:
                                bal_kw += 1
                            elif w_l in _PIPE_KW_CLOSE:
                                bal_kw = max(0, bal_kw - 1)
                        k_ln += 1
                        if bal_p == 0 and bal_b == 0 and bal_kw == 0:
                            break
                    block_txt = "".join(collected)
                    pls = _split_line_pipelines_with_spans(block_txt)
                    next_pipe_stages = [s[2] for _, _, stages in pls[:1] for s in stages]
                    for h in created_hds:
                        if h["trailing_pipe"]:
                            h["downstream_pipes"].extend(next_pipe_stages)

    return "".join(shell_lines), heredocs


def _extract_heredoc_cmd_substitutions(body: str) -> tuple[list[str], bool]:
    """Extract $(...) and `...` from an unquoted heredoc body (single quotes do NOT suppress expansion here!)."""
    subs: list[str] = []
    i = 0
    n = len(body)
    esc = False
    while i < n:
        ch = body[i]
        if esc:
            esc = False
            i += 1
            continue
        if ch == "\\":
            esc = True
            i += 1
            continue
        if ch == "`":
            bt_end = _scan_backtick_end(body, i)
            if bt_end < 0:
                return subs, True
            subs.append(body[i + 1 : bt_end - 1].replace("\\`", "`"))
            i = bt_end
            continue
        if ch == "$" and i + 1 < n and body[i + 1] == "(":
            sub_end = _scan_cmd_subst_end(body, i)
            if sub_end < 0:
                return subs, True
            subs.append(body[i + 2 : sub_end - 1])
            i = sub_end
            continue
        i += 1
    return subs, False


def _is_safe_scratch_target(scratch_root: str, rel_subpath: str) -> bool:
    """Validate rel_subpath strictly inside Antigravity scratch_root with no symlinks escaping."""
    return is_safe_scratch_target(scratch_root, rel_subpath)


def _is_safe_python_static_rm(
    toks: list[str],
    ws_roots: list[tuple[str, str]] | None = None,
) -> bool:
    """Return True iff argv tokens represent a plain rm targeting only concrete safe /tmp, scratch, or active workspace targets."""
    if len(toks) < 2 or toks[0] not in ("rm", "/bin/rm"):
        return False
    operands: list[str] = []
    end_opts = False
    for tok in toks[1:]:
        if tok == "__DYN__":
            return False
        if not end_opts and tok == "--":
            end_opts = True
            continue
        if not end_opts and tok.startswith("-"):
            if re.fullmatch(r"-[rRfvd]+", tok) or tok in ALLOWED_RM_LONG_OPTS:
                continue
            return False
        operands.append(tok)
    if not operands:
        return False
    for op in operands:
        if _is_safe_tmp_target(op):
            continue
        m_scr = SCRATCH_ROOT_RE.match(op)
        if m_scr and m_scr.group(2) and _is_safe_scratch_target(m_scr.group(1), m_scr.group(2)):
            continue
        if ws_roots and is_safe_workspace_target(op, ws_roots, allow_glob=False):
            continue
        return False
    return True


def _inspect_python_code(
    code: str,
    _depth: int = 0,
    ws_roots: list[tuple[str, str]] | None = None,
    capture_stdout: bool = False,
    external_symlink_active: bool = False,
) -> tuple[bool, list[str], bool, bool]:
    """Analyze Python code with ast to extract real OS/subprocess commands without false-triggering on strings/dicts.

    Returns (has_unsafe_rm, extracted_shell_cmds, fallback_raw, has_symlink_op).
    """
    if _depth > 2:
        return True, [], True, False
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False, [], True, ("symlink" in code)

    aliases: dict[str, str] = {}
    imported_mods: set[str] = set()
    assigned_counts: dict[str, int] = {}
    first_assign_node: dict[str, ast.AST] = {}
    def_names: set[str] = set()
    sys_exec_mutated = False
    list_assign_candidates: dict[str, list[ast.AST]] = {}
    disqualified_list_vars: set[str] = set()
    has_unsafe_rm = False
    extracted_shell_cmds: list[str] = []
    fallback_raw = False
    has_unverified_exec = False
    has_symlink_op = False
    has_native_del_op = False

    def _collect_target_names(tgt: ast.AST) -> list[str]:
        names: list[str] = []
        if isinstance(tgt, ast.Name):
            names.append(tgt.id)
        elif isinstance(tgt, (ast.Tuple, ast.List)):
            for elt in tgt.elts:
                names.extend(_collect_target_names(elt))
        elif isinstance(tgt, ast.Starred):
            names.extend(_collect_target_names(tgt.value))
        elif isinstance(tgt, ast.Subscript):
            cur = tgt.value
            while isinstance(cur, ast.Subscript):
                cur = cur.value
            if isinstance(cur, ast.Name):
                names.append(cur.id)
        return names

    # Collect import aliases and count ALL binding/shadowing/mutation forms for sound constant folding
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            def_names.add(node.name)
            disqualified_list_vars.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for nm in node.names:
                disqualified_list_vars.add(nm)
        elif isinstance(node, ast.Import):
            for al in node.names:
                mod = al.name
                top_mod = mod.split(".")[0]
                imported_mods.add(top_mod)
                as_name = al.asname or top_mod
                aliases[as_name] = top_mod if not al.asname else mod
                disqualified_list_vars.add(as_name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            top_mod = mod.split(".")[0]
            if top_mod:
                imported_mods.add(top_mod)
            for al in node.names:
                as_name = al.asname or al.name
                if al.name == "*":
                    continue
                aliases[as_name] = f"{mod}.{al.name}" if mod else al.name
                disqualified_list_vars.add(as_name)
        elif isinstance(node, ast.arg):
            assigned_counts[node.arg] = assigned_counts.get(node.arg, 0) + 2
            disqualified_list_vars.add(node.arg)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute) and tgt.attr == "executable":
                    sys_exec_mutated = True
                if isinstance(tgt, ast.Name):
                    assigned_counts[tgt.id] = assigned_counts.get(tgt.id, 0) + 1
                    first_assign_node.setdefault(tgt.id, node.value)
                    if len(node.targets) == 1 and isinstance(node.value, (ast.List, ast.Tuple)):
                        list_assign_candidates.setdefault(tgt.id, []).append(node.value)
                    else:
                        disqualified_list_vars.add(tgt.id)
                else:
                    for nm in _collect_target_names(tgt):
                        assigned_counts[nm] = assigned_counts.get(nm, 0) + 2
                        disqualified_list_vars.add(nm)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Attribute) and node.target.attr == "executable":
                sys_exec_mutated = True
            if isinstance(node.target, ast.Name) and node.value is not None:
                assigned_counts[node.target.id] = assigned_counts.get(node.target.id, 0) + 1
                first_assign_node.setdefault(node.target.id, node.value)
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    list_assign_candidates.setdefault(node.target.id, []).append(node.value)
                else:
                    disqualified_list_vars.add(node.target.id)
            else:
                for nm in _collect_target_names(node.target):
                    assigned_counts[nm] = assigned_counts.get(nm, 0) + 2
                    disqualified_list_vars.add(nm)
        elif isinstance(node, ast.NamedExpr):
            assigned_counts[node.target.id] = assigned_counts.get(node.target.id, 0) + 2
            disqualified_list_vars.add(node.target.id)
        elif isinstance(node, ast.AugAssign):
            for nm in _collect_target_names(node.target):
                assigned_counts[nm] = assigned_counts.get(nm, 0) + 2
                disqualified_list_vars.add(nm)
        elif isinstance(node, ast.Delete):
            for tgt in node.targets:
                for nm in _collect_target_names(tgt):
                    assigned_counts[nm] = assigned_counts.get(nm, 0) + 2
                    disqualified_list_vars.add(nm)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            for nm in _collect_target_names(node.target):
                assigned_counts[nm] = assigned_counts.get(nm, 0) + 2
            if (
                isinstance(node, ast.For)
                and isinstance(node.target, ast.Name)
                and isinstance(node.iter, (ast.List, ast.Tuple))
                and node.iter.elts
                and all(isinstance(el, (ast.List, ast.Tuple)) for el in node.iter.elts)
            ):
                list_assign_candidates.setdefault(node.target.id, []).extend(node.iter.elts)
            else:
                for nm in _collect_target_names(node.target):
                    disqualified_list_vars.add(nm)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    for nm in _collect_target_names(item.optional_vars):
                        assigned_counts[nm] = assigned_counts.get(nm, 0) + 2
                        disqualified_list_vars.add(nm)
        elif isinstance(node, ast.ExceptHandler) and isinstance(node.name, str):
            assigned_counts[node.name] = assigned_counts.get(node.name, 0) + 2
            disqualified_list_vars.add(node.name)
        elif hasattr(ast, "MatchAs") and isinstance(node, ast.MatchAs) and isinstance(node.name, str):
            assigned_counts[node.name] = assigned_counts.get(node.name, 0) + 2
            disqualified_list_vars.add(node.name)
        elif hasattr(ast, "MatchStar") and isinstance(node, ast.MatchStar) and isinstance(node.name, str):
            assigned_counts[node.name] = assigned_counts.get(node.name, 0) + 2
            disqualified_list_vars.add(node.name)
        elif hasattr(ast, "MatchMapping") and isinstance(node, ast.MatchMapping) and isinstance(node.rest, str):
            assigned_counts[node.rest] = assigned_counts.get(node.rest, 0) + 2
            disqualified_list_vars.add(node.rest)
        elif isinstance(node, ast.Call):
            fn_id = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else (
                    node.func.attr
                    if (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in ("builtins", "__builtins__"))
                    else ""
                )
            )
            if fn_id in ("setattr", "delattr"):
                a0 = node.args[0] if node.args else None
                a1 = node.args[1] if len(node.args) >= 2 else None
                if (
                    a1 is None
                    or (isinstance(a1, ast.Constant) and a1.value == "executable")
                    or (not isinstance(a1, ast.Constant) and "sys" in imported_mods)
                    or (isinstance(a0, ast.Name) and (a0.id == "sys" or aliases.get(a0.id, "").split(".", 1)[0] == "sys"))
                ):
                    sys_exec_mutated = True
            if isinstance(node.func, ast.Attribute) and node.func.attr in PY_MUTATING_METHODS:
                cur = node.func.value
                while isinstance(cur, ast.Subscript):
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    assigned_counts[cur.id] = assigned_counts.get(cur.id, 0) + 2
                    disqualified_list_vars.add(cur.id)

    _mutable_ast_types = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)
    const_env: dict[str, ast.AST] = {
        k: v for k, v in first_assign_node.items()
        if assigned_counts.get(k, 0) == 1 and not isinstance(v, _mutable_ast_types)
    }

    def eval_str(node: ast.AST, depth: int = 0) -> str | None:
        if depth > 6:
            return None
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return node.value
            if isinstance(node.value, bytes):
                return node.value.decode("utf-8", errors="ignore")
        if isinstance(node, ast.Name) and node.id in const_env:
            return eval_str(const_env[node.id], depth + 1)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            ls = eval_str(node.left, depth + 1)
            rs = eval_str(node.right, depth + 1)
            if ls is not None and rs is not None:
                return ls + rs
            return None
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                elif isinstance(v, ast.FormattedValue) and not v.format_spec and v.conversion in (-1, 115):
                    s = eval_str(v.value, depth + 1)
                    if s is None:
                        return None
                    parts.append(s)
                else:
                    return None
            return "".join(parts)
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "chr"
                and len(node.args) == 1
                and not node.keywords
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, int)
                and not isinstance(node.args[0].value, bool)
                and 0 <= node.args[0].value <= 0x10FFFF
            ):
                return chr(node.args[0].value)
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "join"
                and len(node.args) == 1
                and not node.keywords
            ):
                sep = eval_str(node.func.value, depth + 1)
                seq = eval_argv(node.args[0], depth + 1)
                if sep is not None and seq is not None and "__DYN__" not in seq:
                    return sep.join(seq)
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and "sys" in imported_mods
            and assigned_counts.get("sys", 0) == 0
            and assigned_counts.get(node.value.id, 0) == 0
            and not sys_exec_mutated
        ):
            if resolve_func(node)[0] == "sys.executable":
                return "python3"
        return None

    def collect_lits(node: ast.AST) -> str:
        buf: list[str] = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                buf.append(sub.value)
            else:
                s = eval_str(sub)
                if s:
                    buf.append(s)
        return " ".join(buf)

    def eval_argv(node: ast.AST, depth: int = 0) -> list[str] | None:
        if depth > 6:
            return None
        if isinstance(node, ast.Name) and node.id in const_env:
            return eval_argv(const_env[node.id], depth + 1)
        if isinstance(node, (ast.List, ast.Tuple)):
            out: list[str] = []
            for elt in node.elts:
                if isinstance(elt, ast.Starred):
                    if (
                        isinstance(elt.value, ast.Name)
                        and elt.value.id in safe_list_bindings
                        and len(safe_list_bindings[elt.value.id]) == 1
                    ):
                        sv_list = eval_argv(safe_list_bindings[elt.value.id][0], depth + 1)
                        if sv_list is not None:
                            out.extend(sv_list)
                        else:
                            out.append("__DYN__")
                    else:
                        out.append("__DYN__")
                    continue
                s = eval_str(elt, depth + 1)
                out.append(s if s is not None else "__DYN__")
            return out
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            la = eval_argv(node.left, depth + 1)
            ra = eval_argv(node.right, depth + 1)
            if la is not None and ra is not None:
                return la + ra
            return None
        return None

    def resolve_func(fn: ast.AST, depth: int = 0) -> tuple[str, str, str]:
        if depth > 4:
            return "", "", ""
        if isinstance(fn, ast.Name):
            if fn.id in aliases:
                resolved = aliases[fn.id]
            elif fn.id in const_env and isinstance(const_env[fn.id], (ast.Name, ast.Attribute)):
                return resolve_func(const_env[fn.id], depth + 1)
            else:
                resolved = fn.id
            attr = resolved.rsplit(".", 1)[-1]
            root = resolved.split(".", 1)[0]
            return resolved, attr, root
        if isinstance(fn, ast.Attribute):
            if isinstance(fn.value, ast.Name):
                if fn.value.id in aliases:
                    base = aliases[fn.value.id]
                elif fn.value.id in const_env and isinstance(const_env[fn.value.id], (ast.Name, ast.Attribute)):
                    base, _, _ = resolve_func(const_env[fn.value.id], depth + 1)
                else:
                    base = fn.value.id
                return f"{base}.{fn.attr}", fn.attr, base.split(".")[0]
            if isinstance(fn.value, ast.Attribute) and isinstance(fn.value.value, ast.Name):
                b, _, _ = resolve_func(fn.value, depth + 1)
                return f"{b}.{fn.attr}", fn.attr, b.split(".")[0]
            return fn.attr, fn.attr, ""
        return "", "", ""

    allowed_list_load_ids: set[int] = set()
    _argv_call_attrs = frozenset({"run", "call", "Popen", "check_output", "check_call", "spawn", "create_subprocess_exec"})
    for nd in ast.walk(tree):
        if isinstance(nd, (ast.List, ast.Tuple)):
            for elt in nd.elts:
                if isinstance(elt, ast.Starred) and isinstance(elt.value, ast.Name):
                    allowed_list_load_ids.add(id(elt.value))
        elif isinstance(nd, ast.Call):
            c_full, c_attr, _ = resolve_func(nd.func)
            is_argv_like = (
                c_full in ("subprocess.run", "subprocess.call", "subprocess.check_call",
                           "subprocess.check_output", "subprocess.Popen",
                           "asyncio.create_subprocess_exec", "pty.spawn")
                or c_full.startswith(("os.exec", "os.spawn", "os.posix_spawn", "posix.exec", "posix.spawn"))
                or c_attr in DANGEROUS_PY_ARGV_FUNCS | _argv_call_attrs
                or c_attr in DANGEROUS_PY_OS_EXEC_FUNCS
            )
            if is_argv_like:
                if nd.args and isinstance(nd.args[0], ast.Name):
                    allowed_list_load_ids.add(id(nd.args[0]))
                for kw in nd.keywords:
                    if kw.arg == "args" and isinstance(kw.value, ast.Name):
                        allowed_list_load_ids.add(id(kw.value))
            elif (
                c_full in ("print", "builtins.print")
                and "print" not in def_names
                and assigned_counts.get("print", 0) == 0
            ):
                for pa in nd.args:
                    if isinstance(pa, ast.Name):
                        allowed_list_load_ids.add(id(pa))

    for nd in ast.walk(tree):
        if isinstance(nd, ast.Name) and isinstance(nd.ctx, ast.Load) and nd.id in list_assign_candidates:
            if id(nd) not in allowed_list_load_ids:
                disqualified_list_vars.add(nd.id)

    safe_list_bindings: dict[str, list[ast.AST]] = {
        k: v for k, v in list_assign_candidates.items()
        if v and k not in disqualified_list_vars
    }

    if imported_mods - SAFE_PY_INSPECT_MODS:
        fallback_raw = True

    def check_cmd_tokens(toks: list[str], mark_unverified: bool = True) -> None:
        nonlocal has_unsafe_rm, fallback_raw, has_unverified_exec, has_symlink_op
        if not toks:
            return
        if toks[0] == "__DYN__":
            fallback_raw = True
            if mark_unverified:
                has_unverified_exec = True
        if _tokens_contain_rm(toks, _depth + 1):
            if not _is_safe_python_static_rm(toks, ws_roots=ws_roots):
                has_unsafe_rm = True
        unwrapped_base, rem_args = _unwrap_runner_tokens(toks)
        if unwrapped_base == "__DYN__":
            fallback_raw = True
            if mark_unverified:
                has_unverified_exec = True
        is_script_interp = (
            unwrapped_base in ("node", "perl", "ruby")
            or bool(re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", unwrapped_base))
        )
        if unwrapped_base in SHELL_PROGS or unwrapped_base in (
            "find", "fd", "fdfind", "xargs", "parallel", "eval", "env", "osascript",
        ):
            if "__DYN__" in toks:
                fallback_raw = True
                if mark_unverified:
                    has_unverified_exec = True
        elif is_script_interp and "__DYN__" in toks:
            fallback_raw = True
            if mark_unverified and (not rem_args or rem_args[0] == "__DYN__" or rem_args[0].startswith("-")):
                has_unverified_exec = True
        if re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", unwrapped_base) and _depth < 2:
            found_c = _scan_python_cli_c_flag(rem_args, lambda t: t)
            if found_c is not None:
                c_idx, c_pos = found_c
                nested_code = rem_args[c_idx][c_pos + 1 :] if c_pos >= 0 else rem_args[c_idx]
                if nested_code == "__DYN__":
                    fallback_raw = True
                    if mark_unverified:
                        has_unverified_exec = True
                elif nested_code:
                    sub_rm, sub_cmds, sub_fb, sub_sym = _inspect_python_code(nested_code, _depth + 1, ws_roots=ws_roots)
                    if sub_rm:
                        has_unsafe_rm = True
                    if sub_fb:
                        fallback_raw = True
                    if sub_sym:
                        has_symlink_op = True
                    extracted_shell_cmds.extend(sub_cmds)
        extracted_shell_cmds.append(shlex.join(toks))

    # Track direct Call.func nodes so non-called dangerous function refs trigger fallback
    call_func_ids = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "__builtins__":
            fallback_raw = True
            has_unverified_exec = True
        if isinstance(node, ast.Attribute) and (
            node.attr.startswith("__") and node.attr.endswith("__") and node.attr not in SAFE_PY_DUNDERS
        ):
            fallback_raw = True
            has_unverified_exec = True
            continue
        if isinstance(node, (ast.Attribute, ast.Name)) and resolve_func(node)[0] == "sys.modules":
            fallback_raw = True
            has_unverified_exec = True
        if isinstance(node, (ast.Attribute, ast.Name)) and id(node) not in call_func_ids:
            full, attr, root = resolve_func(node)
            if (
                full in ("os.system", "os.popen", "subprocess.run", "subprocess.Popen", "subprocess.call",
                         "subprocess.check_call", "subprocess.check_output", "asyncio.create_subprocess_shell",
                         "asyncio.create_subprocess_exec", "pty.spawn", "os.symlink", "posix.symlink",
                         "getattr", "builtins.getattr", "setattr", "delattr",
                         "builtins.setattr", "builtins.delattr", "vars", "globals", "locals",
                         "builtins.vars", "builtins.globals", "builtins.locals",
                         "eval", "exec", "compile", "__import__",
                         "builtins.eval", "builtins.exec", "builtins.compile", "builtins.__import__",
                         "importlib.import_module", "pkgutil.resolve_name", "pydoc.locate",
                         "runpy.run_module", "runpy.run_path", "operator.attrgetter", "operator.methodcaller")
                or full.startswith(("os.exec", "os.spawn", "os.posix_spawn", "posix.exec", "posix.spawn"))
                or attr in DANGEROUS_PY_SHELL_FUNCS | DANGEROUS_PY_ARGV_FUNCS | frozenset({"attrgetter", "methodcaller", "resolve_name", "locate", "run_module", "run_path", "rmtree", "unlink", "rmdir", "removedirs"})
                or attr in DANGEROUS_PY_OS_EXEC_FUNCS
                or full in (
                    "shutil.rmtree", "os.remove", "os.unlink", "os.rmdir", "os.removedirs",
                    "posix.remove", "posix.unlink", "posix.rmdir", "posix.removedirs",
                    "nt.remove", "nt.unlink", "nt.rmdir", "nt.removedirs",
                )
                or (attr in ("run", "call") and "subprocess" in imported_mods)
            ):
                fallback_raw = True
                has_unverified_exec = True

        if not isinstance(node, ast.Call):
            continue

        if isinstance(node.func, (ast.Call, ast.Subscript, ast.Lambda)) or any(kw.arg is None for kw in node.keywords):
            fallback_raw = True

        full, attr, root = resolve_func(node.func)

        if full in ("os.symlink", "posix.symlink", "nt.symlink") or attr in ("symlink", "symlink_to"):
            has_symlink_op = True

        if full in ("eval", "exec", "compile", "__import__", "builtins.eval", "builtins.exec",
                    "builtins.compile", "builtins.__import__", "importlib.import_module",
                    "pkgutil.resolve_name", "pydoc.locate", "runpy.run_module", "runpy.run_path",
                    "operator.attrgetter", "operator.methodcaller") or attr in (
            "attrgetter", "methodcaller", "resolve_name", "locate", "run_module", "run_path"
        ):
            fallback_raw = True
            has_unverified_exec = True
            continue

        if full in ("globals", "locals", "builtins.globals", "builtins.locals"):
            fallback_raw = True
            has_unverified_exec = True
            continue

        if full in ("vars", "builtins.vars"):
            fallback_raw = True
            if len(node.args) == 0 or (
                len(node.args) == 1
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in (imported_mods | {"builtins", "__builtins__"})
            ):
                has_unverified_exec = True
            continue

        if full in ("getattr", "builtins.getattr"):
            if len(node.args) >= 2:
                aname = eval_str(node.args[1])
                if aname is not None:
                    if aname in ("symlink", "symlink_to"):
                        has_symlink_op = True
                    if aname in DANGEROUS_PY_ATTR_NAMES or aname in DANGEROUS_PY_OS_EXEC_FUNCS:
                        fallback_raw = True
                        has_unverified_exec = True
                else:
                    fallback_raw = True
                    has_unverified_exec = True
            else:
                fallback_raw = True
                has_unverified_exec = True
            continue

        if full in ("setattr", "delattr", "builtins.setattr", "builtins.delattr"):
            tgt_root = resolve_func(node.args[0])[2] if node.args else ""
            aname = eval_str(node.args[1]) if len(node.args) >= 2 else None
            if (
                tgt_root in (imported_mods | {"sys", "os", "subprocess", "builtins", "__builtins__"})
                or aname == "executable"
                or (aname is None and not isinstance(node.args[0] if node.args else None, ast.Name))
                or (aname is not None and (aname in DANGEROUS_PY_ATTR_NAMES or aname in DANGEROUS_PY_OS_EXEC_FUNCS))
            ):
                sys_exec_mutated = True
                fallback_raw = True
                has_unverified_exec = True
            continue

        if capture_stdout and (
            full in ("print", "builtins.print", "sys.stdout.write", "os.write", "posix.write")
            or (
                attr == "write"
                and isinstance(node.func, ast.Attribute)
                and (
                    resolve_func(node.func.value)[0] == "sys.stdout"
                    or "stdout" in ast.unparse(node.func)
                )
            )
        ):
            write_args = (
                (list(node.args[1:]) + [kw.value for kw in node.keywords if kw.arg in ("data", "str")])
                if full in ("os.write", "posix.write")
                else list(node.args)
            )
            if full in ("os.write", "posix.write") and not write_args:
                fallback_raw = True
                has_unverified_exec = True
            for pa in write_args:
                ps = eval_str(pa)
                if ps is not None:
                    extracted_shell_cmds.append(ps)
                else:
                    lits = collect_lits(pa)
                    if lits:
                        extracted_shell_cmds.append(lits)
                    if RM_WORD_RE.search(lits):
                        has_unsafe_rm = True
                    fallback_raw = True
                    has_unverified_exec = True
            continue

        if (
            full in (
                "shutil.rmtree", "os.remove", "os.unlink", "os.rmdir", "os.removedirs",
                "posix.remove", "posix.unlink", "posix.rmdir", "posix.removedirs",
                "nt.remove", "nt.unlink", "nt.rmdir", "nt.removedirs",
            )
            or attr in ("rmtree", "unlink", "rmdir", "removedirs")
        ):
            has_native_del_op = True
            p_arg = None
            has_dir_fd = any(kw.arg in ("dir_fd", None) for kw in node.keywords)
            if not has_dir_fd:
                if (
                    attr in ("unlink", "rmdir")
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Call)
                    and resolve_func(node.func.value.func)[0] in ("pathlib.Path", "Path")
                ):
                    if len(node.func.value.args) == 1 and not node.func.value.keywords:
                        p_arg = node.func.value.args[0]
                else:
                    p_arg = node.args[0] if node.args else next(
                        (kw.value for kw in node.keywords if kw.arg == "path"), None
                    )
            p_str = eval_str(p_arg) if p_arg is not None else None
            if p_str is None or not _is_safe_python_static_rm(["rm", "-rf", p_str], ws_roots=ws_roots):
                has_unsafe_rm = True
            continue

        is_shell_call = (
            full in ("os.system", "posix.system", "nt.system", "os.popen", "posix.popen",
                     "subprocess.getoutput", "subprocess.getstatusoutput",
                     "asyncio.create_subprocess_shell", "commands.getoutput", "commands.getstatusoutput")
            or attr in DANGEROUS_PY_SHELL_FUNCS
        )
        if is_shell_call:
            arg0 = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg in ("cmd", "command", "args")), None)
            if arg0 is not None:
                cmd_s = eval_str(arg0)
                if cmd_s is not None:
                    extracted_shell_cmds.append(cmd_s)
                else:
                    lits = collect_lits(arg0)
                    if lits:
                        extracted_shell_cmds.append(lits)
                    if RM_WORD_RE.search(lits):
                        has_unsafe_rm = True
                    fallback_raw = True
                    has_unverified_exec = True
            else:
                fallback_raw = True
                has_unverified_exec = True
            continue

        is_argv_call = (
            full in ("subprocess.run", "subprocess.call", "subprocess.check_call",
                     "subprocess.check_output", "subprocess.Popen",
                     "asyncio.create_subprocess_exec", "pty.spawn")
            or full.startswith(("os.exec", "os.spawn", "os.posix_spawn", "posix.exec", "posix.spawn"))
            or attr in DANGEROUS_PY_ARGV_FUNCS
            or attr in DANGEROUS_PY_OS_EXEC_FUNCS
            or (attr in ("run", "call") and ("subprocess" in imported_mods or "sys.modules" in code))
        )
        if is_argv_call:
            shell_kw = next((kw.value for kw in node.keywords if kw.arg == "shell"), None)
            shell_true = shell_kw is not None and not (isinstance(shell_kw, ast.Constant) and shell_kw.value is False)
            exec_kw = next((kw.value for kw in node.keywords if kw.arg == "executable"), None)
            exec_s = eval_str(exec_kw) if exec_kw is not None else None
            if exec_kw is not None and exec_s is None:
                fallback_raw = True
                has_unverified_exec = True

            arg0 = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg in ("args", "cmd", "file")), None)
            if arg0 is None:
                fallback_raw = True
                has_unverified_exec = True
                continue
            if shell_true:
                cmd_s = eval_str(arg0)
                if cmd_s is None:
                    tv = eval_argv(arg0)
                    if tv and tv[0] != "__DYN__":
                        cmd_s = tv[0]
                if cmd_s is not None:
                    extracted_shell_cmds.append(cmd_s)
                else:
                    lits = collect_lits(arg0)
                    if lits:
                        extracted_shell_cmds.append(lits)
                    if RM_WORD_RE.search(lits):
                        has_unsafe_rm = True
                    fallback_raw = True
                    has_unverified_exec = True
                if exec_s and _is_rm_cmd_word(exec_s):
                    has_unsafe_rm = True
                continue

            if full.startswith(("os.exec", "os.spawn", "os.posix_spawn", "posix.exec", "posix.spawn")) or attr in DANGEROUS_PY_OS_EXEC_FUNCS:
                is_spawn = "spawn" in attr and "posix_spawn" not in attr
                start_i = 1 if is_spawn else 0
                path_node = node.args[start_i] if len(node.args) > start_i else next(
                    (kw.value for kw in node.keywords if kw.arg in ("path", "file")), None
                )
                path_s = eval_str(path_node) if path_node is not None else None
                if path_node is not None and path_s is None:
                    fallback_raw = True
                    has_unverified_exec = True
                argv_idx = start_i + 1
                if len(node.args) > argv_idx and isinstance(node.args[argv_idx], (ast.List, ast.Tuple)):
                    toks = eval_argv(node.args[argv_idx])
                elif len(node.args) > argv_idx:
                    toks = [eval_str(a) or "__DYN__" for a in node.args[argv_idx : argv_idx + 8]]
                else:
                    toks = []
                if path_s is not None:
                    eff_toks = [path_s] + (toks[1:] if len(toks) >= 2 else ([] if toks else []))
                    check_cmd_tokens(eff_toks)
                if toks:
                    check_cmd_tokens(toks)
                elif path_s is None:
                    lits = collect_lits(node)
                    if RM_WORD_RE.search(lits):
                        has_unsafe_rm = True
                    fallback_raw = True
                    has_unverified_exec = True
                continue

            if full == "asyncio.create_subprocess_exec" or attr == "create_subprocess_exec":
                prog_node = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == "program"), None)
                rest_nodes = node.args[1:8] if node.args else []
                toks = []
                if prog_node is not None:
                    ps = eval_str(prog_node)
                    toks.append(ps if ps is not None else "__DYN__")
                for a in rest_nodes:
                    sv = eval_str(a)
                    toks.append(sv if sv is not None else "__DYN__")
                if not toks:
                    fallback_raw = True
                    has_unverified_exec = True
                    continue
                check_cmd_tokens(toks)
                continue

            cmd_s = eval_str(arg0)
            if cmd_s is not None:
                if exec_s is not None:
                    check_cmd_tokens([exec_s])
                extracted_shell_cmds.append(cmd_s)
                continue
            toks = eval_argv(arg0)
            if not toks and isinstance(arg0, ast.Name) and arg0.id in safe_list_bindings:
                for lst_node in safe_list_bindings[arg0.id]:
                    t_lst = eval_argv(lst_node)
                    if not t_lst:
                        lits = collect_lits(lst_node)
                        if lits:
                            extracted_shell_cmds.append(lits)
                        if RM_WORD_RE.search(lits):
                            has_unsafe_rm = True
                        fallback_raw = True
                        has_unverified_exec = True
                        continue
                    check_cmd_tokens(t_lst)
                    if exec_s is not None:
                        check_cmd_tokens([exec_s] + t_lst[1:])
                continue
            if not toks:
                lits = collect_lits(arg0)
                if lits:
                    extracted_shell_cmds.append(lits)
                if RM_WORD_RE.search(lits):
                    has_unsafe_rm = True
                fallback_raw = True
                has_unverified_exec = True
                if exec_s and _is_rm_cmd_word(exec_s):
                    has_unsafe_rm = True
                continue
            check_cmd_tokens(toks)
            if exec_s is not None:
                check_cmd_tokens([exec_s] + toks[1:])

    # Unconditionally harvest folded Call arguments whenever fallback_raw is True (incl. dynamic builtins/exec)
    if fallback_raw:
        if "symlink" in code:
            has_symlink_op = True
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            all_vals = list(n.args) + [kw.value for kw in n.keywords]
            for val in all_vals:
                av = eval_argv(val)
                if av and len(av) >= 1 and av[0] != "__DYN__":
                    check_cmd_tokens(av, mark_unverified=False)
                else:
                    sv = eval_str(val)
                    if sv:
                        extracted_shell_cmds.append(sv)
                        if "symlink" in sv:
                            has_symlink_op = True
                        if RM_WORD_RE.search(sv):
                            has_unsafe_rm = True
                        if _depth < 2 and any(
                            k in sv for k in ("import ", "os.", "subprocess", "system(", "exec(", "eval(", "__import__", "popen(")
                        ):
                            sub_rm, sub_cmds, _, sub_sym = _inspect_python_code(sv, _depth + 1, ws_roots=ws_roots)
                            if sub_rm:
                                has_unsafe_rm = True
                            if sub_sym:
                                has_symlink_op = True
                            extracted_shell_cmds.extend(sub_cmds)

    if (has_symlink_op or external_symlink_active) and (
        has_native_del_op or any(contains_rm_command(sc) or RM_WORD_RE.search(sc) for sc in extracted_shell_cmds)
    ):
        has_unsafe_rm = True

    if has_unverified_exec:
        has_unsafe_rm = True

    return has_unsafe_rm, extracted_shell_cmds, fallback_raw, has_symlink_op


def _parse_mktemp_subst(
    raw_val: str,
    created_tmp_dirs: set[str] | None = None,
) -> tuple[str, bool] | None:
    """Validate V=$(mktemp ...) and return (kind, uses_explicit_tmp), or None on unsafe/missing parent."""
    s = raw_val.strip()
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        s = s[1:-1].strip()
    if not (s.startswith("$(") and s.endswith(")")):
        return None
    if _scan_cmd_subst_end(s, 0) != len(s):
        return None
    inner = s[2:-1].strip()
    if not inner or any(c in inner for c in ";|&\n`<>"):
        return None
    try:
        toks = shlex.split(inner, posix=True)
    except ValueError:
        return None
    if not toks or toks[0] not in ("mktemp", "/usr/bin/mktemp"):
        return None
    kind = "mktemp_file"
    idx = 1
    n = len(toks)
    positional: list[str] = []
    saw_t = False
    uses_explicit_tmp = False

    def _is_valid_mktemp_parent(tdir: str) -> bool:
        if tdir in ("/tmp", "/private/tmp"):
            return True
        if not _is_safe_tmp_target(tdir):
            return False
        if os.path.isdir(tdir) and not os.path.islink(tdir):
            return True
        if created_tmp_dirs is not None and tdir in created_tmp_dirs:
            return True
        return False

    while idx < n:
        t = toks[idx]
        if t in ("-d", "--directory"):
            kind = "mktemp_dir"
            idx += 1
            continue
        if t in ("-q", "-u", "--quiet", "--dry-run"):
            idx += 1
            continue
        if t == "-t":
            if idx + 1 >= n:
                return None
            prefix = toks[idx + 1]
            if not prefix or "/" in prefix or ".." in prefix or any(c in FORBIDDEN_TMP_CHARS for c in prefix):
                return None
            saw_t = True
            idx += 2
            continue
        if t in ("-p", "--tmpdir"):
            if idx + 1 >= n:
                return None
            tdir = toks[idx + 1]
            if not _is_valid_mktemp_parent(tdir):
                return None
            uses_explicit_tmp = True
            idx += 2
            continue
        if t.startswith("--tmpdir="):
            tdir = t.split("=", 1)[1]
            if not _is_valid_mktemp_parent(tdir):
                return None
            uses_explicit_tmp = True
            idx += 1
            continue
        if t.startswith("-"):
            # Combined short flags like -dq
            if re.fullmatch(r"-[dqu]+", t):
                if "d" in t:
                    kind = "mktemp_dir"
                idx += 1
                continue
            return None
        positional.append(t)
        idx += 1
    if len(positional) > 1:
        return None
    if len(positional) == 1:
        tpl = positional[0]
        if saw_t:
            if "/" in tpl or ".." in tpl or any(c in FORBIDDEN_TMP_CHARS for c in tpl):
                return None
        else:
            if not (tpl.startswith(("/tmp/", "/private/tmp/", "$TMPDIR/", "${TMPDIR}/"))):
                return None
            norm_tpl = tpl.replace("${TMPDIR}/", "/tmp/").replace("$TMPDIR/", "/tmp/")
            if ".." in norm_tpl.split("/") or any(c in FORBIDDEN_TMP_CHARS for c in norm_tpl):
                return None
            if tpl.startswith(("/tmp/", "/private/tmp/")):
                uses_explicit_tmp = True
                parent = norm_tpl.rsplit("/", 1)[0]
                if not _is_valid_mktemp_parent(parent):
                    return None
            else:
                # Reject nested subdirs under $TMPDIR/ when creating via template
                rel_after_tmp = norm_tpl[len("/tmp/") :]
                if "/" in rel_after_tmp:
                    return None
    return kind, uses_explicit_tmp


def _classify_safe_var_assignment(
    name: str,
    val_tok_raw: str,
    val_str: str,
    env_tmpdir_valid: bool,
    tmpdir_poisoned: bool,
    created_tmp_dirs: set[str] | None = None,
    ws_roots: list[tuple[str, str]] | None = None,
) -> SafeVarInfo | None:
    """Classify a shell variable assignment as a safe temp/scratch/workspace target or return None."""
    if name in ("TMPDIR", "CDPATH", "PATH", "HOME", "IFS", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"):
        return None
    mk = _parse_mktemp_subst(val_tok_raw, created_tmp_dirs)
    if mk is not None:
        mk_kind, uses_explicit_tmp = mk
        if uses_explicit_tmp or env_tmpdir_valid:
            return SafeVarInfo(kind=mk_kind, base_path="", chain_only=False)
        return None
    if "$" in val_tok_raw or "`" in val_tok_raw:
        return None
    if val_str in ("/tmp", "/private/tmp"):
        return SafeVarInfo(kind="tmp_root", base_path=val_str, chain_only=False)
    if _is_safe_tmp_target(val_str):
        return SafeVarInfo(kind="tmp_path", base_path=val_str, chain_only=False)
    m_scr = SCRATCH_ROOT_RE.match(val_str)
    if m_scr:
        s_root, s_rel = m_scr.group(1), m_scr.group(2)
        if not s_rel:
            if not os.path.islink(s_root):
                return SafeVarInfo(kind="scratch_root", base_path=s_root, chain_only=False)
        elif _is_safe_scratch_target(s_root, s_rel):
            return SafeVarInfo(kind="scratch_subpath", base_path=val_str, chain_only=False)
    if ws_roots and val_str.startswith("/") and not any(ch.isspace() for ch in val_str):
        trimmed = val_str.rstrip("/")
        trimmed_cmp = norm_cmp_path(trimmed)
        for raw_norm, real_norm in ws_roots:
            if trimmed_cmp == norm_cmp_path(raw_norm) or trimmed_cmp == norm_cmp_path(real_norm):
                return SafeVarInfo(kind="ws_root", base_path=real_norm, chain_only=False, ws_real=real_norm)
        m_ws = match_workspace_path(val_str, ws_roots)
        if m_ws and validate_workspace_rel_subpath(
            m_ws[0], m_ws[1], allow_glob=False, check_protected_anchors=False
        ):
            clean_rel = strip_leading_dotslash(m_ws[1]).rstrip("/")
            real_base = os.path.normpath(os.path.join(m_ws[0], clean_rel))
            return SafeVarInfo(kind="ws_subpath", base_path=real_base, chain_only=False, ws_real=m_ws[0])
    return None


def _validate_rel_subpath_under_root(
    root_dir: str,
    subpath: str,
    allow_glob: bool = False,
    check_symlinks: bool = True,
) -> bool:
    """Validate rel subpath strictly under root_dir (non-empty, no .., no symlink escape, bounded leaf glob)."""
    return validate_rel_subpath_under_root(
        root_dir, subpath, allow_glob=allow_glob, check_symlinks=check_symlinks
    )


def _is_safe_leaf_glob(
    path_str: str,
    allow_scratch: bool = False,
    env_tmpdir: str = "",
    env_tmpdir_valid: bool = False,
) -> bool:
    """Allow bounded leaf-only globs inside /tmp, /private/tmp, env TMPDIR, or scratch without escaping or matching roots."""
    if "*" not in path_str and "?" not in path_str:
        return False
    last_slash = path_str.rfind("/")
    if last_slash <= 0:
        return False
    parent = path_str[:last_slash]
    leaf = path_str[last_slash + 1 :]
    if not leaf or leaf.startswith(".") or not re.fullmatch(r"[A-Za-z0-9._*?-]+", leaf):
        return False
    if any(c in parent for c in FORBIDDEN_TMP_CHARS) or ".." in parent.split("/"):
        return False
    if parent in ("/tmp", "/private/tmp"):
        return bool(re.match(r"^[A-Za-z0-9_-]{2,}", leaf))
    if parent.startswith(("/tmp/", "/private/tmp/")):
        return _is_safe_tmp_target(parent)
    if env_tmpdir_valid and env_tmpdir and (path_str.startswith(env_tmpdir + "/")):
        return _validate_rel_subpath_under_root(env_tmpdir, path_str[len(env_tmpdir) + 1 :], allow_glob=True)
    if allow_scratch:
        m_scr = SCRATCH_ROOT_RE.match(parent)
        if m_scr:
            s_root, s_rel = m_scr.group(1), m_scr.group(2)
            if not s_rel:
                return (not os.path.islink(s_root)) and bool(re.match(r"^[A-Za-z0-9_-]{2,}", leaf))
            return _is_safe_scratch_target(s_root, s_rel)
    return False


def _is_safe_tmpdir_operand(
    op_val: str,
    expand_0: bool,
    env_tmpdir: str,
    env_tmpdir_valid: bool,
    tmpdir_poisoned: bool,
    env_tmpdir_set: bool = False,
) -> bool:
    """Validate $TMPDIR/<subpath>, ${TMPDIR}/<subpath>, ${TMPDIR:-/tmp}/<subpath>, or literal env TMPDIR subpaths."""
    if tmpdir_poisoned:
        return False
    if env_tmpdir_valid and env_tmpdir and op_val.startswith(env_tmpdir + "/"):
        return _validate_rel_subpath_under_root(env_tmpdir, op_val[len(env_tmpdir) + 1 :], allow_glob=True)
    if not expand_0:
        return False
    m = re.fullmatch(r"(?:\$TMPDIR|\$\{TMPDIR\}|\$\{TMPDIR:-(/(?:private/)?tmp)\})/(.+)", op_val)
    if not m:
        return False
    is_fallback_tmp = op_val.startswith("${TMPDIR:-")
    if is_fallback_tmp:
        if (env_tmpdir_set or env_tmpdir) and not env_tmpdir_valid:
            return False
    elif not env_tmpdir_valid:
        return False
    eff_root = env_tmpdir if env_tmpdir_valid else (m.group(1) or "")
    return _validate_rel_subpath_under_root(
        eff_root,
        m.group(2),
        allow_glob=True,
        check_symlinks=bool(eff_root),
    )


def _has_unquoted_dollar(raw: str) -> bool:
    """Return True if raw contains a '$' parameter/subst expansion outside quotes."""
    quote: str | None = None
    escaped = False
    for ch in raw:
        if escaped:
            escaped = False
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch == "$":
            return True
    return False


def _unwrap_shell_mutator_cmd(toks: list[ShellToken]) -> tuple[str, list[ShellToken]]:
    """Unwrap builtin/command/noglob/exec wrappers (ignoring quoting) and return (cmd_basename_low, remaining)."""
    idx = 0
    n = len(toks)
    while idx < n:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^\]]*\])?\+?=", toks[idx].raw):
            idx += 1
            continue
        b = os.path.basename(toks[idx].value).lower()
        if b in ("builtin", "command", "noglob", "exec"):
            idx += 1
            while idx < n and toks[idx].value.startswith("-") and toks[idx].value != "--":
                if b == "exec" and toks[idx].value == "-a":
                    idx += 2
                else:
                    idx += 1
            if idx < n and toks[idx].value == "--":
                idx += 1
            continue
        break
    if idx >= n:
        return "", []
    return os.path.basename(toks[idx].value).lower(), toks[idx + 1 :]


_SHADOW_SENSITIVE_CMDS = frozenset({
    "mktemp", "cd", "rm", "mkdir", "set", "command", "builtin",
    "pushd", "popd", "ln", "eval", "source", ".", "export", "readonly",
})


def _is_func_def_header_stmt(st: dict, toks: list[ShellToken], next_st: dict | None) -> str | None:
    """Return function name if st is `name()` or `function name()` followed by ')' continuation, else None."""
    if not (st["conn_next"] == "(" and next_st is not None and next_st["conn_prev"] == ")"):
        return None
    nm: str | None = None
    if len(toks) == 1 and not toks[0].had_quote_or_escape:
        nm = toks[0].value
    elif (
        len(toks) == 2
        and not toks[0].had_quote_or_escape
        and toks[0].value.lower() == "function"
        and not toks[1].had_quote_or_escape
    ):
        nm = toks[1].value
    if nm and nm.lower() not in (SHELL_PREFIX_KEYWORDS | frozenset({"!", "time", "exec"})) and re.fullmatch(
        r"[A-Za-z_./:-][A-Za-z0-9_./:-]*", nm
    ):
        return os.path.basename(nm)
    return None


def all_rm_invocations_are_safe(
    shell_cmd: str,
    ws_roots: list[tuple[str, str]] | None = None,
    initial_cwd: str = "",
) -> bool:
    """Return True iff every rm invocation in shell_cmd strictly deletes safe tmp/scratch/workspace paths (fail-closed)."""
    if "\x00" in shell_cmd:
        return False
    norm = _strip_shell_line_continuations(shell_cmd)
    subs, has_unclosed = _extract_cmd_substitutions(norm)
    if has_unclosed:
        return False
    for sub in subs:
        if contains_rm_command(sub) or RM_WORD_RE.search(sub):
            return False

    stmts = _split_top_level_statements(norm)
    if not stmts:
        return False

    # Pass 1: Detect TMPDIR/CDPATH/IFS/eval tampering, shadowed builtins, CWD mutation safety, and poisoned variables
    tmpdir_poisoned = False
    cdpath_poisoned = bool(os.environ.get("CDPATH"))
    ifs_poisoned = False
    shadowed_sensitive = False
    cwd_pristine_unsafe = False
    poisoned_vars: set[str] = set()
    potential_mkdir_dirs: set[str] = set()
    raw_env_tmpdir = os.environ.get("TMPDIR", "")
    env_tmpdir_set = bool(raw_env_tmpdir)
    env_tmpdir = raw_env_tmpdir.rstrip("/")

    def mark_var_poison_scan() -> None:
        nonlocal tmpdir_poisoned, cdpath_poisoned, ifs_poisoned, shadowed_sensitive, cwd_pristine_unsafe
        p_if, p_loop, p_case, p_brace = 0, 0, 0, 0
        prev_was_func_header = False
        n_stmts = len(stmts)
        for s_idx, st in enumerate(stmts):
            next_st = stmts[s_idx + 1] if s_idx + 1 < n_stmts else None
            toks = _split_tokens_with_quote_info(st["text"])
            if toks is None:
                cwd_pristine_unsafe = True
                return
            t_idx = 0
            while t_idx < len(toks):
                tk_v = toks[t_idx].value.lower()
                if toks[t_idx].had_quote_or_escape or tk_v not in SHELL_PREFIX_KEYWORDS:
                    break
                if tk_v == "if":
                    p_if += 1
                elif tk_v == "fi":
                    p_if = max(0, p_if - 1)
                elif tk_v in ("for", "while", "until", "select"):
                    p_loop += 1
                elif tk_v == "done":
                    p_loop = max(0, p_loop - 1)
                elif tk_v == "case":
                    p_case += 1
                elif tk_v == "esac":
                    p_case = max(0, p_case - 1)
                elif tk_v == "{":
                    p_brace += 1
                elif tk_v == "}":
                    p_brace = max(0, p_brace - 1)
                t_idx += 1

            if t_idx < len(toks) and not toks[t_idx].had_quote_or_escape and toks[t_idx].value.lower() in ("for", "select"):
                p_loop += 1

            stmt_brace_open = 0
            stmt_brace_close = 0
            for k_i in range(t_idx, len(toks)):
                tk = toks[k_i]
                if not tk.had_quote_or_escape:
                    if tk.value == "{":
                        p_brace += 1
                        stmt_brace_open += 1
                    elif tk.value == "}":
                        stmt_brace_close += 1

            func_hdr_name = _is_func_def_header_stmt(st, toks, next_st)
            if func_hdr_name:
                cwd_pristine_unsafe = True
                if func_hdr_name in _SHADOW_SENSITIVE_CMDS:
                    shadowed_sensitive = True

            is_zsh_func_body = prev_was_func_header and st["conn_prev"] == ")"
            if is_zsh_func_body:
                cwd_pristine_unsafe = True
            in_branch = (
                p_if > 0
                or p_loop > 0
                or p_case > 0
                or p_brace > 0
                or st["paren_depth"] > 0
                or st["conn_prev"] in ("||", "|", "|&")
                or st["conn_next"] in ("|", "|&", "&")
                or is_zsh_func_body
            )

            for tk in toks:
                m_arr = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\[[^\]]*\]\+?=", tk.raw)
                if m_arr:
                    vn = m_arr.group(1)
                    poisoned_vars.add(vn)
                    if vn == "TMPDIR":
                        tmpdir_poisoned = True
                    elif vn == "CDPATH":
                        cdpath_poisoned = True
                    elif vn == "IFS":
                        ifs_poisoned = True
                    continue
                m_plus = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\+=", tk.raw)
                if m_plus:
                    vn = m_plus.group(1)
                    poisoned_vars.add(vn)
                    if vn == "TMPDIR":
                        tmpdir_poisoned = True
                    elif vn == "CDPATH":
                        cdpath_poisoned = True
                    elif vn == "IFS":
                        ifs_poisoned = True
                    continue
                m_eq = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", tk.raw)
                if m_eq:
                    vn = m_eq.group(1)
                    if vn == "TMPDIR":
                        tmpdir_poisoned = True
                    elif vn == "CDPATH":
                        cdpath_poisoned = True
                    elif vn == "IFS":
                        ifs_poisoned = True
                    if in_branch:
                        poisoned_vars.add(vn)

            # Unwrap builtin/command/noglob/exec to inspect mutating builtins & array ops
            s_toks = toks[t_idx:]
            if s_toks:
                cmd0, cmd_args = _unwrap_shell_mutator_cmd(s_toks)
                if cmd0 == "function":
                    cwd_pristine_unsafe = True
                    if cmd_args:
                        fname = os.path.basename(cmd_args[0].value)
                        if fname in _SHADOW_SENSITIVE_CMDS:
                            shadowed_sensitive = True
                elif cmd0 in ("trap", "setopt", "unsetopt", "pushd", "popd", "chdir"):
                    cwd_pristine_unsafe = True
                elif cmd0 == "cd" and in_branch:
                    cwd_pristine_unsafe = True
                elif cmd0 == "alias":
                    for a in cmd_args:
                        if a.value.startswith("-"):
                            continue
                        aname = a.value.split("=", 1)[0]
                        if os.path.basename(aname) in _SHADOW_SENSITIVE_CMDS:
                            shadowed_sensitive = True
                elif cmd0 in ("eval", "source", "."):
                    tmpdir_poisoned = True
                    cdpath_poisoned = True
                    ifs_poisoned = True
                    cwd_pristine_unsafe = True
                elif cmd0 == "env":
                    for a in cmd_args:
                        if a.value == "-i" or a.value.startswith("--ignore-environment"):
                            tmpdir_poisoned = True
                        m_eq = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", a.raw)
                        if m_eq:
                            if m_eq.group(1) == "TMPDIR":
                                tmpdir_poisoned = True
                            elif m_eq.group(1) == "CDPATH":
                                cdpath_poisoned = True
                            elif m_eq.group(1) == "IFS":
                                ifs_poisoned = True
                elif cmd0 in ("unset", "unsetenv", "read", "for", "select", "vared", "mapfile", "readarray"):
                    for a in cmd_args:
                        if a.value in ("in", "--") or a.value.startswith("-"):
                            if a.value == "in":
                                break
                            continue
                        m_vn = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", a.value)
                        if m_vn:
                            vn = m_vn.group(1)
                            poisoned_vars.add(vn)
                            if vn == "TMPDIR":
                                tmpdir_poisoned = True
                            elif vn == "CDPATH":
                                cdpath_poisoned = True
                            elif vn == "IFS":
                                ifs_poisoned = True
                elif cmd0 in ("export", "local", "readonly", "declare", "typeset"):
                    has_unsafe_decl_flag = any(
                        a.value.startswith("-") and any(c in a.value[1:] for c in ("n", "a", "A"))
                        for a in cmd_args
                    )
                    for a in cmd_args:
                        if a.value.startswith("-"):
                            continue
                        m_arr = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\[[^\]]*\]\+?=", a.raw)
                        if m_arr:
                            vn = m_arr.group(1)
                            poisoned_vars.add(vn)
                            if vn == "TMPDIR":
                                tmpdir_poisoned = True
                            elif vn == "CDPATH":
                                cdpath_poisoned = True
                            elif vn == "IFS":
                                ifs_poisoned = True
                            continue
                        m_eq = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", a.raw, re.S)
                        if m_eq:
                            vn = m_eq.group(1)
                            if vn == "TMPDIR":
                                tmpdir_poisoned = True
                            elif vn == "CDPATH":
                                cdpath_poisoned = True
                            elif vn == "IFS":
                                ifs_poisoned = True
                            if has_unsafe_decl_flag or in_branch or cmd0 in ("local", "declare", "typeset"):
                                poisoned_vars.add(vn)
                        else:
                            m_vn = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", a.value)
                            if m_vn:
                                vn = m_vn.group(1)
                                poisoned_vars.add(vn)
                                if vn == "TMPDIR":
                                    tmpdir_poisoned = True
                                elif vn == "CDPATH":
                                    cdpath_poisoned = True
                                elif vn == "IFS":
                                    ifs_poisoned = True
                elif cmd0 == "printf":
                    pi = 0
                    while pi < len(cmd_args):
                        if cmd_args[pi].value == "-v" and pi + 1 < len(cmd_args):
                            m_vn = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", cmd_args[pi + 1].value)
                            if m_vn:
                                vn = m_vn.group(1)
                                poisoned_vars.add(vn)
                                if vn == "TMPDIR":
                                    tmpdir_poisoned = True
                                elif vn == "CDPATH":
                                    cdpath_poisoned = True
                                elif vn == "IFS":
                                    ifs_poisoned = True
                            pi += 2
                            continue
                        pi += 1
                elif cmd0 == "mkdir" and not in_branch:
                    for a in cmd_args:
                        if not a.value.startswith("-") and _is_safe_tmp_target(a.value):
                            potential_mkdir_dirs.add(a.value)

            prev_was_func_header = bool(func_hdr_name)
            for _ in range(stmt_brace_close):
                p_brace = max(0, p_brace - 1)

    mark_var_poison_scan()
    if shadowed_sensitive:
        return False

    env_tmpdir_valid = (not tmpdir_poisoned) and is_valid_env_tmpdir(env_tmpdir)

    # Run second pass to verify safe assignments vs non-safe re-assignments
    for st in stmts:
        toks = _split_tokens_with_quote_info(st["text"])
        if toks is None:
            return False
        for tk in toks:
            m_eq = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", tk.raw, re.S)
            if m_eq:
                vn = m_eq.group(1)
                if vn in poisoned_vars:
                    continue
                val_raw = m_eq.group(2)
                eq_idx = tk.value.find("=")
                val_str = tk.value[eq_idx + 1 :] if eq_idx >= 0 else ""
                if _classify_safe_var_assignment(
                    vn, val_raw, val_str, env_tmpdir_valid, tmpdir_poisoned, potential_mkdir_dirs, ws_roots=ws_roots
                ) is None:
                    poisoned_vars.add(vn)

    # Pass 2: Sequential statement verification
    pristine_ws_cwd = (
        validate_initial_cwd_in_workspace(initial_cwd, ws_roots)
        if (ws_roots and not cwd_pristine_unsafe)
        else None
    )
    cwd_mutated = False
    safe_vars_active: dict[str, SafeVarInfo] = {}
    created_tmp_dirs: set[str] = set()
    created_ws_dirs: set[str] = set()
    saw_symlink_cmd = False
    current_cd_cwd: str | None = None
    current_cd_ws_real: str | None = None
    cwd_chain_only = False
    errexit = False
    if_depth = 0
    loop_depth = 0
    case_depth = 0
    brace_depth = 0
    rm_count = 0

    def get_effective_cwd() -> tuple[str | None, str | None]:
        if not cwd_mutated:
            if pristine_ws_cwd is not None:
                return pristine_ws_cwd[1], pristine_ws_cwd[0]
            return None, None
        return current_cd_cwd, current_cd_ws_real

    def valid_operand(op: ShellToken) -> bool:
        val = op.value
        expand0 = bool(op.expandable and op.expandable[0])

        if ifs_poisoned and _has_unquoted_dollar(op.raw):
            return False

        # Rule A: direct /tmp/<concrete> or /private/tmp/<concrete>
        if _is_safe_tmp_target(val):
            return True

        # Rule B1: $TMPDIR/<subpath>
        if _is_safe_tmpdir_operand(val, expand0, env_tmpdir, env_tmpdir_valid, tmpdir_poisoned, env_tmpdir_set):
            return True

        # Rule B2: direct ~/.gemini/antigravity/brain/<uuid>/scratch/<subpath>
        m_scr = SCRATCH_ROOT_RE.match(val)
        if m_scr and m_scr.group(2) and _is_safe_scratch_target(m_scr.group(1), m_scr.group(2)):
            return True

        # Rule C: safe leaf glob directly in /tmp, /private/tmp, env TMPDIR, or scratch
        if _is_safe_leaf_glob(val, allow_scratch=True, env_tmpdir=env_tmpdir, env_tmpdir_valid=env_tmpdir_valid):
            return True

        # Rule W1: direct concrete path or bounded leaf glob strictly inside active workspace root
        if ws_roots and is_safe_workspace_target(val, ws_roots, allow_glob=True):
            return True

        # Rule D: safe variable reference ($tmp, "$tmp", $S/sub, $W/sub)
        if expand0 and val.startswith("$"):
            m_var = re.fullmatch(r"(?:\$([A-Za-z_][A-Za-z0-9_]*)|\$\{([A-Za-z_][A-Za-z0-9_]*)\})(/.*)?", val)
            if m_var:
                vname = m_var.group(1) or m_var.group(2)
                suffix = m_var.group(3) or ""
                if vname not in poisoned_vars and vname in safe_vars_active:
                    vinfo = safe_vars_active[vname]
                    if not suffix:
                        if vinfo.kind == "ws_subpath" and vinfo.ws_real:
                            m_ws_bare = match_workspace_path(vinfo.base_path, [(vinfo.ws_real, vinfo.ws_real)])
                            return bool(
                                m_ws_bare
                                and validate_workspace_rel_subpath(
                                    m_ws_bare[0], m_ws_bare[1], allow_glob=False, check_protected_anchors=True
                                )
                            )
                        if vinfo.kind in ("mktemp_file", "mktemp_dir", "tmp_path", "scratch_subpath"):
                            return True
                        return False
                    rel = strip_leading_dotslash(suffix[1:])
                    if not rel or "$" in rel:
                        return False
                    if vinfo.kind == "mktemp_file":
                        return False
                    if vinfo.kind == "mktemp_dir":
                        parts = [p for p in rel.split("/") if p != ""]
                        if not parts or "." in parts or ".." in parts:
                            return False
                        if "*" in rel or "?" in rel:
                            last_sl = rel.rfind("/")
                            p_rel = rel[:last_sl] if last_sl >= 0 else ""
                            leaf = rel[last_sl + 1 :] if last_sl >= 0 else rel
                            if any(c in p_rel for c in FORBIDDEN_TMP_CHARS):
                                return False
                            if not leaf or leaf.startswith(".") or not re.fullmatch(r"[A-Za-z0-9._*?-]+", leaf):
                                return False
                            return True
                        return not any(c in FORBIDDEN_TMP_CHARS for c in rel)
                    # Concrete root/path variables: validate expanded path against A / B2 / C / W1
                    expanded_full = vinfo.base_path.rstrip("/") + "/" + rel
                    if vinfo.kind in ("tmp_path", "tmp_root"):
                        return _is_safe_tmp_target(expanded_full) or _is_safe_leaf_glob(expanded_full, allow_scratch=False)
                    if vinfo.kind in ("scratch_root", "scratch_subpath"):
                        m_sub = SCRATCH_ROOT_RE.match(expanded_full)
                        if m_sub and m_sub.group(2):
                            if _is_safe_scratch_target(m_sub.group(1), m_sub.group(2)):
                                return True
                            return _is_safe_leaf_glob(expanded_full, allow_scratch=True)
                        return False
                    if vinfo.kind in ("ws_root", "ws_subpath") and vinfo.ws_real:
                        m_ws = match_workspace_path(expanded_full, [(vinfo.ws_real, vinfo.ws_real)])
                        if m_ws:
                            return validate_workspace_rel_subpath(m_ws[0], m_ws[1], allow_glob=True)
                        return False
                return False

        # Rule E / W2: relative operand when inside verified tmp cwd or active workspace cwd
        eff_cwd, eff_ws_real = get_effective_cwd()
        if eff_cwd is not None:
            if not val or val.startswith(("/", "~", "-", "$")) or val in (".", "./"):
                return False
            clean = strip_leading_dotslash(val)
            if not clean:
                return False
            if "$" in clean or any(c in clean for c in ("`", "[", "]", "{", "}", "~", "\\", ";", "|", "&", "<", ">")):
                return False
            if eff_ws_real is not None:
                base_rel = ws_relpath(eff_cwd, eff_ws_real)
                if base_rel is None:
                    return False
                full_rel = f"{base_rel}/{clean}" if base_rel else clean
                return validate_workspace_rel_subpath(eff_ws_real, full_rel, allow_glob=True)
            parts = [p for p in clean.split("/") if p != ""]
            if not parts or "." in parts or ".." in parts:
                return False
            if eff_cwd == "__MKTEMP_DIR__":
                if "*" in clean or "?" in clean:
                    last_sl = clean.rfind("/")
                    p_rel = clean[:last_sl] if last_sl >= 0 else ""
                    leaf = clean[last_sl + 1 :] if last_sl >= 0 else clean
                    if any(c in p_rel for c in FORBIDDEN_TMP_CHARS):
                        return False
                    return bool(leaf and not leaf.startswith(".") and re.fullmatch(r"[A-Za-z0-9._*?-]+", leaf))
                return not any(c in FORBIDDEN_TMP_CHARS for c in clean)
            combined = eff_cwd.rstrip("/") + "/" + clean
            return (
                _is_safe_tmp_target(combined)
                or (
                    env_tmpdir_valid
                    and env_tmpdir
                    and combined.startswith(env_tmpdir + "/")
                    and _validate_rel_subpath_under_root(env_tmpdir, combined[len(env_tmpdir) + 1 :], allow_glob=True)
                )
                or _is_safe_leaf_glob(combined, allow_scratch=False, env_tmpdir=env_tmpdir, env_tmpdir_valid=env_tmpdir_valid)
            )

        return False

    prev_was_func_header_p2 = False
    cur_pipeline_frags: list[str] = []
    n_stmts_p2 = len(stmts)
    for st_idx, st in enumerate(stmts):
        next_st_p2 = stmts[st_idx + 1] if st_idx + 1 < n_stmts_p2 else None
        conn_prev = st["conn_prev"]
        conn_next = st["conn_next"]
        p_depth = st["paren_depth"]
        if conn_prev in ("|", "|&"):
            cur_pipeline_frags.append(st["text"])
        else:
            cur_pipeline_frags = [st["text"]]

        if p_depth != 0 and contains_rm_command(st["text"]):
            return False

        # Chain & branch resets before evaluating current statement
        if conn_prev in (";", "\n", "||", "|", "|&", "&", "(", ")"):
            for k in [vn for vn, vi in safe_vars_active.items() if vi.chain_only]:
                del safe_vars_active[k]
            if cwd_chain_only:
                current_cd_cwd = None
                current_cd_ws_real = None
                cwd_chain_only = False
        if conn_prev in ("||", "|", "|&", "(", ")") or conn_next in ("|", "|&", "&") or p_depth > 0 or brace_depth > 0:
            current_cd_cwd = None
            current_cd_ws_real = None
            cwd_chain_only = False

        toks = _split_tokens_with_quote_info(st["text"])
        if toks is None:
            return False
        if not toks:
            continue

        # Update control-flow depth and track leading keywords
        negated = False
        idx = 0
        while idx < len(toks):
            v_low = toks[idx].value.lower()
            if not toks[idx].had_quote_or_escape and v_low == "!":
                negated = True
                idx += 1
                continue
            if not toks[idx].had_quote_or_escape and v_low in SHELL_PREFIX_KEYWORDS:
                if v_low == "if":
                    if_depth += 1
                    current_cd_cwd = None
                    current_cd_ws_real = None
                elif v_low == "fi":
                    if_depth = max(0, if_depth - 1)
                    current_cd_cwd = None
                    current_cd_ws_real = None
                elif v_low in ("elif", "else", "then"):
                    current_cd_cwd = None
                    current_cd_ws_real = None
                elif v_low in ("for", "while", "until", "select"):
                    loop_depth += 1
                    current_cd_cwd = None
                    current_cd_ws_real = None
                elif v_low == "done":
                    loop_depth = max(0, loop_depth - 1)
                    current_cd_cwd = None
                    current_cd_ws_real = None
                elif v_low == "case":
                    case_depth += 1
                    current_cd_cwd = None
                    current_cd_ws_real = None
                elif v_low == "esac":
                    case_depth = max(0, case_depth - 1)
                    current_cd_cwd = None
                    current_cd_ws_real = None
                elif v_low == "{":
                    brace_depth += 1
                    current_cd_cwd = None
                    current_cd_ws_real = None
                elif v_low == "}":
                    brace_depth = max(0, brace_depth - 1)
                    current_cd_cwd = None
                    current_cd_ws_real = None
                idx += 1
                continue
            break

        rem = toks[idx:]
        if rem and not rem[0].had_quote_or_escape and rem[0].value.lower() in ("for", "select"):
            loop_depth += 1
            current_cd_cwd = None
            current_cd_ws_real = None
        stmt_b_close = 0
        for tk in rem:
            if not tk.had_quote_or_escape:
                if tk.value == "{":
                    brace_depth += 1
                    current_cd_cwd = None
                    current_cd_ws_real = None
                elif tk.value == "}":
                    stmt_b_close += 1

        func_hdr_p2 = _is_func_def_header_stmt(st, toks, next_st_p2)
        is_zsh_func_p2 = prev_was_func_header_p2 and conn_prev == ")"
        if is_zsh_func_p2:
            current_cd_cwd = None
            current_cd_ws_real = None
            cwd_chain_only = False

        in_branch = (
            if_depth > 0
            or loop_depth > 0
            or case_depth > 0
            or brace_depth > 0
            or p_depth > 0
            or conn_prev in ("||", "|", "|&")
            or conn_next in ("|", "|&", "&")
            or is_zsh_func_p2
        )

        if len(cur_pipeline_frags) > 1 and _fragment_creates_symlink(" | ".join(cur_pipeline_frags)):
            saw_symlink_cmd = True
            current_cd_cwd = None
            current_cd_ws_real = None

        if not rem:
            prev_was_func_header_p2 = bool(func_hdr_p2)
            for _ in range(stmt_b_close):
                brace_depth = max(0, brace_depth - 1)
            continue

        # Check unwrapped command for symlink creation (shell ln, subshells/sh -c, node/perl/ruby/python) before rm inspection
        eff_cmd, eff_args = _unwrap_shell_mutator_cmd(rem)
        if eff_cmd == "ln" or _fragment_creates_symlink(st["text"]):
            saw_symlink_cmd = True
            if eff_cmd != "ln":
                current_cd_cwd = None
                current_cd_ws_real = None

        if eff_cmd == "mkdir" and not in_branch and not negated and (
            conn_next == "&&" or (errexit and conn_next in (";", "\n"))
        ):
            cur_mkdir_cwd, cur_mkdir_ws = get_effective_cwd()
            has_parents = any(
                not a.had_quote_or_escape
                and (a.value == "--parents" or (a.value.startswith("-") and not a.value.startswith("--") and "p" in a.value[1:]))
                for a in eff_args
            )
            mi = 0
            while mi < len(eff_args):
                a = eff_args[mi]
                if a.value == "-m":
                    mi += 2
                    continue
                if a.value.startswith("-"):
                    mi += 1
                    continue
                if _is_safe_tmp_target(a.value):
                    created_tmp_dirs.add(a.value)
                elif ws_roots:
                    ws_base_real: str | None = None
                    rel_mk: str | None = None
                    if a.value.startswith("/"):
                        m_ws = match_workspace_path(a.value, ws_roots)
                        if m_ws and validate_workspace_rel_subpath(
                            m_ws[0], m_ws[1], allow_glob=False, check_protected_anchors=False
                        ):
                            ws_base_real = m_ws[0]
                            rel_mk = strip_leading_dotslash(m_ws[1]).rstrip("/")
                    elif cur_mkdir_ws is not None and cur_mkdir_cwd is not None and not a.value.startswith(("~", "-", "$")):
                        base_mk = ws_relpath(cur_mkdir_cwd, cur_mkdir_ws)
                        clean_mk = strip_leading_dotslash(a.value).rstrip("/")
                        if base_mk is not None and clean_mk and not any(c in FORBIDDEN_WS_CHARS for c in a.value):
                            comb_mk = f"{base_mk}/{clean_mk}" if base_mk else clean_mk
                            if validate_workspace_rel_subpath(
                                cur_mkdir_ws, comb_mk, allow_glob=False, check_protected_anchors=False
                            ):
                                ws_base_real = cur_mkdir_ws
                                rel_mk = comb_mk
                    if ws_base_real and rel_mk:
                        parts_mk = [p for p in rel_mk.split("/") if p]
                        if has_parents:
                            for k in range(1, len(parts_mk) + 1):
                                created_ws_dirs.add(norm_cmp_path(os.path.join(ws_base_real, *parts_mk[:k])))
                        else:
                            created_ws_dirs.add(norm_cmp_path(os.path.join(ws_base_real, *parts_mk)))
                mi += 1

        # Strip pure safe redirections (`>/dev/null`, `2>/dev/null`, `2>&1`) for statement checks
        if contains_rm_command(st["text"]):
            if saw_symlink_cmd:
                return False
            rm_count += 1
            rm_toks: list[ShellToken] = []
            ri = 0
            while ri < len(rem):
                rt = rem[ri]
                if not rt.had_quote_or_escape and rt.value in ("command", "noglob") and not rm_toks:
                    ri += 1
                    continue
                if not rt.had_quote_or_escape and REDIR_ONLY_RE.match(rt.value):
                    if rt.value in ("2>", ">", "&>", "<", "0<") and ri + 1 < len(rem) and rem[ri + 1].value == "/dev/null":
                        ri += 2
                        continue
                    return False
                if not rt.had_quote_or_escape and rt.value in (
                    "2>/dev/null", ">/dev/null", "&>/dev/null", "</dev/null", "0</dev/null", "2>&1", "1>&2",
                ):
                    ri += 1
                    continue
                if not rt.had_quote_or_escape and REDIR_INLINE_RE.match(rt.value):
                    return False
                rm_toks.append(rt)
                ri += 1
            if not rm_toks:
                return False
            cmd_tok = rm_toks[0]
            if cmd_tok.had_quote_or_escape or cmd_tok.value not in ("rm", "/bin/rm"):
                return False
            operands: list[ShellToken] = []
            end_opts = False
            for op_tok in rm_toks[1:]:
                if not end_opts and not op_tok.had_quote_or_escape and op_tok.value == "--":
                    end_opts = True
                    continue
                if not end_opts and op_tok.value.startswith("-"):
                    if not op_tok.had_quote_or_escape and (
                        re.fullmatch(r"-[rRfvd]+", op_tok.value) or op_tok.value in ALLOWED_RM_LONG_OPTS
                    ):
                        continue
                    return False
                operands.append(op_tok)
            if not operands or not all(valid_operand(op) for op in operands):
                return False
            for _ in range(stmt_b_close):
                brace_depth = max(0, brace_depth - 1)
            continue

        # Pure variable assignments (e.g., tmp=$(mktemp), S=<scratch_dir>)
        if all(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t.raw) for t in rem):
            if not in_branch and not negated:
                is_chain_assign = (conn_prev == "&&")
                for tk in rem:
                    m_eq = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", tk.raw, re.S)
                    if not m_eq:
                        continue
                    vn, vraw = m_eq.group(1), m_eq.group(2)
                    if vn in poisoned_vars:
                        safe_vars_active.pop(vn, None)
                        continue
                    eq_pos = tk.value.find("=")
                    vstr = tk.value[eq_pos + 1 :] if eq_pos >= 0 else ""
                    vinfo = _classify_safe_var_assignment(
                        vn, vraw, vstr, env_tmpdir_valid, tmpdir_poisoned, created_tmp_dirs, ws_roots=ws_roots
                    )
                    if vinfo is not None:
                        vinfo.chain_only = is_chain_assign
                        safe_vars_active[vn] = vinfo
                    else:
                        safe_vars_active.pop(vn, None)
            for _ in range(stmt_b_close):
                brace_depth = max(0, brace_depth - 1)
            continue

        base0 = os.path.basename(rem[0].value) if not rem[0].had_quote_or_escape else ""

        # Handle `export S=...` or `readonly tmp=...` (not `local`, which is function-scoped)
        if base0 in ("export", "readonly") and not in_branch and not negated:
            is_chain_assign = (conn_prev == "&&")
            for a in rem[1:]:
                m_eq = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", a.raw, re.S)
                if m_eq:
                    vn, vraw = m_eq.group(1), m_eq.group(2)
                    if vn in poisoned_vars:
                        safe_vars_active.pop(vn, None)
                        continue
                    eq_pos = a.value.find("=")
                    vstr = a.value[eq_pos + 1 :] if eq_pos >= 0 else ""
                    vinfo = _classify_safe_var_assignment(
                        vn, vraw, vstr, env_tmpdir_valid, tmpdir_poisoned, created_tmp_dirs, ws_roots=ws_roots
                    )
                    if vinfo is not None:
                        vinfo.chain_only = is_chain_assign
                        safe_vars_active[vn] = vinfo
                    else:
                        safe_vars_active.pop(vn, None)
            for _ in range(stmt_b_close):
                brace_depth = max(0, brace_depth - 1)
            continue

        # Handle `set -e` / `set +e`
        if eff_cmd == "set":
            if base0 == "set" and not in_branch and not negated and conn_prev != "&&":
                ai = 1
                while ai < len(rem):
                    av = rem[ai].value
                    if av == "--":
                        break
                    if av == "-o" and ai + 1 < len(rem) and rem[ai + 1].value == "errexit":
                        errexit = True
                        ai += 2
                        continue
                    if av == "+o" and ai + 1 < len(rem) and rem[ai + 1].value == "errexit":
                        errexit = False
                        current_cd_cwd = None
                        current_cd_ws_real = None
                        ai += 2
                        continue
                    if re.fullmatch(r"-[A-Za-z]*e[A-Za-z]*", av):
                        errexit = True
                    elif re.fullmatch(r"\+[A-Za-z]*e[A-Za-z]*", av):
                        errexit = False
                        current_cd_cwd = None
                        current_cd_ws_real = None
                    ai += 1
            for _ in range(stmt_b_close):
                brace_depth = max(0, brace_depth - 1)
            continue

        if eff_cmd in ("eval", "source", "."):
            safe_vars_active.clear()
            cwd_mutated = True
            pristine_ws_cwd = None
            current_cd_cwd = None
            current_cd_ws_real = None
            cwd_chain_only = False
            ifs_poisoned = True
            tmpdir_poisoned = True
            cdpath_poisoned = True

        # Handle `cd <dir>` (both /tmp and verified active workspace directories)
        if eff_cmd in ("cd", "pushd", "popd", "chdir"):
            prev_cwd, prev_ws_real = get_effective_cwd()
            cwd_mutated = True
            pristine_ws_cwd = None
            if base0 == "cd" and not in_branch and not negated:
                cd_args = [a for a in rem[1:] if not a.value.startswith("-") and a.value != "--"]
                if len(cd_args) == 1:
                    t_op = cd_args[0]
                    tv = t_op.value
                    t_exp0 = bool(t_op.expandable and t_op.expandable[0])
                    new_cwd: str | None = None
                    new_ws_real: str | None = None
                    if ifs_poisoned and _has_unquoted_dollar(t_op.raw):
                        new_cwd = None
                    elif (
                        tv in ("/tmp", "/private/tmp")
                        or _is_safe_tmp_target(tv)
                        or (
                            env_tmpdir_valid
                            and env_tmpdir
                            and (
                                tv.rstrip("/") == env_tmpdir
                                or (
                                    tv.startswith(env_tmpdir + "/")
                                    and _validate_rel_subpath_under_root(env_tmpdir, tv[len(env_tmpdir) + 1 :], allow_glob=False)
                                )
                            )
                        )
                    ):
                        new_cwd = tv
                    elif t_exp0 and tv.startswith("$"):
                        if tv in ("$TMPDIR", "${TMPDIR}") and env_tmpdir_valid and env_tmpdir:
                            new_cwd = env_tmpdir
                        else:
                            m_v = re.fullmatch(r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\})", tv)
                            if m_v:
                                vn = m_v.group(1) or m_v.group(2)
                                if vn not in poisoned_vars and vn in safe_vars_active:
                                    vi = safe_vars_active[vn]
                                    if vi.kind == "mktemp_dir":
                                        new_cwd = "__MKTEMP_DIR__"
                                    elif vi.kind in ("tmp_root", "tmp_path"):
                                        new_cwd = vi.base_path
                                    elif vi.kind in ("ws_root", "ws_subpath") and vi.ws_real:
                                        new_cwd = vi.base_path
                                        new_ws_real = vi.ws_real
                    elif ws_roots and tv.startswith("/"):
                        trimmed_tv_cmp = norm_cmp_path(tv.rstrip("/"))
                        for raw_norm, real_norm in ws_roots:
                            if trimmed_tv_cmp == norm_cmp_path(raw_norm) or trimmed_tv_cmp == norm_cmp_path(real_norm):
                                new_cwd = real_norm
                                new_ws_real = real_norm
                                break
                        if new_cwd is None:
                            m_ws = match_workspace_path(tv, ws_roots)
                            if m_ws and validate_workspace_rel_subpath(
                                m_ws[0], m_ws[1], allow_glob=False, check_protected_anchors=False
                            ):
                                clean_rel = strip_leading_dotslash(m_ws[1]).rstrip("/")
                                cand_cwd = os.path.normpath(os.path.join(m_ws[0], clean_rel))
                                if os.path.isdir(cand_cwd) or norm_cmp_path(cand_cwd) in created_ws_dirs:
                                    new_cwd = cand_cwd
                                    new_ws_real = m_ws[0]
                    elif prev_ws_real is not None and prev_cwd is not None and not cdpath_poisoned and not tv.startswith(("/", "~", "-", "$")):
                        if tv in (".", "./"):
                            new_cwd = prev_cwd
                            new_ws_real = prev_ws_real
                        elif not any(c in FORBIDDEN_WS_CHARS for c in tv):
                            clean_cd = strip_leading_dotslash(tv).rstrip("/")
                            base_rel = ws_relpath(prev_cwd, prev_ws_real)
                            if base_rel is not None:
                                comb_rel = f"{base_rel}/{clean_cd}" if base_rel and clean_cd else clean_cd
                                if comb_rel and validate_workspace_rel_subpath(
                                    prev_ws_real, comb_rel, allow_glob=False, check_protected_anchors=False
                                ):
                                    cand_cwd = os.path.normpath(os.path.join(prev_ws_real, comb_rel))
                                    if os.path.isdir(cand_cwd) or norm_cmp_path(cand_cwd) in created_ws_dirs:
                                        new_cwd = cand_cwd
                                        new_ws_real = prev_ws_real

                    can_persist = (conn_next == "&&") or (errexit and conn_next in (";", "\n"))
                    if new_cwd is not None and can_persist:
                        current_cd_cwd = new_cwd
                        current_cd_ws_real = new_ws_real
                        cwd_chain_only = (conn_next == "&&") and not errexit
                        for _ in range(stmt_b_close):
                            brace_depth = max(0, brace_depth - 1)
                        continue
            current_cd_cwd = None
            current_cd_ws_real = None
            cwd_chain_only = False

        for _ in range(stmt_b_close):
            brace_depth = max(0, brace_depth - 1)
        prev_was_func_header_p2 = bool(func_hdr_p2)

    return rm_count > 0


def all_rm_invocations_are_safe_tmp(shell_cmd: str) -> bool:
    """Backward-compatible wrapper returning True iff every rm targets safe /tmp, mktemp, or scratch paths."""
    return all_rm_invocations_are_safe(shell_cmd)


VITEST_ONESHOT_ARGS = frozenset({
    "run", "--run", "--watch=false", "--no-watch", "--version", "-v", "--help", "-h", "list", "init",
})
VITEST_OPTS_WITH_VALUE = frozenset({
    "-t", "--testNamePattern", "-c", "--config", "-r", "--root", "--dir", "--project",
    "--environment", "--reporter", "--outputFile", "--pool", "--shard", "--bail", "--retry", "--testTimeout", "--hookTimeout",
})
RUNNER_OPTS_WITH_VALUE = frozenset({
    "-C", "--dir", "--filter", "-F", "--prefix", "-p", "--package", "--directory", "--project", "--python", "--with",
})


def _strip_pkg_version(name: str) -> str:
    if not name or name.startswith("@"):
        return name
    return name.split("@", 1)[0]


def _unwrap_runner_tokens(tokens: list[str], unwrap_pkg: bool = True) -> tuple[str, list[str]]:
    """Unwrap env/nohup/sudo/pnpm/uv/npx/python -m prefixes to extract (target_tool_basename, remaining_args)."""
    idx = 0
    n = len(tokens)
    while idx < n:
        raw_base = os.path.basename(tokens[idx].lstrip("=$")).lower()
        base = _strip_pkg_version(raw_base)
        if base in ("nohup", "command", "exec", "builtin", "caffeinate", "doas", "stdbuf", "arch", "timeout", "gtimeout"):
            val_opts = PREFIX_WRAPPER_VAL_OPTS.get(base, frozenset())
            idx += 1
            while idx < n and tokens[idx].startswith("-") and tokens[idx] != "--":
                if tokens[idx] in val_opts:
                    idx += 2
                else:
                    idx += 1
            if idx < n and tokens[idx] == "--":
                idx += 1
            if base in ("timeout", "gtimeout") and idx < n and not _is_rm_cmd_word(tokens[idx]):
                idx += 1
            continue
        if base == "xargs":
            rem = _skip_xargs_opts(tokens[idx + 1 :])
            if not rem:
                return "", []
            tokens = rem
            idx = 0
            n = len(tokens)
            continue
        if base == "parallel":
            idx += 1
            while idx < n:
                t = tokens[idx]
                if t == "--":
                    idx += 1
                    break
                if t.startswith(":::"):
                    return "", []
                if t in PARALLEL_VALUE_OPTS:
                    idx += 2
                    continue
                if t.startswith("-") and t != "-":
                    idx += 1
                    continue
                break
            continue
        if base == "time":
            idx += 1
            while idx < n:
                t = tokens[idx]
                if t == "--":
                    idx += 1
                    break
                if t in ("-o", "--output", "-f", "--format"):
                    idx += 2
                    continue
                if t.startswith("-") and t != "-":
                    idx += 1
                    continue
                break
            continue
        if base == "env":
            split_strs: list[str] = []
            idx += 1
            while idx < n:
                t = tokens[idx]
                if t == "--":
                    idx += 1
                    break
                if (not t.startswith("-") and "=" in t) or t in ("-i", "-0"):
                    idx += 1
                    continue
                if t in ("-S", "--split-string"):
                    if idx + 1 >= n:
                        return "", []
                    split_strs.append(tokens[idx + 1])
                    idx += 2
                    continue
                if t.startswith("--split-string="):
                    split_strs.append(t.split("=", 1)[1])
                    idx += 1
                    continue
                if t.startswith("-") and not t.startswith("--") and "S" in t:
                    pos = t.index("S")
                    if pos + 1 < len(t):
                        split_strs.append(t[pos + 1 :])
                        idx += 1
                    elif idx + 1 < n:
                        split_strs.append(tokens[idx + 1])
                        idx += 2
                    else:
                        return "", []
                    continue
                if t in ("-u", "-C"):
                    idx += 2
                elif t.startswith("-"):
                    idx += 1
                else:
                    break
            if split_strs:
                head_toks: list[str] = []
                for sv in split_strs:
                    try:
                        head_toks.extend(shlex.split(sv, posix=True))
                    except ValueError:
                        return "__UNSAFE_ENV_SPLIT__", []
                while head_toks and not head_toks[0].startswith("-") and "=" in head_toks[0]:
                    head_toks.pop(0)
                tokens = list(tokens[:idx]) + head_toks + list(tokens[idx:])
                n = len(tokens)
            continue
        if base in ("sudo", "nice"):
            idx += 1
            while idx < n and tokens[idx].startswith("-"):
                if tokens[idx] in ("-u", "-g", "-n", "-C"):
                    idx += 2
                else:
                    idx += 1
            continue
        if unwrap_pkg and base in ("pnpm", "uv", "npx", "pnpx", "bunx", "yarn", "npm", "uvx", "bun"):
            next_idx, did_unwrap = _unwrap_pkg_manager_invocation(
                tokens,
                idx,
                lambda t: t,
                lambda _t, tv: tv.startswith("-") and tv != "-",
            )
            if did_unwrap and next_idx > idx:
                idx = next_idx
                continue
            break
        if unwrap_pkg and re.fullmatch(r"python(?:3(?:\.\d+)?)?", base):
            p_idx = idx + 1
            while p_idx < n and tokens[p_idx].startswith("-") and tokens[p_idx] != "-m":
                if tokens[p_idx] in ("-W", "-X"):
                    p_idx += 2
                else:
                    p_idx += 1
            if p_idx + 1 < n and tokens[p_idx] == "-m":
                mod = _strip_pkg_version(os.path.basename(tokens[p_idx + 1]).lower())
                return mod, tokens[p_idx + 2 :]
        break
    if idx >= n:
        return "", []
    return _strip_pkg_version(os.path.basename(tokens[idx].lstrip("=$")).lower()), tokens[idx + 1 :]


def detect_noninteractive_hang(command: str, is_daemon: bool, _depth: int = 0) -> str | None:
    """Detect interactive/watch-mode commands that hang forever when IsDaemon is false."""
    if is_daemon or _depth > 2:
        return None
    segments = _split_shell_ops(command)
    if not segments:
        return None
    for seg in segments:
        try:
            tokens = shlex.split(seg, posix=True)
        except ValueError:
            continue
        tokens = _strip_leading_shell_tokens(tokens)
        if not tokens:
            continue
        lead = os.path.basename(tokens[0].lstrip("=$")).lower()
        if lead in ("timeout", "gtimeout"):
            continue
        tool_base, tool_args = _unwrap_runner_tokens(tokens)
        if tool_base in ("sh", "bash", "zsh", "dash", "ksh"):
            for nested_cmd in _extract_shell_exec_inputs(tool_args):
                nested = detect_noninteractive_hang(nested_cmd, is_daemon, _depth + 1)
                if nested:
                    return nested
            continue
        if tool_base == "vitest":
            has_explicit_watch = False
            has_oneshot = False
            vi = 0
            while vi < len(tool_args):
                a = tool_args[vi]
                if a in VITEST_OPTS_WITH_VALUE:
                    vi += 2
                    continue
                if a in ("watch", "--watch", "-w", "--watch=true"):
                    has_explicit_watch = True
                elif a in VITEST_ONESHOT_ARGS or (a.startswith("--run=") and not a.endswith("=false")):
                    has_oneshot = True
                vi += 1
            if has_explicit_watch or not has_oneshot:
                return (
                    "非デーモン実行での vitest ウォッチモード起動は無限待機の原因になります。"
                    "最適解: `pnpm --dir <dir> exec vitest run --testTimeout=30000` のように `run` を明示してください（常駐させる場合は IsDaemon: true を指定）。"
                )
        elif tool_base == "jest":
            if any(
                a in ("--watch", "--watchAll")
                or (a.startswith(("--watch=", "--watchAll=")) and not a.endswith("=false"))
                for a in tool_args
            ):
                return (
                    "非デーモン実行での jest ウォッチモード起動は無限待機の原因になります。"
                    "最適解: `--ci` または `--watchAll=false` を指定してください。"
                )
        elif tool_base == "playwright":
            if any(
                a in ("--ui", "--debug")
                or a.startswith(("--ui=", "--ui-port", "--ui-host"))
                for a in tool_args
            ):
                return (
                    "非デーモン実行での playwright --ui / --debug は対話待機の原因になります。"
                    "最適解: ヘッドレス単発実行 (`pnpm exec playwright test --timeout=30000`) を使用してください。"
                )
        elif tool_base in ("pytest", "py.test"):
            if any(a in ("--pdb", "-f", "--looponfail") for a in tool_args):
                return "非デーモン実行での pytest --pdb / --looponfail は無限待機の原因になります。単発実行フラグのみで実行してください。"
        elif tool_base == "tail":
            for a in tool_args:
                if a == "--":
                    break
                if (
                    a in ("-f", "-F", "--follow")
                    or a.startswith("--follow=")
                    or re.fullmatch(r"-[0-9A-Za-z]*[fF][0-9A-Za-z]*", a)
                ):
                    return (
                        "非デーモン実行での `tail -f` は終了せずタスクを永久停止させます。"
                        "最適解: `read_file` ツール（StartLine/EndLine 指定）でログを直接読み取ってください。"
                    )
    return None


def _unwrap_prefix_shell_tokens(tokens: list[ShellToken]) -> list[ShellToken]:
    """Strip redirections, env/keywords, and simple runner wrappers while preserving ShellToken spans."""
    kept, _ = _strip_all_redirs_collect_targets(tokens)
    kept = _strip_group_wrappers(kept)
    idx = 0
    n = len(kept)
    while idx < n:
        v = kept[idx].value
        vl = v.lower()
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", kept[idx].raw) or vl in SHELL_PREFIX_KEYWORDS:
            idx += 1
            continue
        base = os.path.basename(v.lstrip("=$")).lower()
        if base == "env":
            split_strs_sh: list[str] = []
            idx += 1
            while idx < n:
                tv = kept[idx].value
                if tv == "--":
                    idx += 1
                    break
                if (not tv.startswith("-") and "=" in kept[idx].raw) or tv in ("-i", "-0"):
                    idx += 1
                    continue
                if tv in ("-S", "--split-string"):
                    if idx + 1 >= n:
                        idx += 1
                        break
                    split_strs_sh.append(kept[idx + 1].value)
                    idx += 2
                    continue
                if tv.startswith("--split-string="):
                    split_strs_sh.append(tv.split("=", 1)[1])
                    idx += 1
                    continue
                if tv.startswith("-") and not tv.startswith("--") and "S" in tv:
                    pos = tv.index("S")
                    if pos + 1 < len(tv):
                        split_strs_sh.append(tv[pos + 1 :])
                        idx += 1
                    elif idx + 1 < n:
                        split_strs_sh.append(kept[idx + 1].value)
                        idx += 2
                    else:
                        idx += 1
                    continue
                if tv in ENV_VAL_OPTS:
                    idx += 2
                elif tv.startswith("-"):
                    idx += 1
                else:
                    break
            if split_strs_sh:
                syn_toks: list[ShellToken] = []
                for sv in split_strs_sh:
                    try:
                        parts = shlex.split(sv, posix=True)
                    except ValueError:
                        return []
                    for pt in parts:
                        syn_toks.append(
                            ShellToken(pt, [False] * len(pt), False, pt, -1, -1, "$(" in pt or "`" in pt)
                        )
                while syn_toks and not syn_toks[0].value.startswith("-") and "=" in syn_toks[0].value:
                    syn_toks.pop(0)
                kept = list(kept[:idx]) + syn_toks + list(kept[idx:])
                n = len(kept)
            continue
        if base in PREFIX_WRAPPER_VAL_OPTS:
            val_opts = PREFIX_WRAPPER_VAL_OPTS[base]
            idx += 1
            while idx < n:
                tv = kept[idx].value
                if tv == "--":
                    idx += 1
                    break
                if tv in val_opts:
                    idx += 2
                    continue
                if tv.startswith("-") and tv != "-":
                    idx += 1
                    continue
                break
            if base == "sudo":
                while idx < n and not kept[idx].value.startswith("-") and "=" in kept[idx].raw:
                    idx += 1
            if base in ("timeout", "gtimeout") and idx < n:
                idx += 1
            continue
        if base in ("uv", "uvx", "pnpm", "pnpx", "npx", "yarn", "bun", "bunx", "npm"):
            new_idx, did_unwrap = _unwrap_pkg_manager_invocation(
                kept,
                idx,
                lambda t: t.value,
                lambda _t, tv: tv.startswith("-") and tv != "-",
            )
            if did_unwrap:
                idx = new_idx
                continue
            break
        break
    return kept[idx:]


def _target_path_variants(path: str) -> set[str]:
    """Generate normalized path variants (resolving '.'/'..' and /tmp <-> /private/tmp aliases)."""
    p = path.strip()
    if not p or p in ("/dev/null", "/dev/stdout", "/dev/stderr"):
        return set()
    variants = {p}
    if not any(c in p for c in "$`"):
        norm = os.path.normpath(p)
        variants.add(norm)
        for v in list(variants):
            if v.startswith("/private/tmp/"):
                variants.add("/tmp/" + v[len("/private/tmp/") :])
            elif v.startswith("/tmp/"):
                variants.add("/private/tmp/" + v[len("/tmp/") :])
    else:
        norm = re.sub(r"/\.(?=/|$)", "", p)
        variants.add(norm)
    return variants


def _extract_simple_var_map(shell_only_cmd: str) -> dict[str, str]:
    """Extract unambiguous top-level static string assignments VAR=val for heredoc target expansion."""
    var_map: dict[str, str] = {}
    counts: dict[str, int] = {}
    stmts = _split_top_level_statements(shell_only_cmd)
    if not stmts:
        return var_map
    for st in stmts:
        if st["paren_depth"] > 0:
            continue
        toks = _split_tokens_with_quote_info(st["text"])
        if not toks:
            continue
        for t in toks:
            m_eq = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", t.raw, re.S)
            if not m_eq:
                break
            vn = m_eq.group(1)
            counts[vn] = counts.get(vn, 0) + 1
            eq_idx = t.value.find("=")
            val = t.value[eq_idx + 1 :] if eq_idx >= 0 else ""
            if counts[vn] == 1 and "$" not in val and "`" not in val:
                var_map[vn] = val
            else:
                var_map.pop(vn, None)
    return var_map


def _expand_simple_vars(s: str, var_map: dict[str, str]) -> list[str]:
    """Expand $VAR / ${VAR} using static var_map; return original and expanded forms."""
    out = [s]
    if "$" in s and var_map:
        def _repl(m: re.Match[str]) -> str:
            vn = m.group(1) or m.group(2)
            return var_map.get(vn, m.group(0))
        exp = re.sub(r"\$(?:([A-Za-z_][A-Za-z0-9_]*)|\{([A-Za-z_][A-Za-z0-9_]*)\})", _repl, s)
        if exp != s:
            out.append(exp)
    return out


def _shell_script_references_targets(
    script_text: str,
    target_variants: set[str],
    target_basenames: set[str],
    var_map: dict[str, str],
) -> bool:
    """Return True if a shell -c script invokes or sources any path/basename in target_variants."""
    expanded_script = _expand_simple_vars(script_text, var_map)[-1]
    for sc in (script_text, expanded_script):
        try:
            sub_toks = shlex.split(sc, posix=True)
        except ValueError:
            sub_toks = sc.split()
        for t in sub_toks:
            for vt in _expand_simple_vars(t, var_map):
                if _target_path_variants(vt) & target_variants:
                    return True
                if os.path.basename(vt.rstrip("/")) in target_basenames:
                    return True
        for tv in target_variants:
            if tv in sc:
                return True
        for tb in target_basenames:
            if re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(tb)}(?![A-Za-z0-9_.-])", sc):
                return True
    return False


def _collect_segment_consumers(shell_only_cmd: str) -> list[tuple[str, list[str], str, list[str]]]:
    """Return all consumer entries across simple and compound stages in shell_only_cmd."""
    out: list[tuple[str, list[str], str, list[str]]] = []
    for _, _, pls in _split_line_pipelines_with_spans(shell_only_cmd):
        for _, _, st_txt in pls:
            _, _, entries = _extract_stage_consumer_bases_and_targets(st_txt)
            out.extend(entries)
    return out


def _inspect_inline_python_tokens(
    rem: list[ShellToken],
    s_start: int,
    has_shell_peer: bool,
    extra_shell_cmds: list[str],
    raw_fallback_texts: list[str],
    replacements: list[tuple[int, int, str]] | None,
    ws_roots: list[tuple[str, str]] | None = None,
    raw_tok_vals: list[str] | None = None,
    external_symlink_active: bool = False,
) -> bool:
    """Inspect a python* command's -c / -uc flags cleanly; returns True if unsafe_rm found."""
    if not rem:
        return False
    cmd_base = os.path.basename(rem[0].value.lstrip("=$")).lower()
    if not re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", cmd_base):
        return False
    found_unsafe_rm = False
    found_c = _scan_python_cli_c_flag(rem[1:], lambda t: t.value)
    if found_c is not None:
        c_idx, c_pos = found_c
        code_tok = rem[1 + c_idx]
        py_code = code_tok.value[c_pos + 1 :] if c_pos >= 0 else code_tok.value
        u_rm, py_cmds, fb_raw, has_sym = _inspect_python_code(
            py_code,
            ws_roots=ws_roots,
            capture_stdout=has_shell_peer,
            external_symlink_active=external_symlink_active,
        )
        if u_rm:
            found_unsafe_rm = True
        extra_shell_cmds.extend(py_cmds)
        if fb_raw:
            raw_fallback_texts.append(py_code)
        elif (
            replacements is not None
            and code_tok.raw_start >= 0
            and not has_shell_peer
            and not has_sym
            and not code_tok.has_cmd_subst
        ):
            repl_str = "-c __PY_CHECKED__" if c_pos >= 0 else "__PY_CHECKED__"
            replacements.append((s_start + code_tok.raw_start, s_start + code_tok.raw_end, repl_str))
    elif _interp_reads_code_from_stdin(cmd_base, [t.value for t in rem[1:]]):
        hs_src = raw_tok_vals if raw_tok_vals is not None else [t.value for t in rem[1:]]
        for hs in _extract_herestring_texts(hs_src):
            u_rm, py_cmds, fb_raw, has_sym = _inspect_python_code(
                hs,
                ws_roots=ws_roots,
                capture_stdout=has_shell_peer,
                external_symlink_active=external_symlink_active,
            )
            if u_rm or has_sym:
                found_unsafe_rm = True
            extra_shell_cmds.extend(py_cmds)
            if fb_raw:
                raw_fallback_texts.append(hs)
    return found_unsafe_rm


def _analyze_command_inputs(
    raw_cmd: str,
    ws_roots: list[tuple[str, str]] | None = None,
) -> tuple[str, list[str], list[str], bool]:
    """Extract heredocs and sanitize verified python3 -c scripts cleanly.

    Returns (sanitized_shell, extra_shell_cmds, raw_fallback_texts, direct_force_ask).
    """
    shell_only_cmd, heredocs = _extract_heredocs(raw_cmd)
    extra_shell_cmds: list[str] = []
    raw_fallback_texts: list[str] = []
    direct_force_ask = False

    # Inspect heredocs against compound/multiline pipe consumers + file-executing segments
    cmd_pipelines = _split_line_pipelines_with_spans(shell_only_cmd) if (heredocs or "|" in shell_only_cmd) else []
    seg_consumers = _collect_segment_consumers(shell_only_cmd) if heredocs else []
    stage_infos: list[list[tuple[set[str], list[str], list[tuple[str, list[str], str, list[str]]]]]] = []
    if heredocs:
        for _, _, pls in cmd_pipelines:
            row = []
            for _, _, st_txt in pls:
                row.append(_extract_stage_consumer_bases_and_targets(st_txt))
            stage_infos.append(row)
    var_map = _extract_simple_var_map(shell_only_cmd) if heredocs else {}
    exec_tools = SHELL_PROGS | frozenset({"source", ".", "node", "perl", "ruby", "osascript"})
    cmd_has_symlink = (
        ("ln" in shell_only_cmd or "symlink" in shell_only_cmd)
        and _fragment_creates_symlink(shell_only_cmd)
    )
    for hd in heredocs:
        owner_bases, owner_targets, _ = _extract_stage_consumer_bases_and_targets(hd["owner_seg"])
        owner_base, _, _ = _seg_command_base_and_args(hd["owner_seg"])
        downstream_bases: set[str] = set()
        all_targets = list(owner_targets)
        for ds in hd["downstream_pipes"]:
            dbases, dtgts, _ = _extract_stage_consumer_bases_and_targets(ds)
            downstream_bases.update(dbases)
            all_targets.extend(dtgts)
        file_exec_bases: set[str] = set()
        target_variants: set[str] = set()
        target_basenames: set[str] = set()
        has_unresolved_target = False
        for raw_tgt in all_targets:
            for tgt in _expand_simple_vars(raw_tgt, var_map):
                if "$" in tgt or "`" in tgt:
                    has_unresolved_target = True
                vs = _target_path_variants(tgt)
                target_variants.update(vs)
                for v in vs:
                    bn = os.path.basename(v.rstrip("/"))
                    if bn and bn not in (".", "..") and not bn.startswith("-"):
                        target_basenames.add(bn)
        if target_variants or has_unresolved_target:
            def _record_hd_alias(d_raw: str) -> bool:
                nonlocal has_unresolved_target
                chg = False
                for d_exp in _expand_simple_vars(d_raw, var_map):
                    if ("$" in d_exp or "`" in d_exp) and not has_unresolved_target:
                        has_unresolved_target = True
                        chg = True
                    vs_d = _target_path_variants(d_exp)
                    if not (vs_d <= target_variants):
                        target_variants.update(vs_d)
                        chg = True
                    for v in vs_d:
                        bn = os.path.basename(v.rstrip("/"))
                        if bn and bn not in (".", "..") and not bn.startswith("-") and bn not in target_basenames:
                            target_basenames.add(bn)
                            chg = True
                return chg

            for _alias_iter in range(3):
                added_alias = False
                for sb, sargs, first_tok, in_redirs in seg_consumers:
                    if sb not in ("mv", "cp", "ln", "install", "rsync"):
                        continue
                    nf_args = [a for a in sargs if a != "--" and not a.startswith("-")]
                    if len(nf_args) < 2 and not in_redirs:
                        continue
                    srcs = (nf_args[:-1] + in_redirs) if len(nf_args) >= 2 else in_redirs
                    matched_src_basenames: set[str] = set()
                    has_dyn_src = False
                    for cand in srcs:
                        for c_exp in _expand_simple_vars(cand, var_map):
                            if "$" in c_exp or "`" in c_exp:
                                bn_dyn = os.path.basename(c_exp.rstrip("/"))
                                if not bn_dyn or "$" in bn_dyn or bn_dyn in target_basenames:
                                    has_dyn_src = True
                            if (_target_path_variants(c_exp) & target_variants) or (
                                os.path.basename(c_exp.rstrip("/")) in target_basenames
                            ):
                                bn = os.path.basename(c_exp.rstrip("/"))
                                if bn:
                                    matched_src_basenames.add(bn)
                    if not matched_src_basenames and not has_dyn_src:
                        continue
                    if has_dyn_src and not has_unresolved_target:
                        has_unresolved_target = True
                        added_alias = True
                    dst = nf_args[-1] if nf_args else ""
                    if dst:
                        for d_exp in _expand_simple_vars(dst, var_map):
                            if ("$" in d_exp or "`" in d_exp) and not has_unresolved_target:
                                has_unresolved_target = True
                                added_alias = True
                            if d_exp.endswith("/") and matched_src_basenames:
                                for mbn in matched_src_basenames:
                                    if _record_hd_alias(d_exp + mbn):
                                        added_alias = True
                            elif _record_hd_alias(d_exp):
                                added_alias = True
                            if not d_exp.endswith("/") and os.path.basename(d_exp.rstrip("/")) in (".", ".."):
                                for mbn in matched_src_basenames:
                                    if mbn not in target_basenames:
                                        target_basenames.add(mbn)
                                        added_alias = True
                for pl_row in stage_infos:
                    upstream_tainted = False
                    for _, dtgts, s_entries in pl_row:
                        stage_tainted = upstream_tainted
                        for sb_e, sargs_e, _, in_r_e in s_entries:
                            if sb_e in ("cat", "head", "tail", "sed", "tr", "awk", "cut", "base64", "xxd", "tee", "dd", "grep", "egrep", "fgrep", "rg", "sort", "uniq"):
                                cands = list(sargs_e) + list(in_r_e)
                                for cand in cands:
                                    for c_exp in _expand_simple_vars(cand, var_map):
                                        if sb_e == "dd" and c_exp.startswith("if="):
                                            c_exp = c_exp[3:]
                                        if _target_path_variants(c_exp) & target_variants:
                                            stage_tainted = True
                                        elif ("$" in c_exp or "`" in c_exp) and os.path.basename(c_exp.rstrip("/")) in target_basenames:
                                            stage_tainted = True
                        if stage_tainted:
                            upstream_tainted = True
                            for dt in dtgts:
                                if _record_hd_alias(dt):
                                    added_alias = True
                            for sb_e, sargs_e, _, _ in s_entries:
                                if sb_e == "dd":
                                    for a_e in sargs_e:
                                        if a_e.startswith("of=") and _record_hd_alias(a_e[3:]):
                                            added_alias = True
                if not added_alias:
                    break

            for pl_row in stage_infos:
                pl_tainted = False
                for sb_set, _, s_entries in pl_row:
                    if pl_tainted:
                        for db in sb_set:
                            if db in exec_tools or (db and re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", db)):
                                file_exec_bases.add(db)
                    for sb_e, sargs_e, _, in_r_e in s_entries:
                        for cand in (sargs_e + in_r_e):
                            for c_exp in _expand_simple_vars(cand, var_map):
                                if _target_path_variants(c_exp) & target_variants:
                                    pl_tainted = True
                                elif ("$" in c_exp or "`" in c_exp) and has_unresolved_target and os.path.basename(c_exp.rstrip("/")) in target_basenames:
                                    pl_tainted = True

            for sb, sargs, first_tok, in_redirs in seg_consumers:
                is_exec_tool = sb in exec_tools or bool(sb and re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", sb))
                if target_variants:
                    for cand in (sargs + in_redirs):
                        for c_exp in _expand_simple_vars(cand, var_map):
                            if _target_path_variants(c_exp) & target_variants:
                                file_exec_bases.add(sb)
                            elif is_exec_tool and os.path.basename(c_exp.rstrip("/")) in target_basenames:
                                file_exec_bases.add(sb)
                    for ft in _expand_simple_vars(first_tok, var_map):
                        if (_target_path_variants(ft) & target_variants) or (
                            ft.startswith("./") and os.path.basename(ft) in target_basenames
                        ):
                            file_exec_bases.add("sh")
                    if sb in SHELL_PROGS:
                        for sc in _extract_shell_exec_inputs(sargs):
                            if _shell_script_references_targets(sc, target_variants, target_basenames, var_map):
                                file_exec_bases.add(sb)
                    if (
                        sb == "find"
                        and ("-exec" in sargs or "-execdir" in sargs or "-ok" in sargs or "-okdir" in sargs)
                        and (
                            has_unresolved_target
                            or any(
                                (_target_path_variants(c) & target_variants)
                                or (os.path.basename(c.rstrip("/")) in target_basenames)
                                or (c == "." and any(not tv.startswith("/") for tv in target_variants))
                                or (
                                    not c.startswith("-")
                                    and c != "--"
                                    and any(
                                        tv == vc_n or tv.startswith(vc_n + "/")
                                        for vc in _target_path_variants(c)
                                        for vc_n in [vc.rstrip("/")]
                                        if vc_n
                                        for tv in target_variants
                                    )
                                )
                                for a in sargs for c in _expand_simple_vars(a, var_map)
                            )
                        )
                    ):
                        fi = 0
                        while fi < len(sargs):
                            if sargs[fi] in ("-exec", "-execdir", "-ok", "-okdir"):
                                sub_start = fi + 1
                                sub_end = _find_exec_end(sargs, sub_start)
                                if sub_start < sub_end:
                                    eb, _ = _unwrap_runner_tokens(sargs[sub_start:sub_end], unwrap_pkg=True)
                                    if eb and (eb in exec_tools or re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", eb)):
                                        file_exec_bases.add(eb)
                                fi = sub_end + 1
                                continue
                            fi += 1
                if has_unresolved_target and sb in SHELL_PROGS:
                    si_u, n_u = 0, len(sargs)
                    saw_c_opt = False
                    has_script_file = False
                    while si_u < n_u:
                        sa = sargs[si_u]
                        if sa == "--":
                            si_u += 1
                            if si_u < n_u and not saw_c_opt:
                                has_script_file = True
                            break
                        if sa in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
                            si_u += 2
                            continue
                        if sa.startswith("-") and not sa.startswith("--") and "c" in sa[1:]:
                            saw_c_opt = True
                            break
                        if sa.startswith("-") or (sa.startswith("+") and len(sa) > 1):
                            si_u += 1
                            continue
                        has_script_file = True
                        break
                    if has_script_file or any(
                        re.search(r"(?:^|\s)(?:\.|source)\s", sc) for sc in _extract_shell_exec_inputs(sargs)
                    ):
                        file_exec_bases.add(sb)
                if (
                    has_unresolved_target
                    and sb
                    and re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", sb)
                    and _scan_python_cli_c_flag(sargs, lambda t: t) is None
                    and any(("$" in a or "`" in a) for a in sargs if not a.startswith("-"))
                ):
                    file_exec_bases.add(sb)

            for st_hd in _split_top_level_statements(shell_only_cmd) or []:
                st_txt_hd = st_hd["text"]
                if "<(" not in st_txt_hd and "$(" not in st_txt_hd and "`" not in st_txt_hd:
                    continue
                st_toks_hd = _split_tokens_with_quote_info(st_txt_hd)
                if not st_toks_hd:
                    continue
                st_kept_hd, _ = _strip_all_redirs_collect_targets(st_toks_hd)
                st_kept_hd = _strip_group_wrappers(st_kept_hd)
                st_strs_hd = _strip_leading_shell_tokens([t.value for t in st_kept_hd])
                st_base_hd, _ = _unwrap_runner_tokens(st_strs_hd, unwrap_pkg=False)
                if st_base_hd in SHELL_PROGS or st_base_hd in ("source", ".", "eval"):
                    st_subs_hd, _ = _extract_cmd_substitutions(st_txt_hd)
                    if any(
                        _shell_script_references_targets(sub_txt, target_variants, target_basenames, var_map)
                        for sub_txt in st_subs_hd
                    ):
                        file_exec_bases.add("sh")

        if not hd["is_quoted_delim"]:
            hd_subs, hd_unclosed = _extract_heredoc_cmd_substitutions(hd["body"])
            if hd_unclosed:
                raw_fallback_texts.append(hd["body"])
            else:
                extra_shell_cmds.extend(hd_subs)

        consumers = owner_bases | downstream_bases | file_exec_bases
        if hd["unterminated"]:
            raw_fallback_texts.append(hd["body"])
            if any(b in SHELL_PROGS or b in ("source", ".") for b in consumers):
                extra_shell_cmds.append(hd["body"])
            continue

        is_shell_heredoc = any(b in SHELL_PROGS or b in ("source", ".") for b in consumers)
        is_python_heredoc = any(re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", b) for b in consumers if b)
        is_script_heredoc = any(b in ("node", "perl", "ruby", "osascript") for b in consumers)

        def _shell_cmd_has_rm() -> bool:
            if not shell_only_cmd:
                return False
            if contains_rm_command(shell_only_cmd):
                return True
            if _split_top_level_statements(shell_only_cmd) is None:
                return bool(RM_WORD_RE.search(shell_only_cmd))
            return any(b == "rm" or b.endswith("/rm") for b, _ in _iter_unwrapped_cmds(shell_only_cmd))

        if is_python_heredoc:
            u_rm, py_cmds, fb_raw, has_sym = _inspect_python_code(
                hd["body"],
                ws_roots=ws_roots,
                external_symlink_active=cmd_has_symlink,
            )
            if u_rm:
                direct_force_ask = True
            extra_shell_cmds.extend(py_cmds)
            if fb_raw:
                raw_fallback_texts.append(hd["body"])
            if has_sym and _shell_cmd_has_rm():
                direct_force_ask = True

        if is_shell_heredoc:
            if is_python_heredoc or (owner_base not in SHELL_PROGS and owner_base not in ("cat", "tee")):
                raw_fallback_texts.append(hd["body"])
            else:
                extra_shell_cmds.append(hd["body"])
            if _fragment_creates_symlink(hd["body"]) and _shell_cmd_has_rm():
                direct_force_ask = True
        elif is_script_heredoc and not is_python_heredoc:
            script_lang = (
                "osascript" if "osascript" in consumers else (
                    "node" if "node" in consumers else (
                        "perl" if "perl" in consumers else (
                            "ruby" if "ruby" in consumers else ""
                        )
                    )
                )
            )
            if _script_stmt_has_native_del(hd["body"], script_lang):
                direct_force_ask = True
            if re.search(r"\bsymlink(?:Sync)?\b", _collapse_script_strings(hd["body"])) and _shell_cmd_has_rm():
                direct_force_ask = True
            raw_fallback_texts.append(hd["body"])

    # Inspect inline python -c '...' stages and nested shell/$(...)/<(...) fragments recursively
    replacements: list[tuple[int, int, str]] = []

    def _scan_shell_fragment(frag: str, allow_replace: bool, _depth: int = 0) -> None:
        nonlocal direct_force_ask
        for _, _, stages in _split_line_pipelines_with_spans(frag):
            stage_all_bases: set[str] = set()
            for _, _, st in stages:
                sb_set, _, _ = _extract_stage_consumer_bases_and_targets(st)
                stage_all_bases.update(sb_set)
            has_shell_peer = any(b in SHELL_PROGS or b in ("eval", "source", ".") for b in stage_all_bases)
            for s_start, s_end, s_txt in stages:
                toks = _split_tokens_with_quote_info(s_txt)
                if not toks:
                    continue
                rem = _unwrap_prefix_shell_tokens(toks)
                rem_base = os.path.basename(rem[0].value.lstrip("=$")).lower() if rem else ""
                if rem and re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", rem_base):
                    if _inspect_inline_python_tokens(
                        rem,
                        s_start if allow_replace else 0,
                        has_shell_peer,
                        extra_shell_cmds,
                        raw_fallback_texts,
                        replacements if allow_replace else None,
                        ws_roots=ws_roots,
                        raw_tok_vals=[t.value for t in toks],
                        external_symlink_active=cmd_has_symlink,
                    ):
                        direct_force_ask = True
                else:
                    if _depth < 3 and rem and rem_base in SHELL_PROGS:
                        for sc in _extract_shell_exec_inputs([t.value for t in rem[1:]]):
                            _scan_shell_fragment(sc, False, _depth + 1)
                    if "python" in s_txt.lower() or (_depth < 3 and any(b in SHELL_PROGS for b in stage_all_bases)):
                        sub_stmts = _split_top_level_statements(s_txt)
                        if sub_stmts:
                            for sub_st in sub_stmts:
                                sub_toks = _split_tokens_with_quote_info(sub_st["text"])
                                if not sub_toks:
                                    continue
                                sub_rem = _unwrap_prefix_shell_tokens(sub_toks)
                                if not sub_rem:
                                    continue
                                sub_base = os.path.basename(sub_rem[0].value.lstrip("=$")).lower()
                                if re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", sub_base):
                                    if _inspect_inline_python_tokens(
                                        sub_rem, 0, has_shell_peer, extra_shell_cmds, raw_fallback_texts, None, ws_roots=ws_roots,
                                        raw_tok_vals=[t.value for t in sub_toks],
                                        external_symlink_active=cmd_has_symlink,
                                    ):
                                        direct_force_ask = True
                                elif _depth < 3 and sub_base in SHELL_PROGS:
                                    for sc in _extract_shell_exec_inputs([t.value for t in sub_rem[1:]]):
                                        _scan_shell_fragment(sc, False, _depth + 1)
        if _depth < 3:
            frag_subs, _ = _extract_cmd_substitutions(frag)
            for sub_frag in frag_subs:
                _scan_shell_fragment(sub_frag, False, _depth + 1)

    _scan_shell_fragment(shell_only_cmd, True, 0)

    # Also extract echo/printf/<<< strings piped into simple/compound shell, python*, or script consumers
    for _, _, p_stages in _split_line_pipelines_with_spans(shell_only_cmd):
        if len(p_stages) < 2:
            continue
        p_parsed = []
        for _, _, st in p_stages:
            stoks = _split_tokens_with_quote_info(st)
            st_vals = [t.value for t in stoks] if stoks else []
            s_bases = _extract_stage_consumer_bases_and_targets(st)[0]
            p_parsed.append((_seg_command_base_and_args(st), s_bases, st_vals))
        for j in range(1, len(p_parsed)):
            (db, dargs, _), d_bases, _ = p_parsed[j]
            is_sh_c = any(b in SHELL_PROGS or b in ("eval", "source", ".") for b in d_bases)
            is_py_c = any(re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", b) for b in d_bases) and _interp_reads_code_from_stdin(db, dargs)
            is_sc_c = any(b in ("node", "perl", "ruby", "osascript") for b in d_bases) and _interp_reads_code_from_stdin(db, dargs)
            if not (is_sh_c or is_py_c or is_sc_c):
                continue
            for i in range(j):
                (ub, uargs, _), _, u_toks = p_parsed[i]
                cands: list[str] = []
                if ub in ("echo", "printf", "print"):
                    emitted = _reconstruct_echo_printf_text(ub, uargs)
                    if emitted:
                        cands.append(emitted)
                cands.extend(_extract_herestring_texts(u_toks))
                for emitted in cands:
                    if not emitted:
                        continue
                    if is_sh_c:
                        extra_shell_cmds.append(emitted)
                    elif is_py_c:
                        u_rm, py_cmds, fb_raw, has_sym = _inspect_python_code(
                            emitted,
                            ws_roots=ws_roots,
                            external_symlink_active=cmd_has_symlink,
                        )
                        if u_rm or has_sym:
                            direct_force_ask = True
                        extra_shell_cmds.extend(py_cmds)
                        if fb_raw:
                            raw_fallback_texts.append(emitted)
                    elif is_sc_c and _contains_obfuscated_rm_in_script(emitted):
                        direct_force_ask = True

    sanitized_shell = shell_only_cmd
    for r_start, r_end, repl_txt in sorted(replacements, key=lambda x: x[0], reverse=True):
        sanitized_shell = sanitized_shell[:r_start] + repl_txt + sanitized_shell[r_end:]

    return sanitized_shell, extra_shell_cmds, raw_fallback_texts, direct_force_ask


def _has_unsafe_unquoted_redirection(seg: str) -> bool:
    """Return True if seg contains any unquoted redirection other than 2>/dev/null, 2> /dev/null, 2>&1, 1>&2."""
    quote: str | None = None
    escaped = False
    i = 0
    n = len(seg)
    while i < n:
        ch = seg[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            i += 1
            continue
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == "<":
            return True
        if ch == ">":
            has_fd = i >= 1 and seg[i - 1] in ("1", "2") and (i == 1 or seg[i - 2].isspace())
            if has_fd:
                fd = seg[i - 1]
                tail = seg[i:]
                if fd == "2":
                    m = re.match(r"^>\s*/dev/null(?=$|\s)|^>&1(?=$|\s)", tail)
                    if m:
                        i += len(m.group(0))
                        continue
                elif fd == "1":
                    m = re.match(r"^>&2(?=$|\s)", tail)
                    if m:
                        i += len(m.group(0))
                        continue
            return True
        i += 1
    return False


def _unwrap_readonly_prefix(tokens: list[str]) -> list[str] | None:
    """Unwrap strictly read-only prefix wrappers (up to 4 levels), returning None if unsafe."""
    cur = _strip_leading_shell_tokens(tokens)
    for _ in range(5):
        if not cur or any(c in cur[0] for c in "*?[{}="):
            return None
        base = os.path.basename(cur[0]).lower()
        if base == "xargs":
            rem = _skip_xargs_opts(cur[1:])
            if not rem:
                return None
            cur = _strip_leading_shell_tokens(rem)
            continue
        if base not in READONLY_PREFIX_WRAPPERS:
            return cur
        args = cur[1:]
        if base == "env":
            idx = 0
            while idx < len(args):
                tok = args[idx]
                if tok == "--":
                    idx += 1
                    break
                if not tok.startswith("-") and "=" in tok:
                    idx += 1
                    continue
                if tok in ("-S", "--split-string") or tok.startswith("--split-string=") or (
                    tok.startswith("-") and not tok.startswith("--") and "S" in tok
                ):
                    return None
                if tok in ENV_VAL_OPTS:
                    idx += 2
                    continue
                if tok.startswith("-") and tok != "-":
                    idx += 1
                    continue
                break
            cur = _strip_leading_shell_tokens(args[idx:])
        else:
            rem, _, _ = _unwrap_prefix_args(base, args, 0)
            if not rem:
                return None
            cur = _strip_leading_shell_tokens(rem)
    return None


def _is_pure_readonly_search_segment(seg: str) -> bool:
    """Return True iff seg is a pure read-only search/listing command with no unsafe redirections or code exec."""
    if any(tok in seg for tok in ("`", "$(", "<(", ">(", "$'", '$"', "\n", "\r", "\x00")):
        return False
    if _has_unsafe_unquoted_redirection(seg):
        return False
    try:
        tokens = shlex.split(seg, posix=True)
    except ValueError:
        return False
    cur = _unwrap_readonly_prefix(tokens)
    if not cur or any(c in cur[0] for c in "*?[{}="):
        return False

    base = os.path.basename(cur[0]).lower()
    args = cur[1:]

    if base in READONLY_SEARCH_CMDS:
        if base in ("ag", "ack"):
            for tok in args:
                if tok == "--":
                    break
                if tok == "--pager" or tok.startswith("--pager="):
                    return False
        return True

    if base == "rg":
        for tok in args:
            if tok == "--":
                break
            if tok == "--pre" or tok.startswith("--pre="):
                return False
        return True

    if base in ("fd", "fdfind"):
        for tok in args:
            if tok == "--":
                break
            if tok in ("-x", "--exec", "-X", "--exec-batch") or tok.startswith(("--exec=", "--exec-batch=")):
                return False
            if tok.startswith("-") and not tok.startswith("--") and ("x" in tok or "X" in tok):
                return False
        return True

    if base == "find":
        idx = 0
        n_args = len(args)
        while idx < n_args:
            tok = args[idx]
            if tok in FIND_UNSAFE_FLAGS:
                return False
            if tok in ("-exec", "-execdir"):
                sub_start = idx + 1
                sub_end = _find_exec_end(args, sub_start)
                sub = args[sub_start:sub_end]
                if not sub or any(c in sub[0] for c in "*?[{}="):
                    return False
                sub_base = os.path.basename(sub[0]).lower()
                if sub_base == "rg":
                    for st in sub[1:]:
                        if st == "--":
                            break
                        if st == "--pre" or st.startswith("--pre="):
                            return False
                elif sub_base in FIND_READONLY_EXEC_CMDS:
                    if sub_base in ("ag", "ack"):
                        for st in sub[1:]:
                            if st == "--":
                                break
                            if st == "--pager" or st.startswith("--pager="):
                                return False
                else:
                    return False
                idx = sub_end + 1
                continue
            idx += 1
        return True

    if base == "git":
        idx = 0
        n_args = len(args)
        subcmd: str | None = None
        while idx < n_args:
            tok = args[idx]
            if tok == "--":
                idx += 1
                if idx < n_args:
                    subcmd = args[idx]
                break
            if not tok.startswith("-") or tok == "-":
                subcmd = tok
                break
            if tok in GIT_READONLY_GLOBAL_FLAGS:
                idx += 1
                continue
            if tok in GIT_READONLY_GLOBAL_VAL_OPTS:
                idx += 2
                continue
            if tok.startswith(("--git-dir=", "--work-tree=", "--namespace=")):
                idx += 1
                continue
            return False
        if subcmd not in GIT_READONLY_SUBCMDS:
            return False
        if subcmd == "grep":
            for st in args[idx + 1 :]:
                if st == "--":
                    break
                if st in ("-O", "--open-files-in-pager") or st.startswith(("-O", "--open-files-in-pager=")):
                    return False
        return True

    return False


def _split_pipelines(command: str) -> list[list[str]] | None:
    """Split command into pipelines (on ;, &&, ||, &, newline) and pipe stages (on |, |&). Fail-closed on subshells/proc-subst."""
    if any(tok in command for tok in ("<(", ">(")):
        return None
    command = _strip_shell_line_continuations(command)
    pipelines: list[list[str]] = []
    cur_pipeline: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    escaped = False
    i = 0
    n = len(command)

    def flush_stage() -> None:
        s = "".join(buf).strip()
        buf.clear()
        if s:
            cur_pipeline.append(s)

    def flush_pipeline() -> None:
        flush_stage()
        if cur_pipeline:
            pipelines.append(list(cur_pipeline))
            cur_pipeline.clear()

    while i < n:
        ch = command[i]
        if escaped:
            buf.append(ch)
            escaped = False
            i += 1
            continue
        if ch == "\\" and quote != "'":
            buf.append(ch)
            escaped = True
            i += 1
            continue
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "#" and (i == 0 or command[i - 1] in " \t\n;|&()"):
            flush_pipeline()
            while i < n and command[i] != "\n":
                i += 1
            if i < n and command[i] == "\n":
                i += 1
            continue
        if ch == "$" and i + 1 < n and command[i + 1] == "(":
            sub_end = _scan_cmd_subst_end(command, i)
            if sub_end < 0:
                return None
            buf.append(command[i:sub_end])
            i = sub_end
            continue
        if ch == "`":
            bt_end = _scan_backtick_end(command, i)
            if bt_end < 0:
                return None
            buf.append(command[i:bt_end])
            i = bt_end
            continue
        if ch in ("(", ")"):
            return None
        if ch == ";":
            flush_pipeline()
            i += 1
            continue
        if ch == "\n":
            flush_pipeline()
            i += 1
            continue
        if ch == "&":
            prev_ch = command[i - 1] if i > 0 else ""
            next_ch = command[i + 1] if i + 1 < n else ""
            if prev_ch in (">", "<") or next_ch == ">":
                buf.append(ch)
                i += 1
                continue
            if next_ch == "&":
                flush_pipeline()
                i += 2
                continue
            flush_pipeline()
            i += 1
            continue
        if ch == "|":
            prev_ch = command[i - 1] if i > 0 else ""
            if prev_ch == ">":
                buf.append(ch)
                i += 1
                continue
            next_ch = command[i + 1] if i + 1 < n else ""
            if next_ch == "|":
                flush_pipeline()
                i += 2
                continue
            if next_ch == "&":
                flush_stage()
                i += 2
                continue
            flush_stage()
            i += 1
            continue
        buf.append(ch)
        i += 1

    if quote is not None or escaped:
        return None
    flush_pipeline()
    return pipelines


def _build_regex_targets(command: str, _depth: int = 0) -> list[str]:
    """Return command fragments excluding pure read-only search stages while recursing into command substitutions."""
    if _depth > 4:
        return [command]
    subs, has_unclosed = _extract_cmd_substitutions(command)
    if has_unclosed:
        return [command]
    pipelines = _split_pipelines(command)
    if pipelines is None:
        return [command]
    targets: list[str] = []
    for pl in pipelines:
        rem = [st for st in pl if not _is_pure_readonly_search_segment(st)]
        if not rem:
            continue
        targets.extend(rem)
        if len(rem) > 1:
            targets.append(" | ".join(rem))
    for sub in subs:
        targets.extend(_build_regex_targets(sub, _depth + 1))
    return targets


def _fragment_creates_symlink(frag: str, _depth: int = 0) -> bool:
    """Return True if frag (or nested shells/substitutions/scripts) invokes ln or symlink creation."""
    if _depth > 3 or not frag:
        return False
    collapsed_frag = _collapse_script_strings(frag)
    if _split_top_level_statements(frag) is None:
        return bool(re.search(r"\b(?:ln|symlink(?:Sync|_to)?)\b", collapsed_frag))
    for base, args in _iter_unwrapped_cmds(frag):
        if base == "ln":
            return True
        if re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", base):
            found_c = _scan_python_cli_c_flag(args, lambda t: t)
            py_srcs: list[str] = []
            if found_c is not None:
                c_idx, c_pos = found_c
                py_srcs.append(args[c_idx][c_pos + 1 :] if c_pos >= 0 else args[c_idx])
            py_srcs.extend(_extract_herestring_texts(args))
            for ps in py_srcs:
                if _inspect_python_code(ps, _depth=min(_depth, 2))[3]:
                    return True
                if re.search(r"\bsymlink(?:_to)?\b", _collapse_script_strings(ps)):
                    return True
            if re.search(r"\bsymlink(?:_to)?\b", collapsed_frag):
                return True
        if base in ("node", "perl", "ruby", "osascript") and re.search(r"\bsymlink(?:Sync)?\b", collapsed_frag):
            return True
        if base == "node" and re.search(
            r"""(?:\bfs\b|require\s*\(\s*['"](?:node:)?fs['"]\s*\))\s*\[""",
            collapsed_frag,
        ):
            return True
        for hs in _extract_herestring_texts(args):
            if base in SHELL_PROGS and (re.search(r"\bln\b", hs) or _fragment_creates_symlink(hs, _depth + 1)):
                return True
    for _, _, stages in _split_line_pipelines_with_spans(frag):
        if len(stages) < 2:
            continue
        has_shell_consumer = any(
            b in SHELL_PROGS or b in ("eval", "source", ".")
            for _, _, st in stages
            for b in _extract_stage_consumer_bases_and_targets(st)[0]
        )
        if not has_shell_consumer:
            continue
        for _, _, st_txt in stages:
            st_toks = _split_tokens_with_quote_info(st_txt)
            if not st_toks:
                continue
            s_kept, _ = _strip_all_redirs_collect_targets(st_toks)
            s_kept = _strip_group_wrappers(s_kept)
            s_strs = _strip_leading_shell_tokens([t.value for t in s_kept])
            ub, uargs = _unwrap_runner_tokens(s_strs)
            if ub in ("echo", "printf", "print"):
                emitted = _reconstruct_echo_printf_text(ub, uargs)
                if emitted and (re.search(r"\bln\b", emitted) or _fragment_creates_symlink(emitted, _depth + 1)):
                    return True
            elif (ub == "base64" and any(a in ("-d", "-D", "--decode") for a in uargs)) or (ub == "xxd" and "-r" in uargs) or ub == "rev":
                return True
    subs, _ = _extract_cmd_substitutions(frag)
    for sub in subs:
        if _fragment_creates_symlink(sub, _depth + 1):
            return True
    return False


NPM_FORBIDDEN_SUBCMDS = frozenset({
    "install", "i", "ci", "add", "update", "up", "uninstall", "rm", "remove", "unlink", "un",
})


def _has_forbidden_npm_cmd(target: str) -> bool:
    """Detect forbidden `npm ... install/add/remove` across wrappers, `env -S`, and `npm exec -c`."""
    if "npm" not in target.lower():
        return False
    if _split_top_level_statements(target) is None:
        return bool(re.search(
            r"(^|[\s;&|('\"`])(?:/[^\s;&|'\"]*)*npm\s+(?:-\S+(?:\s+\S+)?\s+)*(install|i|ci|add|update|up|uninstall|rm|remove|unlink|un)\b",
            target,
            re.IGNORECASE,
        ))
    for base, args in _iter_unwrapped_cmds(target):
        if base != "npm":
            continue
        ni = 0
        na = len(args)
        while ni < na:
            nt = args[ni]
            if nt == "--":
                ni += 1
                break
            if nt in NPM_VAL_OPTS:
                ni += 2
                continue
            if nt.startswith("-"):
                ni += 1
                continue
            if nt.lower() in NPM_FORBIDDEN_SUBCMDS:
                return True
            break
    return False


def _has_npx_cmd(target: str) -> bool:
    """Detect direct/path-prefixed/wrapped `npx` invocations without false-positiving on commit/echo strings."""
    if "npx" not in target.lower():
        return False
    if _split_top_level_statements(target) is None:
        return bool(re.search(
            r"(^|[\s;&|('\"`])(?:/[^\s;&|'\"]*)*npx(?:\s|$)",
            target,
            re.IGNORECASE,
        ))
    return any(base == "npx" for base, _args in _iter_unwrapped_cmds(target))


def _has_remote_script_pipe_exec(command: str, _depth: int = 0) -> bool:
    """Detect curl/wget upstream of a shell interpreter across pipe stages (including compound/multiline consumers)."""
    if _depth > 3:
        return False
    for _, _, stages in _split_line_pipelines_with_spans(command):
        if len(stages) >= 2:
            stage_bases = [_extract_stage_consumer_bases_and_targets(st)[0] for _, _, st in stages]
            for i in range(len(stage_bases)):
                if "curl" in stage_bases[i] or "wget" in stage_bases[i]:
                    for j in range(i + 1, len(stage_bases)):
                        if any(b in SHELL_PROGS or b in ("eval", "source", ".") for b in stage_bases[j]):
                            return True
        for _, _, st_txt in stages:
            sb, sargs, _ = _seg_command_base_and_args(st_txt)
            if sb in SHELL_PROGS:
                for sc in _extract_shell_exec_inputs(sargs):
                    if _has_remote_script_pipe_exec(sc, _depth + 1):
                        return True
            if "(" in st_txt or "{" in st_txt or ";" in st_txt:
                _, _, inner_ents = _extract_stage_consumer_bases_and_targets(st_txt)
                for ib, iargs, _, _ in inner_ents:
                    if ib in SHELL_PROGS:
                        for sc in _extract_shell_exec_inputs(iargs):
                            if _has_remote_script_pipe_exec(sc, _depth + 1):
                                return True
    return False


def _parse_git_subcmd(args: list[str]) -> tuple[str | None, list[str]]:
    """Extract (subcommand, sub_args) after stripping git global options (-C dir, -c k=v, --git-dir=...)."""
    gi = 0
    ng = len(args)
    while gi < ng:
        gt = args[gi]
        if gt == "--":
            gi += 1
            if gi < ng:
                return args[gi], args[gi + 1 :]
            return None, []
        if not gt.startswith("-") or gt == "-":
            return gt, args[gi + 1 :]
        if gt in GIT_GLOBAL_VAL_OPTS:
            gi += 2
            continue
        if gt.startswith(("-C", "-c", "--git-dir=", "--work-tree=", "--namespace=", "--super-prefix=", "--config-env=")):
            gi += 1
            continue
        gi += 1
    return None, []


def _iter_unwrapped_cmds(target: str, _depth: int = 0):
    """Yield (base, args) for statements in target before and after package-manager unwrapping, recursing into shells."""
    if _depth > 3:
        return
    stmts = _split_top_level_statements(target)
    if stmts is None:
        return
    for st in stmts:
        toks = _split_tokens_with_quote_info(st["text"])
        if not toks:
            continue
        kept, _ = _strip_all_redirs_collect_targets(toks)
        kept = _strip_group_wrappers(kept)
        str_toks = _strip_leading_shell_tokens([t.value for t in kept])
        if not str_toks:
            continue
        base_pre, args_pre = _unwrap_runner_tokens(str_toks, unwrap_pkg=False)
        if base_pre in SHELL_PROGS:
            for sc in _extract_shell_exec_inputs(args_pre):
                yield from _iter_unwrapped_cmds(sc, _depth + 1)
            continue
        if base_pre == "eval":
            ev = list(args_pre)
            while ev and ev[0] == "--":
                ev.pop(0)
            if ev:
                yield from _iter_unwrapped_cmds(" ".join(ev), _depth + 1)
            continue
        if base_pre == "npm":
            ni = 0
            npm_val_opts = NPM_VAL_OPTS | PNPM_WRAPPER_VAL_OPTS
            while ni < len(args_pre):
                if args_pre[ni] in npm_val_opts:
                    ni += 2
                    continue
                if args_pre[ni].startswith("-"):
                    ni += 1
                    continue
                break
            if ni < len(args_pre) and args_pre[ni] in ("exec", "x"):
                ci = ni + 1
                while ci < len(args_pre):
                    if args_pre[ci] == "--":
                        break
                    if args_pre[ci] in ("-c", "--call") and ci + 1 < len(args_pre):
                        yield from _iter_unwrapped_cmds(args_pre[ci + 1], _depth + 1)
                        ci += 2
                        continue
                    if args_pre[ci].startswith("--call="):
                        yield from _iter_unwrapped_cmds(args_pre[ci].split("=", 1)[1], _depth + 1)
                    ci += 1
        if base_pre:
            yield base_pre, args_pre
        base_post, args_post = _unwrap_runner_tokens(str_toks, unwrap_pkg=True)
        if base_post and (base_post != base_pre or args_post != args_pre):
            if base_post in SHELL_PROGS:
                for sc in _extract_shell_exec_inputs(args_post):
                    yield from _iter_unwrapped_cmds(sc, _depth + 1)
            else:
                yield base_post, args_post


PIP_GLOBAL_VAL_OPTS = frozenset({
    "--python", "--timeout", "--retries", "--proxy", "--cert", "--client-cert",
    "--cache-dir", "--log", "--exists-action", "--use-feature", "--use-deprecated",
    "--keyring-provider", "--root-user-action", "-C",
})
FIND_VAL_PREDS = frozenset({
    "-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename",
    "-regex", "-iregex", "-lname", "-ilname", "-samefile", "-newer", "-anewer", "-cnewer",
    "-fstype", "-type", "-xtype", "-perm", "-user", "-group", "-uid", "-gid",
    "-links", "-size", "-atime", "-amin", "-ctime", "-cmin", "-mtime", "-mmin",
    "-used", "-maxdepth", "-mindepth", "-fprintf", "-fprint", "-fprint0", "-fls",
    "-printf", "-D", "-f",
})


def _has_forbidden_pip_install(target: str) -> bool:
    """Detect pip/pip3 install even when global flags like --no-cache-dir precede `install`."""
    if "pip" not in target.lower():
        return False
    if _split_top_level_statements(target) is None:
        return bool(re.search(r"(^|[\s;&|('\"`])pip\d*(?:\.\d+)*\b[^\n;|&]*\binstall\b", target, re.IGNORECASE))
    for base, args in _iter_unwrapped_cmds(target):
        if not re.fullmatch(r"pip\d*(?:\.\d+)*", base):
            continue
        pi = 0
        while pi < len(args):
            a = args[pi]
            if a == "--":
                pi += 1
                break
            if a in PIP_GLOBAL_VAL_OPTS:
                pi += 2
                continue
            if a.startswith("-"):
                pi += 1
                continue
            if a.lower() == "install":
                return True
            break
    return False


def _has_latest_pkg_spec(target: str) -> bool:
    """Detect @latest package specifier in npm/pnpm/yarn/bun/uv/pipx even when quoted tokens contain `;`."""
    if "@latest" not in target.lower():
        return False
    if _split_top_level_statements(target) is None:
        return bool(re.search(
            r"(^|[\s;&|('\"`])(npm|pnpm|pnpx|npx|yarn|bun|bunx|uv|uvx|pipx)\b[^\n|;&]*@latest\b",
            target,
            re.IGNORECASE,
        ))
    for base, args in _iter_unwrapped_cmds(target):
        if base in ("npm", "pnpm", "pnpx", "npx", "yarn", "bun", "bunx", "uv", "uvx", "pipx"):
            if any(re.search(r"@latest(?=$|[^A-Za-z0-9_-])", a, re.IGNORECASE) for a in args):
                return True
    return False


def _has_find_delete(target: str) -> bool:
    """Detect `find ... -delete` safely across quoted predicates like `-name 'a;b'` without false-matching `-name -delete`."""
    if "find" not in target.lower() or "-delete" not in target.lower():
        return False
    if _split_top_level_statements(target) is None:
        return bool(re.search(r"\bfind\b[^;&|\n]*\s-delete\b", target, re.IGNORECASE))
    for base, args in _iter_unwrapped_cmds(target):
        if base != "find":
            continue
        fi = 0
        na = len(args)
        while fi < na:
            a = args[fi]
            if a in ("-exec", "-execdir", "-ok", "-okdir"):
                fi = _find_exec_end(args, fi + 1) + 1
                continue
            if a in FIND_VAL_PREDS:
                fi += 2
                continue
            if a.lower() == "-delete":
                return True
            fi += 1
    return False


def _has_destructive_git_cmd(target: str) -> bool:
    """Detect git reset --hard, clean -f/--force, push -f/--force, stash drop/clear with any global flags (-C/-c)."""
    if "git" not in target.lower():
        return False
    if _split_top_level_statements(target) is None:
        return any(
            re.search(p, target, re.IGNORECASE)
            for p in (
                r"git\s+reset\s+--hard",
                r"git\s+clean\b[^|;]*\s-\w*f",
                r"git\s+push\b[^|;]*(--force(\s|$)|\s-f(\s|$))",
                r"git\s+stash\s+(drop|clear)\b",
            )
        )
    for base, args in _iter_unwrapped_cmds(target):
        if base != "git":
            continue
        subcmd, sub_args = _parse_git_subcmd(args)
        if not subcmd:
            continue
        subcmd_l = subcmd.lower()
        if subcmd_l == "reset":
            for a in sub_args:
                if a == "--":
                    break
                if a == "--hard":
                    return True
        elif subcmd_l == "clean":
            ci = 0
            while ci < len(sub_args):
                a = sub_args[ci]
                if a == "--":
                    break
                if a in ("-e", "--exclude"):
                    ci += 2
                    continue
                if a == "--force" or (a.startswith("-") and not a.startswith("--") and "f" in a[1:]):
                    return True
                ci += 1
        elif subcmd_l == "push":
            pi = 0
            while pi < len(sub_args):
                a = sub_args[pi]
                if a == "--":
                    break
                if a in ("-o", "--push-option", "--repo", "--receive-pack", "--exec"):
                    pi += 2
                    continue
                if a == "--force" or (a.startswith("-") and not a.startswith("--") and "f" in a[1:]):
                    return True
                pi += 1
        elif subcmd_l == "stash":
            si = 0
            while si < len(sub_args):
                a = sub_args[si]
                if a == "--":
                    break
                if a in ("-m", "--message", "-u", "--include-untracked"):
                    si += 2 if a in ("-m", "--message") else 1
                    continue
                if a.startswith("-"):
                    si += 1
                    continue
                if a.lower() in ("drop", "clear"):
                    return True
                break
    return False


def _has_git_discard_cmd(target: str) -> bool:
    """Detect git checkout -- <path>, git restore, and git branch -D (including git -C/-c global flags)."""
    if "git" not in target.lower():
        return False
    if _split_top_level_statements(target) is None:
        return any(
            re.search(p, target, re.IGNORECASE)
            for p in (
                r"git\s+checkout\s+--\s",
                r"git\s+restore\b",
                r"\bgit\b[^\n;|&]*\bbranch\b[^\n;|&]*(?:\s-D\b|\s-[A-Za-z]*D[A-Za-z]*\b)",
            )
        )
    for base, args in _iter_unwrapped_cmds(target):
        if base != "git":
            continue
        subcmd, sub_args = _parse_git_subcmd(args)
        if not subcmd:
            continue
        subcmd_l = subcmd.lower()
        if subcmd_l == "restore":
            if not any(a in ("-h", "--help") for a in sub_args):
                return True
        elif subcmd_l == "checkout":
            if "--" in sub_args:
                idx_dd = sub_args.index("--")
                if idx_dd + 1 < len(sub_args) and not any(
                    a in ("-b", "-B", "--orphan", "-h", "--help") for a in sub_args[:idx_dd]
                ):
                    return True
        elif subcmd_l == "branch":
            has_force_d = False
            has_delete = False
            has_force = False
            for a in sub_args:
                if a == "--":
                    break
                if not a.startswith("-") or a == "-":
                    continue
                if a == "-D":
                    has_force_d = True
                    break
                if a.startswith("-") and not a.startswith("--"):
                    if "D" in a[1:]:
                        has_force_d = True
                        break
                    if "d" in a[1:]:
                        has_delete = True
                    if "f" in a[1:]:
                        has_force = True
                elif a == "--delete":
                    has_delete = True
                elif a == "--force":
                    has_force = True
            if has_force_d or (has_delete and has_force):
                return True
    return False


try:
    payload = json.load(sys.stdin)
    args_obj = payload.get("toolCall", {}).get("args") or {}
    cmd = str(args_obj.get("CommandLine", ""))
    raw_daemon = args_obj.get("IsDaemon")
    is_daemon = raw_daemon is True or str(raw_daemon).strip().lower() == "true"
    raw_ws_paths = payload.get("workspacePaths")
    ws_roots = normalize_safe_workspace_roots(raw_ws_paths)
    initial_cwd = str(payload.get("cwd") or "")
except Exception:
    emit({"decision": "allow"})

if not cmd.strip():
    emit({"decision": "allow"})

sanitized_shell, extra_shell_cmds, raw_fallback_texts, direct_force_ask = _analyze_command_inputs(cmd, ws_roots=ws_roots)

hang_reason = detect_noninteractive_hang(sanitized_shell, is_daemon)
if not hang_reason:
    for sc in extra_shell_cmds:
        hang_reason = detect_noninteractive_hang(sc, is_daemon)
        if hang_reason:
            break
if hang_reason:
    emit({"decision": "deny", "reason": hang_reason})


# --- deny: 禁止事項。reason には代替となる最適解を必ず示す ---
# 先頭境界に引用符・バッククォートを含める (sh -c 'npm install ...' 等のシェル間接実行も検出する)
B = r"(^|[\s;&|('\"`])"
REMOTE_PIPE_DENY_MSG = (
    "リモートスクリプトのパイプ実行は禁止です。最適解: ファイルに保存し内容を確認した上で、実行をユーザーに提案する。"
)
PIP_DENY_MSG = (
    "pip install は禁止です。最適解: uv を使う (`uv add <pkg>==<x.y.z>` / `uv sync`)。既存リポジトリが requirements.txt 運用の場合は `uv pip` またはユーザー確認を行ってください。"
)
LATEST_DENY_MSG = (
    "@latest 等の非固定バージョン指定は禁止です。最適解: バージョンを明示的に固定して指定する。"
)
DENY = [
    (B + r"npm\s+(install|i|ci|add|update|up|uninstall|rm)\b",
     "npm の直接実行は禁止です。最適解: pnpm を使う (`pnpm add <pkg>@<x.y.z>` / `pnpm install --frozen-lockfile`)。既存リポジトリが package-lock.json 運用の場合はユーザーに確認してください。"),
    (r"\s--no-verify\b",
     "--no-verify によるフック回避は禁止です。最適解: フックが失敗する真因を修正してから再実行する。"),
    (B + r"(curl|wget)\b[^|;]*\|\s*(sh|bash|zsh)\b",
     REMOTE_PIPE_DENY_MSG),
    (B + r"(crontab|launchctl|schtasks)\b",
     "ホストOSスケジューラの直接操作は禁止です (GEMINI.md §2.1)。必要ならユーザーに手動実行を依頼してください。"),
]
regex_targets = _build_regex_targets(sanitized_shell)
for sc in extra_shell_cmds:
    regex_targets.extend(_build_regex_targets(sc))
regex_targets.extend(raw_fallback_texts)

if _has_remote_script_pipe_exec(sanitized_shell) or any(
    _has_remote_script_pipe_exec(sc) for sc in extra_shell_cmds
):
    emit({"decision": "deny", "reason": REMOTE_PIPE_DENY_MSG})

for pattern, reason in DENY:
    if any(re.search(pattern, target, re.IGNORECASE) for target in regex_targets):
        emit({"decision": "deny", "reason": reason})

if any(_has_forbidden_npm_cmd(target) for target in regex_targets):
    emit({"decision": "deny", "reason": DENY[0][1]})
if any(_has_forbidden_pip_install(target) for target in regex_targets):
    emit({"decision": "deny", "reason": PIP_DENY_MSG})
if any(_has_latest_pkg_spec(target) for target in regex_targets):
    emit({"decision": "deny", "reason": LATEST_DENY_MSG})

# --- 非ブロッキング deny ([DEFER_HUMAN_GATE]): UI停止させずスキップ＋後回し Human Gate へ集約 ---
# rm は安全な /tmp・$TMPDIR・mktemp・scratch・アクティブ workspacePaths 配下のみ allow、それ以外は即 deny ([DEFER_HUMAN_GATE])
# ユーザーが前ターン Human Gate (pending_human_review) に対して次ターンで明示承認した場合に限り Gemini Decision 1 で検証通過させる。
def _emit_defer_or_human_gate(reason: str, *, allow_human_gate: bool = True) -> None:
    if allow_human_gate and not direct_force_ask and not raw_fallback_texts:
        from _human_gate_decision import evaluate_human_gate_override

        cat_parts = [cmd]
        if sanitized_shell and sanitized_shell != cmd:
            cat_parts.append(sanitized_shell)
        cat_parts.extend(extra_shell_cmds)
        cat_parts.extend(raw_fallback_texts)
        override = evaluate_human_gate_override(
            payload,
            "run_command",
            cmd,
            reason,
            catastrophic_check_text="\n".join(cat_parts),
        )
        if override is not None:
            emit(override)
    emit({"decision": "deny", "reason": reason})


DEFER_RM_DENY_MSG = (
    "[DEFER_HUMAN_GATE] ワークスペース外・保護対象（.git/.env/ルート等）・または安全性未確認の削除操作(rm)のため自動実行を拒否しました。"
    "この箇所で停止・回避工作せず、この削除操作はスキップして残りのゴール作業を完了し、"
    "最終報告の「Human Gate（手動確認・実行依頼）」セクションに本コマンド・対象・理由をまとめて提示してください（pending_human_review）。"
    "ユーザーが次ターンで実行を明示承認した場合は再実行して構いません（Human Gate フックが承認を自動検証します）。"
)
if direct_force_ask or any(RM_WORD_RE.search(rf) for rf in raw_fallback_texts):
    _emit_defer_or_human_gate(DEFER_RM_DENY_MSG, allow_human_gate=False)
if contains_rm_command(sanitized_shell) and not all_rm_invocations_are_safe(
    sanitized_shell, ws_roots=ws_roots, initial_cwd=initial_cwd
):
    _emit_defer_or_human_gate(DEFER_RM_DENY_MSG)
for sc in extra_shell_cmds:
    if contains_rm_command(sc) and not all_rm_invocations_are_safe(
        sc, ws_roots=ws_roots, initial_cwd=""
    ):
        _emit_defer_or_human_gate(DEFER_RM_DENY_MSG)

DEFER_DESTRUCTIVE_DENY_MSG = (
    "[DEFER_HUMAN_GATE] 不可逆・破壊的な操作（find -delete / git reset --hard / git clean -f / git push --force / truncate 等）のため自動実行を拒否しました。"
    "ここで停止・回避工作せず、この操作はスキップ（または非破壊な代替手段を優先）して残りのゴール作業を完了し、"
    "最終報告の「Human Gate（手動確認・実行依頼）」セクションに実行コマンド・対象・理由をまとめて提示してください（pending_human_review）。"
    "ユーザーが次ターンで実行を明示承認した場合は再実行して構いません（Human Gate フックが承認を自動検証します）。"
)
def _has_unbypassable_disk_cmd(target: str) -> bool:
    norm_t = re.sub(r"\\\r?\n", "", target)
    if not any(kw in norm_t.lower() for kw in ("diskutil", "mkfs", "dd")):
        return False
    if _split_top_level_statements(norm_t) is None:
        return any(
            re.search(p, norm_t, re.IGNORECASE)
            for p in (
                B + r"diskutil\s+(?:erase\w*|reformat\w*|apfs\s+(?:deleteVolume|deleteContainer|eraseVolume))\b",
                B + r"(?:/[A-Za-z0-9._/-]*)?mkfs(?:\.[A-Za-z0-9_+-]+)?\b",
                B + r"dd\b[^;|&]*\bof\s*=\s*/dev/(?!null\b|stdout\b|stderr\b)",
            )
        )
    for base, args in _iter_unwrapped_cmds(norm_t):
        bl = base.lower()
        if bl == "mkfs" or bl.startswith("mkfs."):
            return True
        if bl == "dd":
            for a in args:
                al = a.lower()
                if (al.startswith("of=/dev/") or al == "of=/dev") and al not in (
                    "of=/dev/null",
                    "of=/dev/stdout",
                    "of=/dev/stderr",
                ):
                    return True
        elif bl == "diskutil":
            non_flag = [a.lower() for a in args if not a.startswith("-")]
            if non_flag:
                sub = non_flag[0]
                if sub.startswith(("erase", "reformat")):
                    return True
                if sub == "apfs" and len(non_flag) >= 2 and non_flag[1] in (
                    "deletevolume",
                    "deletecontainer",
                    "erasevolume",
                ):
                    return True
    return False


FORCE_ASK = [
    B + r"truncate\b",
]
if any(_has_unbypassable_disk_cmd(target) for target in regex_targets):
    _emit_defer_or_human_gate(DEFER_DESTRUCTIVE_DENY_MSG, allow_human_gate=False)
for pattern in FORCE_ASK:
    if any(re.search(pattern, target, re.IGNORECASE) for target in regex_targets):
        _emit_defer_or_human_gate(DEFER_DESTRUCTIVE_DENY_MSG)
if any(
    _has_find_delete(target) or _has_destructive_git_cmd(target)
    for target in regex_targets
):
    _emit_defer_or_human_gate(DEFER_DESTRUCTIVE_DENY_MSG)

DEFER_NPX_DENY_MSG = (
    "[DEFER_HUMAN_GATE] 未検証パッケージを実行しうる npx の直接実行を拒否しました。"
    "最適解: 既存リポジトリの `pnpm exec` またはバージョン固定の `pnpm dlx <pkg>@<version>` を優先してください。"
    "代替不能な場合のみここで停止せずスキップし、最終報告の「Human Gate（手動確認・実行依頼）」へ提示してください（pending_human_review）。"
    "ユーザーが次ターンで実行を明示承認した場合は再実行して構いません（Human Gate フックが承認を自動検証します）。"
)
DEFER_GIT_DISCARD_DENY_MSG = (
    "[DEFER_HUMAN_GATE] 作業内容や履歴を破棄しうる Git 操作（git checkout -- / git restore / git branch -D）のため自動実行を拒否しました。"
    "ファイル修正は `edit_file`、マージ済みブランチ削除は小文字 `git branch -d` を優先してください。"
    "本操作が必要な場合はここで停止せずスキップして残りのゴール作業を完了し、最終報告の「Human Gate（手動確認・実行依頼）」へ提示してください（pending_human_review）。"
    "ユーザーが次ターンで実行を明示承認した場合は再実行して構いません（Human Gate フックが承認を自動検証します）。"
)
if any(_has_npx_cmd(target) for target in regex_targets):
    _emit_defer_or_human_gate(DEFER_NPX_DENY_MSG)
if any(_has_git_discard_cmd(target) for target in regex_targets):
    _emit_defer_or_human_gate(DEFER_GIT_DISCARD_DENY_MSG)

emit({"decision": "allow"})
