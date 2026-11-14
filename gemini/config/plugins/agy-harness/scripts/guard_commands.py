#!/usr/bin/env python3
# agy-harness: run_command ガード (PreToolUse)
# 禁止コマンドを deny (代替の最適解を提示)、破壊的コマンドを force_ask / ask に落とす。
# 該当なしは allow を返す（/tmp または /private/tmp 配下の安全な具体パスのみを対象とする rm は allow）。
import fnmatch
import json
import os
import re
import shlex
import sys

FORBIDDEN_TMP_CHARS = frozenset("$`\n\r\x00~*?[]{}()\\|;&<>")
RM_WORD_RE = re.compile(
    r"""(^|[\s;&|('"`{/\\=!$])\\?['"]?r['"\\]*m['"]?(?=\s|$|[;&|)'"`},])""",
    re.IGNORECASE,
)
SHELL_PREFIX_KEYWORDS = frozenset({
    "!", "{", "}", "if", "then", "elif", "else", "fi",
    "while", "until", "do", "done", "case", "esac",
    "coproc", "noglob", "nocorrect", "time",
})
REDIR_ONLY_RE = re.compile(r"^\d*([<>]{1,2}|>&|<&|&>>?|<>)$")
REDIR_INLINE_RE = re.compile(r"^\d*([<>]{1,2}|>&|<&|&>>?|<>).+")
WRAPPER_PROGS = frozenset({
    "sudo", "env", "command", "builtin", "nohup", "nice", "time", "stdbuf", "arch", "doas",
    "sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish",
    "eval", "exec", "xargs", "find", "parallel", "watch",
    "python", "python3", "node", "perl", "ruby",
})
ALLOWED_RM_LONG_OPTS = frozenset({"--recursive", "--force", "--dir", "--verbose"})
ALLOWED_MKDIR_LONG_OPTS = frozenset({"--parents", "--verbose"})


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.exit(0)


def _split_shell_ops(command: str, delimiters: str = ";|&\n()") -> list[str] | None:
    """Split shell command on unquoted delimiters (preserving >&, <&, &> redirections). Returns None on unbalanced quotes."""
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    escaped = False
    n = len(command)
    for idx, ch in enumerate(command):
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
        if ch == "&" and "&" in delimiters:
            prev_ch = command[idx - 1] if idx > 0 else ""
            next_ch = command[idx + 1] if idx + 1 < n else ""
            if prev_ch in (">", "<") or next_ch == ">":
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


def _split_and_only(command: str) -> list[str] | None:
    """Split strictly on unquoted '&&'. Rejects ';', '|', single '&', '<', '>', '(', ')', newlines, or unbalanced quotes."""
    if any(tok in command for tok in ("`", "$(", "\n", "\r", "\x00")):
        return None
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    escaped = False
    i = 0
    n = len(command)
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
        if ch == "&":
            if i + 1 < n and command[i + 1] == "&" and (i + 2 >= n or command[i + 2] != "&"):
                seg = "".join(buf).strip()
                if not seg:
                    return None
                segments.append(seg)
                buf = []
                i += 2
                continue
            return None
        if ch in (";", "|", "<", ">", "(", ")"):
            return None
        buf.append(ch)
        i += 1
    if quote is not None or escaped:
        return None
    tail = "".join(buf).strip()
    if not tail:
        return None
    segments.append(tail)
    return segments


def _is_safe_tmp_target(raw_path: str) -> bool:
    """Allow only concrete non-symlink paths strictly inside /tmp/<name> or /private/tmp/<name>."""
    if not raw_path or any(c in FORBIDDEN_TMP_CHARS for c in raw_path):
        return False
    if not (raw_path.startswith("/tmp/") or raw_path.startswith("/private/tmp/")):
        return False
    parts = [p for p in raw_path.split("/") if p != ""]
    if "." in parts or ".." in parts:
        return False
    if parts[0] == "tmp":
        if len(parts) < 2:
            return False
        root_prefix = "/tmp"
        sub_parts = parts[1:]
    elif len(parts) >= 3 and parts[0] == "private" and parts[1] == "tmp":
        root_prefix = "/private/tmp"
        sub_parts = parts[2:]
    else:
        return False

    tmp_root_real = os.path.realpath("/tmp")
    deepest_existing = root_prefix
    cur = root_prefix
    for part in sub_parts:
        cur = cur + "/" + part
        if os.path.islink(cur):
            return False
        if os.path.exists(cur):
            deepest_existing = cur

    if os.path.exists(deepest_existing):
        real_anc = os.path.realpath(deepest_existing)
        if real_anc != tmp_root_real and not real_anc.startswith(tmp_root_real + os.sep):
            return False
    return True


def _is_rm_cmd_word(word: str) -> bool:
    cleaned = word.lstrip("=$")
    cleaned_low = cleaned.lower()
    if "{" in cleaned_low and re.search(r"(^|/)?\{([^}]*,)?rm(,|\})", cleaned_low):
        return True
    base = os.path.basename(cleaned_low)
    if base == "rm":
        return True
    if any(c in base for c in "*?[") and fnmatch.fnmatchcase("rm", base):
        return True
    return False


def contains_rm_command(command: str) -> bool:
    """Detect any direct or indirect rm invocation (including !/then/do/redir prefixes, =rm, $'rm', {rm,...}, /bin/r?)."""
    if "`" in command or "$(" in command or "$'" in command or '$"' in command:
        if RM_WORD_RE.search(command):
            return True
    segments = _split_shell_ops(command)
    if segments is None:
        return bool(RM_WORD_RE.search(command))

    for seg in segments:
        try:
            tokens = shlex.split(seg, posix=True)
        except ValueError:
            if RM_WORD_RE.search(seg):
                return True
            continue
        while tokens:
            t0 = tokens[0]
            t0_low = t0.lower()
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t0) or t0_low in SHELL_PREFIX_KEYWORDS:
                tokens.pop(0)
                continue
            if REDIR_ONLY_RE.match(t0):
                tokens.pop(0)
                if tokens:
                    tokens.pop(0)
                continue
            if REDIR_INLINE_RE.match(t0):
                tokens.pop(0)
                continue
            break
        if not tokens:
            continue
        if _is_rm_cmd_word(tokens[0]):
            return True
        base = os.path.basename(tokens[0].lstrip("=$")).lower()
        if base in WRAPPER_PROGS or re.match(r"^python\d*(\.\d+)?$", base):
            if any(_is_rm_cmd_word(t) or RM_WORD_RE.search(t) for t in tokens[1:]):
                return True
        if base == "git" and any(t in ("-c", "--exec") or t.startswith("--exec=") for t in tokens[1:]):
            if any(RM_WORD_RE.search(t) for t in tokens[1:]):
                return True
    return False


def is_safe_tmp_only_rm(command: str) -> bool:
    """Return True iff command consists strictly of safe /tmp rm (and optional /tmp mkdir) segments joined only by &&."""
    segments = _split_and_only(command)
    if not segments:
        return False

    rm_segments = 0
    for seg in segments:
        first_raw = seg.split(None, 1)[0]
        if any(c in first_raw for c in ("'", '"', "\\")):
            return False
        try:
            tokens = shlex.split(seg, posix=True)
        except ValueError:
            return False
        if not tokens:
            return False

        prog = tokens[0]
        if prog in ("rm", "/bin/rm"):
            rm_segments += 1
            operands: list[str] = []
            end_of_opts = False
            for tok in tokens[1:]:
                if not end_of_opts and tok == "--":
                    end_of_opts = True
                    continue
                if not end_of_opts and tok.startswith("-"):
                    if re.fullmatch(r"-[rRfvd]+", tok) or tok in ALLOWED_RM_LONG_OPTS:
                        continue
                    return False
                operands.append(tok)
            if not operands or not all(_is_safe_tmp_target(op) for op in operands):
                return False
        elif prog in ("mkdir", "/bin/mkdir"):
            operands = []
            end_of_opts = False
            for tok in tokens[1:]:
                if not end_of_opts and tok == "--":
                    end_of_opts = True
                    continue
                if not end_of_opts and tok.startswith("-"):
                    if re.fullmatch(r"-[pv]+", tok) or tok in ALLOWED_MKDIR_LONG_OPTS:
                        continue
                    return False
                operands.append(tok)
            if not operands or not all(_is_safe_tmp_target(op) for op in operands):
                return False
        else:
            return False

    return rm_segments > 0


try:
    payload = json.load(sys.stdin)
    cmd = str(payload.get("toolCall", {}).get("args", {}).get("CommandLine", ""))
except Exception:
    emit({"decision": "allow"})

if not cmd.strip():
    emit({"decision": "allow"})

# --- deny: 禁止事項。reason には代替となる最適解を必ず示す ---
# 先頭境界に引用符・バッククォートを含める (sh -c 'npm install ...' 等のシェル間接実行も検出する)
B = r"(^|[\s;&|('\"`])"
DENY = [
    (B + r"npm\s+(install|i|ci|add|update|up|uninstall|rm)\b",
     "npm の直接実行は禁止です。最適解: pnpm を使う (`pnpm add <pkg>@<x.y.z>` / `pnpm install --frozen-lockfile`)。既存リポジトリが package-lock.json 運用の場合はユーザーに確認してください。"),
    (B + r"pip3?\s+install\b",
     "pip install は禁止です。最適解: uv を使う (`uv add <pkg>==<x.y.z>` / `uv sync`)。既存リポジトリが requirements.txt 運用の場合は `uv pip` またはユーザー確認を行ってください。"),
    (B + r"(npm|pnpm|pnpx|npx|yarn|bun|uv|uvx|pipx)\b[^|;&]*@latest\b",
     "@latest 等の非固定バージョン指定は禁止です。最適解: バージョンを明示的に固定して指定する。"),
    (r"\s--no-verify\b",
     "--no-verify によるフック回避は禁止です。最適解: フックが失敗する真因を修正してから再実行する。"),
    (B + r"(curl|wget)\b[^|;]*\|\s*(sh|bash|zsh)\b",
     "リモートスクリプトのパイプ実行は禁止です。最適解: ファイルに保存し内容を確認した上で、実行をユーザーに提案する。"),
    (B + r"(crontab|launchctl|schtasks)\b",
     "ホストOSスケジューラの直接操作は禁止です (GEMINI.md §2.1)。必要ならユーザーに手動実行を依頼してください。"),
]
for pattern, reason in DENY:
    if re.search(pattern, cmd, re.IGNORECASE):
        emit({"decision": "deny", "reason": reason})

# --- force_ask: 不可逆・破壊的。キャッシュ済み許可を無視して必ず確認 ---
# rm は全形態 (/bin/rm, \rm, "rm", フラグなし rm 含む) を検出し、安全な /tmp 限定指定のみ allow する
if contains_rm_command(cmd):
    if not is_safe_tmp_only_rm(cmd):
        emit({"decision": "force_ask",
              "reason": "不可逆または破壊的な削除操作の可能性があります。対象・影響範囲・復元手段を確認してください。"})

FORCE_ASK = [
    r"\bfind\b[^;&|\n]*\s-delete\b",
    r"git\s+reset\s+--hard",
    r"git\s+clean\b[^|;]*\s-\w*f",
    r"git\s+push\b[^|;]*(--force(\s|$)|\s-f(\s|$))",
    r"git\s+stash\s+(drop|clear)\b",
    B + r"truncate\b",
    B + r"diskutil\s+(erase|reformat)",
]
for pattern in FORCE_ASK:
    if re.search(pattern, cmd, re.IGNORECASE):
        emit({"decision": "force_ask",
              "reason": "不可逆または破壊的な操作の可能性があります。対象・影響範囲・復元手段を確認してください。"})

# --- ask: 要確認 (Always Allow キャッシュは尊重) ---
ASK = [
    B + r"npx\s",
    r"git\s+checkout\s+--\s",
    r"git\s+restore\b",
    r"git\s+branch\s+-D\b",
]
for pattern in ASK:
    if re.search(pattern, cmd, re.IGNORECASE):
        emit({"decision": "ask",
              "reason": "未検証パッケージの実行、または作業内容を破棄しうる操作です。意図どおりか確認してください。"})

emit({"decision": "allow"})
