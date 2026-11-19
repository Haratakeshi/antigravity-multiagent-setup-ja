#!/usr/bin/env node

/**
 * Antigravity Smart Process Review Gate (Stop Hook — v1.5.2)
 *
 * Enforces §3.5 Smart Exit Gate without sacrificing Tier 0 speed:
 * 1. Instant allow (<5ms) on non-stop events, `fullyIdle === false`, `AGY_SKIP_PROCESS_REVIEW_GATE=1`,
 *    subagent transcripts (`lastUserIdx < 0`, regardless of `CHECKPOINT`), Q&A/read-only turns,
 *    or `pending_human_review` / `status: unverified` escalations (restoring truncated steps from `transcript_full.jsonl`).
 * 2. Evaluates ONLY the unreviewed delta after the last genuine audit dispatch in the current turn
 *    (supporting both `invoke_subagent` and read-only `workspace: "inherit"` Swarm audit panels,
 *    excluding advisory-only `senior-architect-advisor` / `opus-advisor` and `--check` probes, and preserving
 *    any same-step parallel mutations so `[invoke implementer, invoke auditor]` never skips review).
 * 3. Works both with an active workspace (`workspacePaths[0]`) and in cross-root / workspace-less
 *    Orchestrator sessions (including Unicode/Japanese paths) by resolving per-file and per-prompt git roots,
 *    AND catches changes committed within the same unreviewed window (`git commit` before stop).
 * 4. Detects newly introduced single- and multi-line empty `catch {}` via per-hunk `-U3` old-vs-new
 *    comparison + edit-pair comparison (catching split closing braces, deleted catch bodies, JSX `</tag>` pairs,
 *    same-line contractions like `Don't`, and `${...}` template expressions while ignoring untouched legacy code).
 * 5. Requires Tier 1+ clean-room refutation review ONLY when:
 *    - `flash-implementer` / `self` delegated implementation ran (dirty or committed in-turn logic), OR
 *    - `>= 2` logic code files were modified, OR
 *    - `1` logic code file had `>= 25` added/modified lines (or pathless `apply_patch` / `apply_diff` Fail-Closed).
 */

import { execFileSync } from 'child_process';
import { readFileSync, writeFileSync, mkdirSync, existsSync, realpathSync, readdirSync, lstatSync } from 'fs';
import { homedir, tmpdir } from 'os';
import { basename, dirname, join, relative, resolve, sep, isAbsolute } from 'path';

const LINE_THRESHOLD = 25;
const FILE_THRESHOLD = 2;
const MAX_UNTRACKED_WALK_ENTRIES = 400;
const MAX_UNTRACKED_WALK_DEPTH = 6;
const MAX_LCS_CELLS = 40000;

const MUTATION_TOOLS = new Set([
  'write_file',
  'write_to_file',
  'create_file',
  'edit_file',
  'replace_file_content',
  'multi_replace_file_content',
  'multi_edit',
  'apply_diff',
  'apply_patch',
  'delete_file',
  'remove_file',
]);

const EDIT_DELTA_TOOLS = new Set([
  'edit_file',
  'replace_file_content',
  'multi_replace_file_content',
  'multi_edit',
]);

const PATCH_TOOLS = new Set(['apply_diff', 'apply_patch']);
const DELEGATED_MUTATION_SUBAGENTS = new Set(['flash-implementer', 'self']);

// Post-implementation diff/code auditors ONLY (excludes advisory `senior-architect-advisor`)
const AUDIT_SUBAGENTS = new Set([
  'carbon-verifier',
  'clean-room-auditor',
  'multi-agent-opinion',
  'second-opinion-lite',
  'mindfulness-inspector',
  'zero-bias-reviewer',
  'domain-logic-reviewer',
  'security-vulnerability-reviewer',
  'architecture-invariants-reviewer',
  'contract-boundary-reviewer',
  'cross-domain-combinatorial-auditor',
  'data-migration-gap-reviewer',
  'lifecycle-clock-reviewer',
  'performance-efficiency-reviewer',
  'qa-adversarial-reviewer',
  'resilience-operations-reviewer',
  'ssot-doc-sync-reviewer',
  'supply-chain-reviewer',
  'system-architecture-coherence-auditor',
  'test-integrity-reviewer',
  'ui-state-parity-reviewer',
  'usability-accessibility-reviewer',
  'visual-verification-reviewer',
  'web-production-readiness-reviewer',
  'stripe-billing-readiness-reviewer',
]);

const SWARM_AUDIT_NAME_RE =
  /(?:^|[-_])(review|reviewer|audit|auditor|verify|verifier|refute|refuter|inspector|critic|panel)(?:[-_0-9]|$)/i;
const SWARM_NON_AUDIT_NAME_RE =
  /(?:^|[-_])(advisor|monitor|narrator|author|implementer|editor|writer|fixer|coder|builder|developer)(?:[-_0-9]|$)/i;

const ESCALATION_MARKERS = ['pending_human_review', 'status: unverified'];

const PATH_ARG_KEYS = [
  'Path',
  'TargetFile',
  'AbsolutePath',
  'FilePath',
  'TargetPath',
  'path',
  'file_path',
];

const PATCH_BODY_KEYS = ['Patch', 'Diff', 'patch', 'diff', 'Content', 'CodeContent', 'content', 'input'];
const CHUNK_ARRAY_KEYS = ['ReplacementChunks', 'Edits', 'edits', 'Replacements', 'chunks'];
const EDIT_TARGET_KEYS = ['TargetContent', 'Target', 'OldString', 'old_string', 'oldText', 'OldText', 'search'];
const EDIT_REPLACEMENT_KEYS = [
  'ReplacementContent',
  'Replacement',
  'NewString',
  'new_string',
  'newText',
  'NewText',
  'replace',
];
const FULL_CONTENT_KEYS = ['Content', 'CodeContent', 'content', 'file_text'];

const CODE_FILE_RE = /\.(?:[cm]?[jt]sx?|py|go|rs|swift|kt|kts|java|cs|rb|php|vue|svelte|astro)$/i;
const JS_TS_CATCH_FILE_RE = /\.(?:[cm]?[jt]sx?|vue|svelte|astro)$/i;
const NON_LOGIC_DIR_RE =
  /(?:^|\/)(?:node_modules|\.git|\.next|\.nuxt|\.svelte-kit|\.turbo|dist|build|out|coverage|__pycache__|\.venv|venv|target|vendor|docs|backups|archive|scratch|tmp|temp|__tests__|test|tests|fixtures|mocks|\.agent|\.agents|\.claude|\.github)(?:\/|$)/i;
const NON_LOGIC_FILE_RE =
  /(?:\.d\.[cm]?ts|\.(?:test|spec|stories|e2e)\.[A-Za-z0-9]+|(?:^|\/)test_[^/]+\.py|(?:^|\/)[^/]+_test\.(?:py|go|rs)|(?:^|\/)(?:vite|vitest|jest|playwright|webpack|rollup|tsup|esbuild|tailwind|postcss|eslint|prettier|babel|astro|next|nuxt|svelte|drizzle|prisma)\.config\.[A-Za-z0-9]+|(?:^|\/)setup\.py)$/i;

const SKIP_WALK_DIRS = new Set([
  '.git',
  'node_modules',
  '.next',
  '.nuxt',
  'dist',
  'build',
  'out',
  'coverage',
  '__pycache__',
  '.venv',
  'venv',
  'target',
  'vendor',
]);

const SUBAGENT_ID_RE = /"conversationId"\s*:\s*"([0-9a-fA-F-]{8,})"/;
const PROMPT_PATH_RE = /(?:~(?=\/)|\/[^\s"'`<>|*?():;,[\]{}\\/]+)(?:\/[^\s"'`<>|*?():;,[\]{}\\/]+)+/gu;
const GIT_COMMIT_CMD_RE = /(?:^|[;&|()\s])git(?:\s+-\S+(?:\s+\S+)?)*\s+commit\b/;
const CLI_FALLBACK_UNAVAILABLE_RE = /\[FALLBACK_TO_NATIVE_SUBAGENT\]|status:\s*external_review_unavailable/;

function allow() {
  console.log(JSON.stringify({ decision: 'allow' }));
}

let inputData = '';
process.stdin.setEncoding('utf-8');
process.stdin.on('data', (chunk) => {
  inputData += chunk;
});
process.stdin.on('end', () => {
  try {
    const payload = inputData ? JSON.parse(inputData) : {};
    runGate(payload);
  } catch {
    allow();
  }
});

function unwrapArg(val) {
  if (typeof val !== 'string') return '';
  const s = val.trim();
  if (s.length >= 2 && s.startsWith('"') && s.endsWith('"')) {
    try {
      const parsed = JSON.parse(s);
      if (typeof parsed === 'string') return parsed.trim();
    } catch {
      return s.slice(1, -1).trim();
    }
  }
  return s;
}

function unwrapCodeArg(val) {
  if (typeof val !== 'string') return '';
  const trimmed = val.trim();
  if (trimmed.length >= 2 && trimmed.startsWith('"') && trimmed.endsWith('"')) {
    try {
      const parsed = JSON.parse(trimmed);
      if (typeof parsed === 'string') return parsed;
    } catch {
      return trimmed.slice(1, -1);
    }
  }
  return val;
}

function unwrapJsonValue(val) {
  if (Array.isArray(val) || (val && typeof val === 'object')) return val;
  if (typeof val === 'string') {
    let cur = val.trim();
    for (let depth = 0; depth < 2; depth++) {
      try {
        const parsed = JSON.parse(cur);
        if (typeof parsed === 'string') {
          cur = parsed.trim();
          continue;
        }
        return parsed;
      } catch {
        break;
      }
    }
  }
  return null;
}

function parseIsoEpochSec(iso) {
  if (!iso || typeof iso !== 'string') return 0;
  const ms = Date.parse(iso);
  if (Number.isNaN(ms) || ms <= 0) return 0;
  return Math.floor(ms / 1000);
}

function safeRealpath(p) {
  try {
    return realpathSync(p);
  } catch {
    return resolve(p);
  }
}

function expandHome(p) {
  if (p === '~') return homedir();
  if (p.startsWith('~/') || p.startsWith('~\\')) return join(homedir(), p.slice(2));
  return p;
}

function resolveTargetPath(rawTarget, wsReal) {
  const expanded = expandHome(rawTarget);
  const full = !isAbsolute(expanded) && wsReal ? join(wsReal, expanded) : resolve(expanded);
  try {
    return realpathSync(full);
  } catch {
    const parent = safeRealpath(resolve(full, '..'));
    return join(parent, full.split(/[\\/]/).pop() || '');
  }
}

function buildArtifactPrefixes(artifactDir) {
  if (!artifactDir) return [];
  const realArt = safeRealpath(expandHome(artifactDir));
  const prefixes = [realArt + sep];
  const parentDir = dirname(realArt);
  if (basename(parentDir) === 'brain') {
    prefixes.push(parentDir + sep);
  }
  return prefixes;
}

function buildTempPrefixes() {
  const candidates = ['/tmp', '/private/tmp', tmpdir()];
  const out = new Set();
  for (const c of candidates) {
    if (!c) continue;
    out.add(resolve(c) + sep);
    out.add(safeRealpath(c) + sep);
  }
  return [...out];
}

function isInsideDir(absPath, dirReal) {
  if (!dirReal) return false;
  return absPath === dirReal || absPath.startsWith(dirReal + sep);
}

function normalizeSlash(p) {
  return p.replace(/\\/g, '/').replace(/^\.\//, '');
}

function isInsideUntrackedDir(relPath, untrackedDirs) {
  if (!untrackedDirs || untrackedDirs.length === 0) return false;
  for (const d of untrackedDirs) {
    if (relPath === d || relPath.startsWith(d + '/')) return true;
  }
  return false;
}

function isLogicCodeFile(relPath) {
  const norm = normalizeSlash(relPath);
  if (!CODE_FILE_RE.test(norm)) return false;
  if (NON_LOGIC_DIR_RE.test(norm)) return false;
  if (NON_LOGIC_FILE_RE.test(norm)) return false;
  return true;
}

function isJsTsCatchFile(relPath) {
  const norm = normalizeSlash(relPath);
  if (!JS_TS_CATCH_FILE_RE.test(norm)) return false;
  if (NON_LOGIC_DIR_RE.test(norm)) return false;
  return true;
}

function splitCleanLines(text) {
  if (!text) return [];
  const arr = String(text).replace(/\r\n/g, '\n').split('\n');
  if (arr.length > 0 && arr[arr.length - 1] === '') {
    arr.pop();
  }
  return arr;
}

function diffLinesLcs(targetText, replacementText) {
  const a = splitCleanLines(targetText);
  const b = splitCleanLines(replacementText);
  let prefix = 0;
  const minLen = Math.min(a.length, b.length);
  while (prefix < minLen && a[prefix] === b[prefix]) {
    prefix++;
  }
  let suffix = 0;
  while (
    suffix < a.length - prefix &&
    suffix < b.length - prefix &&
    a[a.length - 1 - suffix] === b[b.length - 1 - suffix]
  ) {
    suffix++;
  }

  const aMid = a.slice(prefix, a.length - suffix);
  const bMid = b.slice(prefix, b.length - suffix);
  if (aMid.length === 0 && bMid.length === 0) {
    return { deltaLines: 0, addedBlocks: [] };
  }
  if (aMid.length === 0) {
    return { deltaLines: bMid.length, addedBlocks: [bMid.join('\n')] };
  }
  if (bMid.length === 0) {
    return { deltaLines: aMid.length, addedBlocks: [] };
  }

  const n = aMid.length;
  const m = bMid.length;
  if (n * m > MAX_LCS_CELLS) {
    return { deltaLines: Math.max(n, m), addedBlocks: [bMid.join('\n')] };
  }

  const width = m + 1;
  const dp = new Uint16Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i--) {
    const row = i * width;
    const nextRow = (i + 1) * width;
    const ai = aMid[i];
    for (let j = m - 1; j >= 0; j--) {
      if (ai === bMid[j]) {
        dp[row + j] = dp[nextRow + j + 1] + 1;
      } else {
        const down = dp[nextRow + j];
        const right = dp[row + j + 1];
        dp[row + j] = down >= right ? down : right;
      }
    }
  }

  const lcsLen = dp[0];
  const deltaLines = Math.max(n - lcsLen, m - lcsLen);
  const addedBlocks = [];
  let curAdded = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (aMid[i] === bMid[j]) {
      if (curAdded.length > 0) {
        addedBlocks.push(curAdded.join('\n'));
        curAdded = [];
      }
      i++;
      j++;
    } else if (dp[(i + 1) * width + j] >= dp[i * width + (j + 1)]) {
      if (curAdded.length > 0) {
        addedBlocks.push(curAdded.join('\n'));
        curAdded = [];
      }
      i++;
    } else {
      curAdded.push(bMid[j]);
      j++;
    }
  }
  while (j < m) {
    curAdded.push(bMid[j]);
    j++;
  }
  if (curAdded.length > 0) {
    addedBlocks.push(curAdded.join('\n'));
  }

  return { deltaLines, addedBlocks };
}

function extractEditPair(obj) {
  if (!obj || typeof obj !== 'object') return null;
  let hasTgt = false;
  let tgt = '';
  for (const k of EDIT_TARGET_KEYS) {
    if (Object.prototype.hasOwnProperty.call(obj, k) && obj[k] !== undefined && obj[k] !== null) {
      hasTgt = true;
      tgt = unwrapCodeArg(String(obj[k]));
      break;
    }
  }
  let hasRep = false;
  let rep = '';
  for (const k of EDIT_REPLACEMENT_KEYS) {
    if (Object.prototype.hasOwnProperty.call(obj, k) && obj[k] !== undefined && obj[k] !== null) {
      hasRep = true;
      rep = unwrapCodeArg(String(obj[k]));
      break;
    }
  }
  if (!hasTgt || !hasRep) return null;
  return { tgt, rep };
}

function extractPatchTargets(args) {
  const targets = [];
  let patchBody = '';
  for (const k of PATCH_BODY_KEYS) {
    if (Object.prototype.hasOwnProperty.call(args, k) && typeof args[k] === 'string') {
      patchBody = unwrapCodeArg(args[k]);
      if (patchBody) break;
    }
  }
  if (!patchBody) return { targets, patchBody: '' };
  for (const line of patchBody.replace(/\r\n/g, '\n').split('\n')) {
    if (line.startsWith('+++ b/') && line.length > 6) {
      targets.push(line.slice(6).trim());
    } else if (line.startsWith('*** Update File: ') || line.startsWith('*** Add File: ')) {
      const idx = line.indexOf(': ');
      if (idx > 0) targets.push(line.slice(idx + 2).trim());
    }
  }
  return { targets: [...new Set(targets)], patchBody };
}

function computeToolDelta(toolName, args, stepTruncated) {
  if (stepTruncated) {
    return { isEditOnly: false, deltaLines: LINE_THRESHOLD, addedBlocks: [], editPairs: [], hadEmptyDeletion: false };
  }
  if (EDIT_DELTA_TOOLS.has(toolName)) {
    for (const ck of CHUNK_ARRAY_KEYS) {
      if (!Object.prototype.hasOwnProperty.call(args, ck) || args[ck] === undefined) continue;
      const rawChunks = unwrapJsonValue(args[ck]);
      if (!Array.isArray(rawChunks) || rawChunks.length === 0) {
        return { isEditOnly: false, deltaLines: 0, addedBlocks: [], editPairs: [], hadEmptyDeletion: false };
      }
      let total = 0;
      const blocks = [];
      const pairs = [];
      let hadEmptyDel = false;
      for (const ch of rawChunks) {
        const pair = extractEditPair(ch);
        if (!pair) {
          return { isEditOnly: false, deltaLines: 0, addedBlocks: [], editPairs: [], hadEmptyDeletion: false };
        }
        const d = diffLinesLcs(pair.tgt, pair.rep);
        total += d.deltaLines;
        blocks.push(...d.addedBlocks);
        pairs.push(pair);
        if (d.addedBlocks.length === 0 && pair.tgt.trim().length > 0) hadEmptyDel = true;
      }
      return { isEditOnly: true, deltaLines: total, addedBlocks: blocks, editPairs: pairs, hadEmptyDeletion: hadEmptyDel };
    }

    const singlePair = extractEditPair(args);
    if (singlePair) {
      const d = diffLinesLcs(singlePair.tgt, singlePair.rep);
      const hadEmptyDel = d.addedBlocks.length === 0 && singlePair.tgt.trim().length > 0;
      return {
        isEditOnly: true,
        deltaLines: d.deltaLines,
        addedBlocks: d.addedBlocks,
        editPairs: [singlePair],
        hadEmptyDeletion: hadEmptyDel,
      };
    }
    // Fail-Closed on unknown/missing edit schema -> fallback to git diff / disk lines
    return { isEditOnly: false, deltaLines: 0, addedBlocks: [], editPairs: [], hadEmptyDeletion: false };
  }

  for (const fk of FULL_CONTENT_KEYS) {
    if (Object.prototype.hasOwnProperty.call(args, fk) && typeof args[fk] === 'string') {
      const fullContent = unwrapCodeArg(args[fk]);
      return {
        isEditOnly: false,
        deltaLines: 0,
        addedBlocks: fullContent ? [fullContent] : [],
        editPairs: [],
        hadEmptyDeletion: false,
      };
    }
  }
  return { isEditOnly: false, deltaLines: 0, addedBlocks: [], editPairs: [], hadEmptyDeletion: false };
}

function checkCooldown(rawCid, kind, lastUserIdx) {
  const safeCid = String(rawCid || '')
    .trim()
    .replace(/[^A-Za-z0-9_-]/g, '_');
  if (!safeCid) return false;
  const turnKey = String(lastUserIdx ?? 0);
  const stateBase = process.env.XDG_STATE_HOME || join(homedir(), '.local', 'state');
  const dir = join(stateBase, 'agy-harness');
  mkdirSync(dir, { recursive: true });
  const gateFile = join(dir, `${safeCid}.${kind}`);
  const now = Math.floor(Date.now() / 1000);
  if (existsSync(gateFile)) {
    try {
      const raw = readFileSync(gateFile, 'utf-8').trim();
      const parts = raw.split(':');
      if (parts.length === 2) {
        const storedTurn = parts[0];
        const lastTs = parseInt(parts[1], 10);
        if (storedTurn === turnKey && !Number.isNaN(lastTs) && now - lastTs < 1800) {
          return true;
        }
      }
    } catch {} // fallback:ok
  }
  try {
    writeFileSync(gateFile, `${turnKey}:${now}`, 'utf-8');
  } catch {} // fallback:ok
  return false;
}

function collectSubagentIds(steps) {
  const auditIds = new Set();
  const mutationIds = new Set();

  for (let s = 0; s < steps.length; s++) {
    const st = steps[s];
    if (!Array.isArray(st.tool_calls) || st.tool_calls.length === 0) continue;

    const subagentCalls = [];
    for (const tc of st.tool_calls) {
      if (!tc || typeof tc !== 'object') continue;
      const nm = String(tc.name || tc.toolName || '');
      if (nm !== 'invoke_subagent') continue;
      const args = tc.args && typeof tc.args === 'object' ? tc.args : {};
      subagentCalls.push({
        callId: String(tc.id || tc.toolCallId || tc.tool_call_id || ''),
        subType: unwrapArg(args.TypeName),
      });
    }
    if (subagentCalls.length === 0) continue;

    const subagentResults = [];
    for (let k = s + 1; k < steps.length; k++) {
      const nxt = steps[k];
      if (!nxt || nxt.type === 'PLANNER_RESPONSE' || nxt.type === 'USER_INPUT') break;
      if (nxt.type === 'GENERIC' && typeof nxt.content === 'string') {
        const m = SUBAGENT_ID_RE.exec(nxt.content);
        if (m) {
          subagentResults.push({
            callId: String(nxt.tool_call_id || nxt.toolCallId || nxt.id || ''),
            convId: m[1],
            used: false,
          });
        }
      }
    }

    let seqIdx = 0;
    for (const call of subagentCalls) {
      let matchedId = '';
      if (call.callId) {
        const byId = subagentResults.find((r) => !r.used && r.callId && r.callId === call.callId);
        if (byId) {
          byId.used = true;
          matchedId = byId.convId;
        }
      }
      if (!matchedId) {
        while (seqIdx < subagentResults.length && subagentResults[seqIdx].used) {
          seqIdx++;
        }
        if (seqIdx < subagentResults.length) {
          subagentResults[seqIdx].used = true;
          matchedId = subagentResults[seqIdx].convId;
          seqIdx++;
        }
      }
      if (!matchedId) continue;

      if (AUDIT_SUBAGENTS.has(call.subType)) {
        auditIds.add(matchedId);
      } else if (DELEGATED_MUTATION_SUBAGENTS.has(call.subType)) {
        mutationIds.add(matchedId);
      }
    }
  }

  return { auditIds, mutationIds };
}

function isCliAuditInvocation(cmd) {
  if (!cmd) return false;
  if (/\s(?:--check|--help|-h)(?:\s|$)/.test(cmd)) return false;
  const runsExitReview = /(?:^|[\s/"'])exit_review\.py\b/.test(cmd) && /--repo\b/.test(cmd);
  const runsSonnetInspect = /(?:^|[\s/"'])sonnet_inspect\.py\b/.test(cmd) && /--findings\b/.test(cmd);
  return runsExitReview || runsSonnetInspect;
}

function isSwarmAuditInvocation(args) {
  if (!args || typeof args !== 'object') return false;
  if (unwrapArg(args.action).toLowerCase() !== 'add') return false;
  if (args.workspace !== undefined && args.workspace !== null && typeof args.workspace !== 'string') {
    return false;
  }
  const ws = unwrapArg(args.workspace).toLowerCase();
  if (ws && ws !== 'inherit') return false;
  const name = unwrapArg(args.name);
  if (!name) return false;
  if (SWARM_NON_AUDIT_NAME_RE.test(name)) return false;
  return SWARM_AUDIT_NAME_RE.test(name);
}

function isAuditToolCall(tc, auditIds) {
  if (!tc || typeof tc !== 'object') return false;
  const nm = String(tc.name || tc.toolName || '');
  const args = tc.args && typeof tc.args === 'object' ? tc.args : {};
  if (nm === 'invoke_subagent' && AUDIT_SUBAGENTS.has(unwrapArg(args.TypeName))) {
    return true;
  }
  if (nm === 'send_message' && auditIds.has(unwrapArg(args.Recipient))) {
    return true;
  }
  if (nm === 'swarm' && isSwarmAuditInvocation(args)) {
    return true;
  }
  if (nm === 'run_command' && isCliAuditInvocation(unwrapArg(args.CommandLine))) {
    return true;
  }
  return false;
}

function isMutationOrCommitToolCall(tc, mutationIds) {
  if (!tc || typeof tc !== 'object') return false;
  const nm = String(tc.name || tc.toolName || '');
  const args = tc.args && typeof tc.args === 'object' ? tc.args : {};
  if (MUTATION_TOOLS.has(nm)) return true;
  if (nm === 'invoke_subagent' && DELEGATED_MUTATION_SUBAGENTS.has(unwrapArg(args.TypeName))) return true;
  if (nm === 'send_message' && mutationIds.has(unwrapArg(args.Recipient))) return true;
  if (nm === 'run_command' && GIT_COMMIT_CMD_RE.test(unwrapArg(args.CommandLine))) return true;
  return false;
}

function stepResultIndicatesCliUnavailable(turnSteps, stepIdx) {
  for (let k = stepIdx + 1; k < turnSteps.length; k++) {
    const nxt = turnSteps[k];
    if (!nxt || nxt.type === 'PLANNER_RESPONSE' || nxt.type === 'USER_INPUT') break;
    if (typeof nxt.content === 'string' && CLI_FALLBACK_UNAVAILABLE_RE.test(nxt.content)) {
      return true;
    }
  }
  return false;
}

function toolCallResultIndicatesError(turnSteps, stepIdx, callIdx, tc) {
  const stepResults = [];
  for (let k = stepIdx + 1; k < turnSteps.length; k++) {
    const nxt = turnSteps[k];
    if (!nxt || nxt.type === 'PLANNER_RESPONSE' || nxt.type === 'USER_INPUT') break;
    if (nxt.type === 'SYSTEM_MESSAGE' || nxt.type === 'CHECKPOINT') continue;
    stepResults.push(nxt);
  }
  if (stepResults.length === 0) return false;

  const hasErr = (r) =>
    r.status === 'ERROR' ||
    r.status === 'INVALID' ||
    (typeof r.error === 'string' && r.error.trim().length > 0);

  const tcCallId = String((tc && (tc.id || tc.toolCallId || tc.tool_call_id)) || '');
  if (tcCallId) {
    const matched = stepResults.find(
      (r) => String(r.tool_call_id || r.toolCallId || r.id || '') === tcCallId,
    );
    if (matched) return hasErr(matched);
    if (stepResults.some((r) => Boolean(r.tool_call_id || r.toolCallId || r.id))) {
      return true;
    }
  }

  const totalCalls = Array.isArray(turnSteps[stepIdx]?.tool_calls) ? turnSteps[stepIdx].tool_calls.length : 0;
  if (totalCalls > 0 && stepResults.length === totalCalls && callIdx < stepResults.length) {
    return hasErr(stepResults[callIdx]);
  }

  // Fail-Closed when result steps cannot be unambiguously aligned to tool_calls
  return true;
}

function stepDispatchesAudit(turnSteps, stepIdx, auditIds) {
  const st = turnSteps[stepIdx];
  if (!st || !Array.isArray(st.tool_calls)) return false;
  for (let callIdx = 0; callIdx < st.tool_calls.length; callIdx++) {
    const tc = st.tool_calls[callIdx];
    if (!isAuditToolCall(tc, auditIds)) continue;
    const nm = String(tc.name || tc.toolName || '');
    if (nm === 'run_command' && stepResultIndicatesCliUnavailable(turnSteps, stepIdx)) {
      continue;
    }
    if (nm === 'swarm' && toolCallResultIndicatesError(turnSteps, stepIdx, callIdx, tc)) {
      continue;
    }
    return true;
  }
  return false;
}

function hydrateTruncatedTurnSteps(transcriptPath, turnSteps, turnStartLineIdx) {
  const needsHydration = turnSteps.some(
    (st) =>
      st &&
      Array.isArray(st.truncated_fields) &&
      (st.truncated_fields.includes('content') || st.truncated_fields.includes('tool_calls')),
  );
  if (!needsHydration) return turnSteps;

  const fullPath = join(dirname(transcriptPath), 'transcript_full.jsonl');
  if (!existsSync(fullPath)) return turnSteps;

  try {
    const fullLines = readFileSync(fullPath, 'utf-8').split('\n');
    const byStepIdx = new Map();
    const parsedFull = [];
    for (const line of fullLines) {
      if (!line.trim()) continue;
      try {
        const obj = JSON.parse(line);
        parsedFull.push(obj);
        if (obj && obj.step_index !== undefined && obj.step_index !== null) {
          byStepIdx.set(obj.step_index, obj);
        }
      } catch {} // fallback:ok
    }

    return turnSteps.map((st, offset) => {
      if (!st || !Array.isArray(st.truncated_fields) || st.truncated_fields.length === 0) return st;
      if (st.step_index !== undefined && st.step_index !== null && byStepIdx.has(st.step_index)) {
        return byStepIdx.get(st.step_index);
      }
      const fallbackObj = parsedFull[turnStartLineIdx + offset];
      if (fallbackObj && fallbackObj.type === st.type) {
        return fallbackObj;
      }
      return st;
    });
  } catch {
    return turnSteps;
  }
}

function extractPromptCandidatePaths(text, wsReal, artPrefixes) {
  if (!text) return [];
  const out = [];
  const matches = text.match(PROMPT_PATH_RE) || [];
  for (const raw of matches) {
    const cleaned = raw.replace(/[.,;:!?)'"`>\]]+$/, '');
    if (!cleaned) continue;
    const absP = resolveTargetPath(cleaned, wsReal);
    if (artPrefixes.some((pfx) => absP.startsWith(pfx))) continue;
    if (existsSync(absP)) {
      out.push(absP);
    } else if (existsSync(dirname(absP))) {
      out.push(dirname(absP));
    }
  }
  return out;
}

function lstatSafeIsDir(p) {
  try {
    const st = lstatSync(p);
    return st.isDirectory() && !st.isSymbolicLink();
  } catch {
    return false;
  }
}

function findGitRoot(absFilePath, wsReal) {
  const homeReal = safeRealpath(homedir());
  let cur = existsSync(absFilePath) && lstatSafeIsDir(absFilePath) ? absFilePath : dirname(absFilePath);
  for (let depth = 0; depth < 32; depth++) {
    if (!cur || cur === dirname(cur)) break;
    if (cur === homeReal && wsReal !== homeReal) break;
    if (existsSync(join(cur, '.git'))) {
      return safeRealpath(cur);
    }
    cur = dirname(cur);
  }
  return null;
}

function inspectTranscript(transcriptPath, artifactDir, wsReal) {
  const emptyResult = {
    hasUnreviewed: false,
    lastUserIdx: -1,
    turnStartSec: 0,
    afterAuditSec: 0,
    unreviewedRanGitCommit: false,
    unrecognizedPatchMutation: false,
    humanEscalated: false,
    delegatedMutation: false,
    delegatedPaths: [],
    directFiles: new Map(),
  };
  if (!transcriptPath || !existsSync(transcriptPath)) {
    return emptyResult;
  }
  try {
    const raw = readFileSync(transcriptPath, 'utf-8');
    const lines = raw.split('\n');
    let lastUserIdx = -1;
    const steps = [];
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const st = JSON.parse(line);
        if (st.type === 'USER_INPUT') lastUserIdx = steps.length;
        steps.push(st);
      } catch {} // fallback:ok
    }

    // Subagent transcripts never contain USER_INPUT (even when CHECKPOINT is present)
    if (lastUserIdx < 0) {
      return emptyResult;
    }

    const turnStartSec = parseIsoEpochSec(steps[lastUserIdx]?.created_at);
    const { auditIds, mutationIds } = collectSubagentIds(steps);
    const turnSteps = hydrateTruncatedTurnSteps(transcriptPath, steps.slice(lastUserIdx + 1), lastUserIdx + 1);
    const artPrefixes = buildArtifactPrefixes(artifactDir);
    const tempPrefixes = buildTempPrefixes();

    let humanEscalated = false;
    for (let i = turnSteps.length - 1; i >= 0; i--) {
      const st = turnSteps[i];
      if (st && st.type === 'PLANNER_RESPONSE' && typeof st.content === 'string' && st.content.trim()) {
        if (ESCALATION_MARKERS.some((m) => st.content.includes(m))) {
          humanEscalated = true;
        }
        break;
      }
    }

    let lastAuditStepIdx = -1;
    for (let i = 0; i < turnSteps.length; i++) {
      if (stepDispatchesAudit(turnSteps, i, auditIds)) {
        lastAuditStepIdx = i;
      }
    }

    const afterAuditSec =
      lastAuditStepIdx >= 0 ? parseIsoEpochSec(turnSteps[lastAuditStepIdx]?.created_at) || turnStartSec : turnStartSec;

    const unreviewedSteps = [];
    if (lastAuditStepIdx >= 0) {
      // Preserve any concurrent mutation tool_calls dispatched in the SAME step as the audit call
      const auditStep = turnSteps[lastAuditStepIdx];
      if (auditStep && Array.isArray(auditStep.tool_calls)) {
        const concurrentMutations = auditStep.tool_calls.filter((tc) => isMutationOrCommitToolCall(tc, mutationIds));
        if (concurrentMutations.length > 0) {
          unreviewedSteps.push({ ...auditStep, tool_calls: concurrentMutations });
        }
      }
    }
    unreviewedSteps.push(...turnSteps.slice(lastAuditStepIdx + 1));

    let delegatedMutation = false;
    let unreviewedRanGitCommit = false;
    let unrecognizedPatchMutation = false;
    const delegatedPaths = [];
    const directFiles = new Map();

    const recordDirectTarget = (rawTarget, nm, args, stepTruncated) => {
      const absT = resolveTargetPath(rawTarget, wsReal);
      if (artPrefixes.some((pfx) => absT.startsWith(pfx))) return;
      if (!isInsideDir(absT, wsReal) && tempPrefixes.some((pfx) => absT.startsWith(pfx))) {
        if (!findGitRoot(absT, wsReal)) return;
      }

      const prev = directFiles.get(absT) || {
        isEditOnly: true,
        stepChangedLines: 0,
        truncated: false,
        addedBlocks: [],
        editPairs: [],
        hadEmptyDeletion: false,
        wasDeletedOnly: true,
      };

      if (nm !== 'delete_file' && nm !== 'remove_file') {
        prev.wasDeletedOnly = false;
      }

      const deltaInfo = computeToolDelta(nm, args, stepTruncated);
      if (!EDIT_DELTA_TOOLS.has(nm) || !deltaInfo.isEditOnly) {
        prev.isEditOnly = false;
      }
      if (stepTruncated) {
        prev.truncated = true;
      }
      if (deltaInfo.hadEmptyDeletion) {
        prev.hadEmptyDeletion = true;
      }
      prev.stepChangedLines += deltaInfo.deltaLines;
      if (deltaInfo.addedBlocks.length > 0) {
        prev.addedBlocks.push(...deltaInfo.addedBlocks);
      }
      if (deltaInfo.editPairs.length > 0) {
        prev.editPairs.push(...deltaInfo.editPairs);
      }
      directFiles.set(absT, prev);
    };

    for (const st of unreviewedSteps) {
      if (!st || !Array.isArray(st.tool_calls)) continue;
      const stepTruncated = Array.isArray(st.truncated_fields) && st.truncated_fields.includes('tool_calls');

      for (const tc of st.tool_calls) {
        if (!tc || typeof tc !== 'object') continue;
        const nm = String(tc.name || tc.toolName || '');
        const args = tc.args && typeof tc.args === 'object' ? tc.args : {};

        if (nm === 'run_command') {
          const cmd = unwrapArg(args.CommandLine);
          if (GIT_COMMIT_CMD_RE.test(cmd) && !/\s(?:--help|-h)(?:\s|$)/.test(cmd)) {
            unreviewedRanGitCommit = true;
          }
          continue;
        }

        if (nm === 'invoke_subagent') {
          const subType = unwrapArg(args.TypeName);
          if (DELEGATED_MUTATION_SUBAGENTS.has(subType)) {
            delegatedMutation = true;
            delegatedPaths.push(
              ...extractPromptCandidatePaths(unwrapCodeArg(String(args.Prompt ?? '')), wsReal, artPrefixes),
            );
          }
          continue;
        }

        if (nm === 'send_message') {
          const rec = unwrapArg(args.Recipient);
          if (mutationIds.has(rec)) {
            delegatedMutation = true;
            delegatedPaths.push(
              ...extractPromptCandidatePaths(unwrapCodeArg(String(args.Message ?? '')), wsReal, artPrefixes),
            );
          }
          continue;
        }

        if (!MUTATION_TOOLS.has(nm)) continue;

        let target = '';
        for (const k of PATH_ARG_KEYS) {
          const v = unwrapArg(args[k]);
          if (v) {
            target = v;
            break;
          }
        }
        if (target) {
          recordDirectTarget(target, nm, args, stepTruncated);
          continue;
        }

        if (PATCH_TOOLS.has(nm)) {
          const { targets: patchTargets } = extractPatchTargets(args);
          if (patchTargets.length > 0) {
            for (const pt of patchTargets) {
              recordDirectTarget(pt, nm, args, stepTruncated);
            }
          } else {
            unrecognizedPatchMutation = true;
          }
        }
      }
    }

    const hasUnreviewed = delegatedMutation || directFiles.size > 0 || unrecognizedPatchMutation;
    return {
      hasUnreviewed,
      lastUserIdx,
      turnStartSec,
      afterAuditSec,
      unreviewedRanGitCommit,
      unrecognizedPatchMutation,
      humanEscalated,
      delegatedMutation,
      delegatedPaths,
      directFiles,
    };
  } catch {
    return emptyResult;
  }
}

function walkUntrackedDirBounded(repoRoot, relDir, outSet, state) {
  const walk = (curRel, depth) => {
    if (depth > MAX_UNTRACKED_WALK_DEPTH || state.scanned >= MAX_UNTRACKED_WALK_ENTRIES) return;
    const absDir = join(repoRoot, curRel);
    if (!lstatSafeIsDir(absDir)) return;
    let entries = [];
    try {
      entries = readdirSync(absDir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const ent of entries) {
      if (state.scanned >= MAX_UNTRACKED_WALK_ENTRIES) return;
      if (ent.isSymbolicLink()) continue;
      const childRel = curRel ? `${curRel}/${ent.name}` : ent.name;
      if (ent.isDirectory()) {
        if (SKIP_WALK_DIRS.has(ent.name)) continue;
        walk(childRel, depth + 1);
      } else if (ent.isFile()) {
        state.scanned += 1;
        outSet.add(normalizeSlash(childRel));
      }
    }
  };
  walk(normalizeSlash(relDir).replace(/\/+$/, ''), 0);
}

function getRepoSnapshot(repoRoot, cache) {
  if (cache.has(repoRoot)) return cache.get(repoRoot);
  const snap = {
    isGit: false,
    hasHead: false,
    trackedModified: new Set(),
    untrackedFiles: new Set(),
    untrackedDirs: [],
  };
  try {
    execFileSync('git', ['--no-optional-locks', '-C', repoRoot, 'rev-parse', '--is-inside-work-tree'], {
      stdio: 'ignore',
      timeout: 2000,
    });
    snap.isGit = true;
  } catch {
    // Fail-Closed when `git status` times out or fails: treat as non-git so direct/delegated mutations are still counted
    snap.isGit = false;
    cache.set(repoRoot, snap);
    return snap;
  }

  try {
    execFileSync('git', ['--no-optional-locks', '-C', repoRoot, 'rev-parse', '--verify', 'HEAD'], {
      stdio: 'ignore',
      timeout: 1500,
    });
    snap.hasHead = true;
  } catch {
    snap.hasHead = false;
  }

  let statusOut = '';
  try {
    statusOut = execFileSync(
      'git',
      ['--no-optional-locks', '-C', repoRoot, '-c', 'core.quotePath=false', 'status', '--porcelain', '-z', '-unormal'],
      {
        encoding: 'utf-8',
        stdio: ['ignore', 'pipe', 'ignore'],
        timeout: 3000,
      },
    );
  } catch {
    // Fail-Closed when `git status` times out or fails: treat as non-git so direct/delegated mutations are still counted
    snap.isGit = false;
    cache.set(repoRoot, snap);
    return snap;
  }

  const tokens = statusOut.split('\0');
  for (let idx = 0; idx < tokens.length; idx++) {
    const entry = tokens[idx];
    if (!entry || entry.length < 4) continue;
    const status = entry.slice(0, 2);
    const rawRel = entry.slice(3);
    if (status[0] === 'R' || status[0] === 'C') {
      idx += 1;
    }
    if (!rawRel) continue;
    if (status === '??') {
      if (rawRel.endsWith('/')) {
        snap.untrackedDirs.push(normalizeSlash(rawRel).replace(/\/+$/, ''));
      } else {
        snap.untrackedFiles.add(normalizeSlash(rawRel));
      }
    } else {
      const normRel = normalizeSlash(rawRel);
      if (existsSync(join(repoRoot, normRel))) {
        snap.trackedModified.add(normRel);
      }
    }
  }

  const walkState = { scanned: 0 };
  for (const relDir of snap.untrackedDirs) {
    if (SKIP_WALK_DIRS.has(relDir.split('/')[0])) continue;
    walkUntrackedDirBounded(repoRoot, relDir, snap.untrackedFiles, walkState);
    if (walkState.scanned >= MAX_UNTRACKED_WALK_ENTRIES) break;
  }

  cache.set(repoRoot, snap);
  return snap;
}

function parseUnifiedDiffMap(diffOut) {
  const byFile = new Map();
  if (!diffOut) return byFile;

  let curRel = '';
  let curEntry = null;
  let curOld = [];
  let curNew = [];
  let inHunk = false;

  const flushHunk = () => {
    if (curEntry && inHunk && (curOld.length > 0 || curNew.length > 0)) {
      curEntry.hunks.push({ oldSide: curOld.join('\n'), newSide: curNew.join('\n') });
    }
    curOld = [];
    curNew = [];
    inHunk = false;
  };

  for (const line of diffOut.split('\n')) {
    if (line.startsWith('diff --git ')) {
      flushHunk();
      curRel = '';
      curEntry = null;
      continue;
    }
    if (!inHunk && line.startsWith('+++ b/')) {
      curRel = normalizeSlash(line.slice(6));
      curEntry = byFile.get(curRel);
      if (!curEntry) {
        curEntry = { addCount: 0, delCount: 0, hunks: [] };
        byFile.set(curRel, curEntry);
      }
      continue;
    }
    if (line.startsWith('@@')) {
      flushHunk();
      if (curEntry) inHunk = true;
      continue;
    }
    if (!inHunk || !curEntry) continue;

    if (line.startsWith('+') && !line.startsWith('+++')) {
      curNew.push(line.slice(1));
      curEntry.addCount += 1;
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      curOld.push(line.slice(1));
      curEntry.delCount += 1;
    } else if (line.startsWith(' ')) {
      const ctx = line.slice(1);
      curOld.push(ctx);
      curNew.push(ctx);
    }
  }
  flushHunk();

  const out = new Map();
  for (const [rel, ent] of byFile.entries()) {
    out.set(rel, {
      changedCount: Math.max(ent.addCount, ent.delCount),
      hunks: ent.hunks,
    });
  }
  return out;
}

function getWorkingDiffMap(repoRoot, hasHead, diffCache) {
  const key = `${repoRoot}::${hasHead ? 'HEAD' : 'INDEX'}`;
  if (diffCache.has(key)) return diffCache.get(key);

  const args = hasHead
    ? ['--no-optional-locks', '-C', repoRoot, '-c', 'core.quotePath=false', 'diff', 'HEAD', '-U3']
    : ['--no-optional-locks', '-C', repoRoot, '-c', 'core.quotePath=false', 'diff', '-U3'];
  let diffOut = '';
  try {
    diffOut = execFileSync('git', args, {
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'ignore'],
      maxBuffer: 12 * 1024 * 1024,
      timeout: 3500,
    });
  } catch {
    const empty = new Map();
    diffCache.set(key, empty);
    return empty;
  }
  const parsed = parseUnifiedDiffMap(diffOut);
  diffCache.set(key, parsed);
  return parsed;
}

function getRecentCommittedDiffMap(repoRoot, minEpochSec, forceHeadCommit, commitCache) {
  const key = `${repoRoot}::${minEpochSec}::${forceHeadCommit ? '1' : '0'}`;
  if (commitCache.has(key)) return commitCache.get(key);

  const empty = new Map();
  if (!forceHeadCommit && minEpochSec <= 0) {
    commitCache.set(key, empty);
    return empty;
  }

  let headEpoch = 0;
  try {
    const rawEpoch = execFileSync('git', ['--no-optional-locks', '-C', repoRoot, 'log', '-1', '--format=%ct', 'HEAD'], {
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 1500,
    }).trim();
    headEpoch = parseInt(rawEpoch, 10) || 0;
  } catch {
    commitCache.set(key, empty);
    return empty;
  }

  const inTimeWindow = minEpochSec > 0 && headEpoch >= minEpochSec - 1;
  if (!forceHeadCommit && !inTimeWindow) {
    commitCache.set(key, empty);
    return empty;
  }

  let hashes = [];
  if (inTimeWindow) {
    try {
      const logOut = execFileSync(
        'git',
        ['--no-optional-locks', '-C', repoRoot, 'log', `--since=@${Math.max(0, minEpochSec - 1)}`, '--format=%H', 'HEAD'],
        { encoding: 'utf-8', stdio: ['ignore', 'pipe', 'ignore'], timeout: 2000 },
      );
      hashes = logOut
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean);
    } catch {} // fallback:ok
  }

  let combinedDiff = '';
  try {
    if (hashes.length > 0) {
      const oldest = hashes[hashes.length - 1];
      let hasParent = false;
      try {
        execFileSync('git', ['--no-optional-locks', '-C', repoRoot, 'rev-parse', '--verify', `${oldest}^`], {
          stdio: 'ignore',
          timeout: 1000,
        });
        hasParent = true;
      } catch {} // fallback:ok
      if (hasParent) {
        combinedDiff = execFileSync(
          'git',
          ['--no-optional-locks', '-C', repoRoot, '-c', 'core.quotePath=false', 'diff', '-U3', `${oldest}^`, 'HEAD'],
          { encoding: 'utf-8', stdio: ['ignore', 'pipe', 'ignore'], maxBuffer: 12 * 1024 * 1024, timeout: 3500 },
        );
      } else {
        combinedDiff = execFileSync(
          'git',
          ['--no-optional-locks', '-C', repoRoot, '-c', 'core.quotePath=false', 'show', '--format=', '-U3', oldest],
          { encoding: 'utf-8', stdio: ['ignore', 'pipe', 'ignore'], maxBuffer: 12 * 1024 * 1024, timeout: 3500 },
        );
        if (hashes.length > 1) {
          combinedDiff +=
            '\n' +
            execFileSync(
              'git',
              ['--no-optional-locks', '-C', repoRoot, '-c', 'core.quotePath=false', 'diff', '-U3', oldest, 'HEAD'],
              { encoding: 'utf-8', stdio: ['ignore', 'pipe', 'ignore'], maxBuffer: 12 * 1024 * 1024, timeout: 3500 },
            );
        }
      }
    } else if (forceHeadCommit) {
      combinedDiff = execFileSync(
        'git',
        ['--no-optional-locks', '-C', repoRoot, '-c', 'core.quotePath=false', 'show', '--format=', '-U3', 'HEAD'],
        { encoding: 'utf-8', stdio: ['ignore', 'pipe', 'ignore'], maxBuffer: 12 * 1024 * 1024, timeout: 3500 },
      );
    }
  } catch {
    commitCache.set(key, empty);
    return empty;
  }

  const parsed = parseUnifiedDiffMap(combinedDiff);
  commitCache.set(key, parsed);
  return parsed;
}

function readDiskBlock(absPath) {
  if (!existsSync(absPath)) return '';
  try {
    return readFileSync(absPath, 'utf-8');
  } catch {
    return '';
  }
}

function sanitizeJsForCatchScan(src) {
  const len = src.length;
  let out = '';
  let i = 0;
  let prevNonSpace = '';
  let braceDepth = 0;
  const tplBraceStack = [];

  // Exclude `<` and `>` so JSX closing tags (`</div> ... </span>`) never masquerade as regex literals
  const canStartRegex = (ch) => !ch || '=([,:;!&|?{}~+-*%^'.includes(ch);

  const scanTemplateTail = () => {
    while (i < len) {
      const c = src[i];
      if (c === '\\') {
        if (i + 1 < len && src[i + 1] === '\n') out += '\n';
        i += 2;
        continue;
      }
      if (c === '$' && i + 1 < len && src[i + 1] === '{') {
        out += '${';
        tplBraceStack.push(braceDepth);
        braceDepth += 1;
        prevNonSpace = '{';
        i += 2;
        return;
      }
      if (c === '\n') {
        out += '\n';
      } else if (c === '`') {
        out += '`';
        prevNonSpace = '`';
        i += 1;
        return;
      } else {
        out += ' ';
      }
      i += 1;
    }
  };

  while (i < len) {
    const ch = src[i];
    const nxt = i + 1 < len ? src[i + 1] : '';
    const prevChar = i > 0 ? src[i - 1] : '';

    // Single-line comment
    if (ch === '/' && nxt === '/') {
      let end = src.indexOf('\n', i + 2);
      if (end < 0) end = len;
      const commentBody = src.slice(i + 2, end);
      if (commentBody.includes('fallback:ok')) {
        out += ' __FALLBACK_OK__ ';
      }
      i = end;
      continue;
    }

    // Multi-line comment
    if (ch === '/' && nxt === '*') {
      let end = src.indexOf('*/', i + 2);
      const commentBody = end >= 0 ? src.slice(i + 2, end) : src.slice(i + 2);
      if (commentBody.includes('fallback:ok')) {
        out += ' __FALLBACK_OK__ ';
      } else {
        const nlCount = (commentBody.match(/\n/g) || []).length;
        out += '\n'.repeat(nlCount) + ' ';
      }
      i = end >= 0 ? end + 2 : len;
      continue;
    }

    // Contraction/possessive apostrophe inside JSX/HTML text (`Don't`, `user's`):
    // In valid JS/TS, a single-quoted string literal can never immediately follow `[A-Za-z0-9_$]`.
    if (ch === "'" && /[A-Za-z0-9_$]/.test(prevChar)) {
      out += ' ';
      i += 1;
      continue;
    }

    // Single or double-quoted string literal (cannot span unescaped newlines)
    if (ch === "'" || ch === '"') {
      const quote = ch;
      out += quote;
      i += 1;
      while (i < len) {
        const c = src[i];
        if (c === '\\') {
          if (i + 1 < len && src[i + 1] === '\n') out += '\n';
          i += 2;
          continue;
        }
        if (c === '\n' || c === '\r') {
          break;
        }
        if (c === quote) {
          out += quote;
          i += 1;
          break;
        }
        out += ' ';
        i += 1;
      }
      prevNonSpace = quote;
      continue;
    }

    // Template literal with `${...}` expression re-entry
    if (ch === '`') {
      out += '`';
      i += 1;
      scanTemplateTail();
      continue;
    }

    // Regex literal (single-line)
    if (ch === '/' && nxt !== '/' && nxt !== '*' && canStartRegex(prevNonSpace)) {
      let j = i + 1;
      let inClass = false;
      let closed = false;
      while (j < len && src[j] !== '\n') {
        const rc = src[j];
        if (rc === '\\') {
          j += 2;
          continue;
        }
        if (rc === '[') inClass = true;
        else if (rc === ']') inClass = false;
        else if (rc === '/' && !inClass) {
          closed = true;
          break;
        }
        j += 1;
      }
      if (closed) {
        out += '/ /';
        i = j + 1;
        while (i < len && /[A-Za-z]/.test(src[i])) i += 1;
        prevNonSpace = '/';
        continue;
      }
    }

    if (ch === '{') {
      braceDepth += 1;
    } else if (ch === '}') {
      if (tplBraceStack.length > 0 && braceDepth === tplBraceStack[tplBraceStack.length - 1] + 1) {
        tplBraceStack.pop();
        braceDepth -= 1;
        out += '}';
        i += 1;
        scanTemplateTail();
        continue;
      }
      if (braceDepth > 0) braceDepth -= 1;
    }

    out += ch;
    if (!/\s/.test(ch)) {
      prevNonSpace = ch;
    }
    i += 1;
  }

  return out;
}

const EMPTY_CATCH_BLOCK_RE = /\bcatch\b\s*(?:\([^(){}]*\))?\s*\{\s*\}/g;

function collectUnannotatedEmptyCatches(rawBlock) {
  if (!rawBlock || !rawBlock.includes('catch')) return [];
  const cleaned = sanitizeJsForCatchScan(rawBlock);
  const found = [];
  EMPTY_CATCH_BLOCK_RE.lastIndex = 0;
  let m;
  while ((m = EMPTY_CATCH_BLOCK_RE.exec(cleaned)) !== null) {
    const beforeSlice = cleaned.slice(Math.max(0, m.index - 80), m.index);
    const afterSlice = cleaned.slice(m.index + m[0].length, m.index + m[0].length + 80);
    if (/__FALLBACK_OK__\s*$/.test(beforeSlice) || /^[ \t]*__FALLBACK_OK__/.test(afterSlice)) {
      continue;
    }
    found.push(m[0].replace(/\s+/g, ' ').trim());
  }
  return found;
}

function findNewlyIntroducedEmptyCatch(oldText, newText) {
  const newCatches = collectUnannotatedEmptyCatches(newText);
  if (newCatches.length === 0) return null;
  const oldCatches = collectUnannotatedEmptyCatches(oldText);
  if (oldCatches.length === 0) return newCatches[0];

  const oldCounts = new Map();
  for (const s of oldCatches) {
    oldCounts.set(s, (oldCounts.get(s) || 0) + 1);
  }
  const seenNew = new Map();
  for (const s of newCatches) {
    const nxtCount = (seenNew.get(s) || 0) + 1;
    seenNew.set(s, nxtCount);
    if (nxtCount > (oldCounts.get(s) || 0)) {
      return s;
    }
  }
  if (newCatches.length > oldCatches.length) {
    return newCatches[newCatches.length - 1];
  }
  return null;
}

function findEmptyCatchInHunks(hunks) {
  if (!hunks || hunks.length === 0) return null;
  for (const h of hunks) {
    const hit = findNewlyIntroducedEmptyCatch(h.oldSide, h.newSide);
    if (hit) return hit;
  }
  return null;
}

function runGate(payload) {
  if (process.env.AGY_SKIP_PROCESS_REVIEW_GATE === '1') {
    allow();
    return;
  }

  const term = String(payload.terminationReason || '');
  if (term !== 'NO_TOOL_CALL' && term !== 'model_stop') {
    allow();
    return;
  }
  if (payload.fullyIdle === false) {
    allow();
    return;
  }

  const wsRaw = Array.isArray(payload.workspacePaths) && payload.workspacePaths[0] ? String(payload.workspacePaths[0]) : '';
  const wsReal = wsRaw && existsSync(wsRaw) ? safeRealpath(wsRaw) : '';

  const {
    hasUnreviewed,
    lastUserIdx,
    turnStartSec,
    afterAuditSec,
    unreviewedRanGitCommit,
    unrecognizedPatchMutation,
    humanEscalated,
    delegatedMutation,
    delegatedPaths,
    directFiles,
  } = inspectTranscript(payload.transcriptPath, payload.artifactDirectoryPath, wsReal);

  if (!hasUnreviewed) {
    allow();
    return;
  }

  const repoCache = new Map();
  const diffCache = new Map();
  const commitCache = new Map();
  let detectedBadCatch = null;
  const recordCatchHit = (hit) => {
    if (hit && !detectedBadCatch) detectedBadCatch = hit;
  };

  const activeLogicFiles = [];
  const minCommitSec = afterAuditSec || turnStartSec;

  for (const [absPath, info] of directFiles.entries()) {
    if (info.wasDeletedOnly && !existsSync(absPath)) continue;

    const gitRoot = findGitRoot(absPath, wsReal);
    const baseRoot = gitRoot || wsReal;
    const relPath =
      baseRoot && isInsideDir(absPath, baseRoot) ? normalizeSlash(relative(baseRoot, absPath)) : basename(absPath);
    const snap = gitRoot ? getRepoSnapshot(gitRoot, repoCache) : null;

    let isDirty = false;
    let committedEntry = null;

    if (snap && snap.isGit) {
      isDirty =
        snap.trackedModified.has(relPath) ||
        snap.untrackedFiles.has(relPath) ||
        (existsSync(absPath) && isInsideUntrackedDir(relPath, snap.untrackedDirs));
      if (!isDirty && snap.hasHead) {
        const committedMap = getRecentCommittedDiffMap(gitRoot, minCommitSec, unreviewedRanGitCommit, commitCache);
        committedEntry = committedMap.get(relPath) || null;
      }
      if (!isDirty && !committedEntry) continue;
    } else if (!existsSync(absPath)) {
      continue;
    }

    if (isJsTsCatchFile(relPath) && !detectedBadCatch) {
      // 1. Check explicit edit pairs (handles split braces & body deletion even without git HEAD)
      for (const pair of info.editPairs) {
        recordCatchHit(findNewlyIntroducedEmptyCatch(pair.tgt, pair.rep));
      }
      // 2. Check -U3 hunks (handles `Target="log(e)" -> Replacement=""` inside surrounding `catch (e) { ... }` and tracked overwrites)
      if (!detectedBadCatch) {
        if (committedEntry) {
          recordCatchHit(findEmptyCatchInHunks(committedEntry.hunks));
        } else if (snap && snap.isGit && snap.trackedModified.has(relPath)) {
          const wMap = getWorkingDiffMap(gitRoot, snap.hasHead, diffCache);
          const fDiff = wMap.get(relPath);
          if (fDiff) {
            recordCatchHit(findEmptyCatchInHunks(fDiff.hunks));
          } else if (!snap.hasHead) {
            recordCatchHit(findNewlyIntroducedEmptyCatch('', readDiskBlock(absPath)));
          }
        } else {
          // Untracked or non-git file (or pure deletion fallback without HEAD)
          if (!info.isEditOnly || info.hadEmptyDeletion || !snap || !snap.isGit) {
            recordCatchHit(findNewlyIntroducedEmptyCatch('', readDiskBlock(absPath)));
          } else {
            for (const blk of info.addedBlocks) {
              recordCatchHit(findNewlyIntroducedEmptyCatch('', blk));
            }
          }
        }
      }
    }

    if (isLogicCodeFile(relPath)) {
      let effectiveLines = 0;
      if (info.isEditOnly && !info.truncated) {
        effectiveLines = info.stepChangedLines;
      } else if (committedEntry) {
        effectiveLines = committedEntry.changedCount;
      } else if (snap && snap.isGit && snap.trackedModified.has(relPath) && snap.hasHead) {
        const wMap = getWorkingDiffMap(gitRoot, true, diffCache);
        if (wMap.has(relPath)) {
          effectiveLines = wMap.get(relPath).changedCount;
        } else if (info.addedBlocks.length > 0 && !info.truncated) {
          effectiveLines = splitCleanLines(info.addedBlocks.join('\n')).length;
        } else {
          effectiveLines = splitCleanLines(readDiskBlock(absPath)).length;
        }
      } else if (info.addedBlocks.length > 0 && !info.truncated) {
        effectiveLines = splitCleanLines(info.addedBlocks.join('\n')).length;
      } else {
        effectiveLines = splitCleanLines(readDiskBlock(absPath)).length;
      }
      activeLogicFiles.push({ relPath, effectiveLines });
    }
  }

  let delegatedHasDirtyLogic = false;
  if (delegatedMutation) {
    const delegatedGitRoots = new Set();
    let hasNonGitDelegatedTarget = false;

    if (wsReal) {
      const wsGit = findGitRoot(wsReal, wsReal);
      if (wsGit) delegatedGitRoots.add(wsGit);
      else hasNonGitDelegatedTarget = true;
    }
    for (const dp of delegatedPaths) {
      const pGit = findGitRoot(dp, wsReal);
      if (pGit) {
        delegatedGitRoots.add(pGit);
      } else if (isLogicCodeFile(basename(dp))) {
        hasNonGitDelegatedTarget = true;
      }
    }

    if (delegatedGitRoots.size === 0 || hasNonGitDelegatedTarget) {
      delegatedHasDirtyLogic = true;
    }

    for (const gRoot of delegatedGitRoots) {
      const gSnap = getRepoSnapshot(gRoot, repoCache);
      if (!gSnap.isGit) {
        delegatedHasDirtyLogic = true;
        continue;
      }
      const wMap = gSnap.trackedModified.size > 0 ? getWorkingDiffMap(gRoot, gSnap.hasHead, diffCache) : new Map();
      for (const rel of gSnap.trackedModified) {
        if (isJsTsCatchFile(rel) && !detectedBadCatch) {
          const fDiff = wMap.get(rel);
          if (fDiff) {
            recordCatchHit(findEmptyCatchInHunks(fDiff.hunks));
          } else if (!gSnap.hasHead) {
            recordCatchHit(findNewlyIntroducedEmptyCatch('', readDiskBlock(join(gRoot, rel))));
          }
        }
        if (isLogicCodeFile(rel)) delegatedHasDirtyLogic = true;
      }
      for (const rel of gSnap.untrackedFiles) {
        if (isJsTsCatchFile(rel) && !detectedBadCatch) {
          recordCatchHit(findNewlyIntroducedEmptyCatch('', readDiskBlock(join(gRoot, rel))));
        }
        if (isLogicCodeFile(rel)) delegatedHasDirtyLogic = true;
      }
      if (gSnap.hasHead) {
        const cMap = getRecentCommittedDiffMap(gRoot, minCommitSec, unreviewedRanGitCommit, commitCache);
        for (const [rel, cEnt] of cMap.entries()) {
          if (isJsTsCatchFile(rel) && !detectedBadCatch) {
            recordCatchHit(findEmptyCatchInHunks(cEnt.hunks));
          }
          if (isLogicCodeFile(rel)) delegatedHasDirtyLogic = true;
        }
      }
    }
  }

  if (detectedBadCatch) {
    if (checkCooldown(payload.conversationId, 'catchgate', lastUserIdx)) {
      allow();
      return;
    }
    console.log(
      JSON.stringify({
        decision: 'continue',
        reason: `[PROCESS GATE] 追加・変更行に空の catch 握り潰しが検知されました: "${detectedBadCatch}"。エラーを伝搬するか、正当な理由がある場合は // fallback:ok を付与してください。`,
      }),
    );
    return;
  }

  if (humanEscalated) {
    allow();
    return;
  }

  let triggerReason = '';
  if (delegatedMutation && delegatedHasDirtyLogic) {
    triggerReason = 'flash-implementer 等の実装サブエージェント委譲';
  } else if (unrecognizedPatchMutation) {
    triggerReason = 'apply_patch / apply_diff によるパス未特定コード変更 (Fail-Closed)';
  } else if (activeLogicFiles.length >= FILE_THRESHOLD) {
    triggerReason = `プロダクトコード変更 ${activeLogicFiles.length} ファイル (閾値 >= ${FILE_THRESHOLD})`;
  } else if (activeLogicFiles.length === 1 && activeLogicFiles[0].effectiveLines >= LINE_THRESHOLD) {
    triggerReason = `${activeLogicFiles[0].relPath} の追加・変更 ${activeLogicFiles[0].effectiveLines} 行 (閾値 >= ${LINE_THRESHOLD}行)`;
  }

  if (triggerReason) {
    if (checkCooldown(payload.conversationId, 'procgate', lastUserIdx)) {
      allow();
      return;
    }
    console.log(
      JSON.stringify({
        decision: 'continue',
        reason: `[PROCESS GATE] ${triggerReason} が検出されましたが、変更後のクリーンルーム反証レビュー（second-opinion-lite / carbon-verifier / claude-exit-review / multi-agent-opinion）の実行記録が確認できません。§3.5 に従い Tier 1 以上の独立検証を実行してから完了報告してください。`,
      }),
    );
    return;
  }

  allow();
}
