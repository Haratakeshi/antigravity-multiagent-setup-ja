#!/usr/bin/env node

/**
 * Antigravity Process Review Gate (Stop Hook)
 *
 * Enforces §3.5 on active workspaces with `src/` JS/TS changes:
 * 1. Runs only on normal model completion (`NO_TOOL_CALL` / `model_stop`) when `fullyIdle === true`
 * 2. Unwraps JSON-stringified tool arguments in `transcript.jsonl` (e.g. `"\"carbon-verifier\""` -> `"carbon-verifier"`)
 * 3. Bypasses subagent transcripts (`lastUserIdx < 0`, regardless of `CHECKPOINT`) and `<appDataDir>/brain/` artifact writes
 * 4. Checks tracked (staged + unstaged) AND untracked `src/` JS/TS files (including monorepo `packages/.../src/...`) for empty `catch {}`
 * 5. Recognizes BOTH fresh `invoke_subagent` AND Round-2+ `send_message` to an already-spawned audit subagent `conversationId` after the last mutation
 * 6. Uses sanitized `conversationId` and separate 30-min cooldowns (`.catchgate` vs `.procgate`)
 */

import { execFileSync } from 'child_process';
import { readFileSync, writeFileSync, mkdirSync, existsSync, realpathSync } from 'fs';
import { homedir } from 'os';
import { basename, dirname, join, resolve, sep, isAbsolute } from 'path';

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

const DELEGATED_MUTATION_SUBAGENTS = new Set(['flash-implementer', 'self']);

const AUDIT_SUBAGENTS = new Set([
  'carbon-verifier',
  'clean-room-auditor',
  'multi-agent-opinion',
  'zero-bias-reviewer',
  'second-opinion-lite',
  'senior-architect-advisor',
  'mindfulness-inspector',
  'domain-logic-reviewer',
  'security-vulnerability-reviewer',
]);

const AUDIT_CLI_SCRIPTS = ['exit_review.py', 'sonnet_inspect.py'];

const PATH_ARG_KEYS = [
  'Path',
  'TargetFile',
  'AbsolutePath',
  'FilePath',
  'TargetPath',
  'path',
  'file_path',
];

const SRC_JS_TS_RE = /(?:^|\/)src\/.*\.(?:[cm]?[jt]sx?)$/;
const SUBAGENT_ID_RE = /"conversationId"\s*:\s*"([0-9a-fA-F-]{8,})"/;
const SENDER_ID_RE = /\bsender=([0-9a-fA-F-]{8,})\b/;

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

function checkCooldown(rawCid, kind) {
  const safeCid = String(rawCid || '')
    .trim()
    .replace(/[^A-Za-z0-9_-]/g, '_');
  if (!safeCid) return false;
  const stateBase = process.env.XDG_STATE_HOME || join(homedir(), '.local', 'state');
  const dir = join(stateBase, 'agy-harness');
  mkdirSync(dir, { recursive: true });
  const gateFile = join(dir, `${safeCid}.${kind}`);
  const now = Math.floor(Date.now() / 1000);
  if (existsSync(gateFile)) {
    try {
      const last = parseInt(readFileSync(gateFile, 'utf-8').trim(), 10);
      if (!Number.isNaN(last) && now - last < 1800) {
        return true;
      }
    } catch {}
  }
  try {
    writeFileSync(gateFile, String(now), 'utf-8');
  } catch {}
  return false;
}

function collectAuditSubagentIds(steps) {
  const ids = new Set();
  for (let s = 0; s < steps.length; s++) {
    const st = steps[s];
    if (!Array.isArray(st.tool_calls) || st.tool_calls.length === 0) continue;

    const genericResults = [];
    for (let k = s + 1; k < steps.length; k++) {
      const nxt = steps[k];
      if (!nxt || nxt.type === 'PLANNER_RESPONSE' || nxt.type === 'USER_INPUT') break;
      if (nxt.type === 'GENERIC') genericResults.push(nxt);
    }

    for (let j = 0; j < st.tool_calls.length; j++) {
      const tc = st.tool_calls[j];
      if (!tc || typeof tc !== 'object') continue;
      const nm = String(tc.name || tc.toolName || '');
      if (nm !== 'invoke_subagent') continue;
      const args = tc.args && typeof tc.args === 'object' ? tc.args : {};
      const subType = unwrapArg(args.TypeName);
      if (!AUDIT_SUBAGENTS.has(subType)) continue;

      const resStep = genericResults[j];
      if (resStep && typeof resStep.content === 'string') {
        const m = SUBAGENT_ID_RE.exec(resStep.content);
        if (m) ids.add(m[1]);
      }
    }
  }
  return ids;
}

function inspectTranscript(transcriptPath, artifactDir, wsReal) {
  if (!transcriptPath || !existsSync(transcriptPath)) {
    return { turnMutated: false, auditedAfterMutation: true };
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
      } catch {}
    }

    // Subagent transcripts never contain USER_INPUT (even when CHECKPOINT is present)
    if (lastUserIdx < 0) {
      return { turnMutated: false, auditedAfterMutation: true };
    }

    const auditSubagentIds = collectAuditSubagentIds(steps);
    const turnSteps = steps.slice(lastUserIdx + 1);
    const artPrefixes = buildArtifactPrefixes(artifactDir);

    let lastMutationStepIdx = -1;
    for (let i = 0; i < turnSteps.length; i++) {
      const st = turnSteps[i];
      if (!Array.isArray(st.tool_calls)) continue;
      for (const tc of st.tool_calls) {
        if (!tc || typeof tc !== 'object') continue;
        const nm = String(tc.name || tc.toolName || '');
        const args = tc.args && typeof tc.args === 'object' ? tc.args : {};

        if (nm === 'invoke_subagent') {
          const subType = unwrapArg(args.TypeName);
          if (DELEGATED_MUTATION_SUBAGENTS.has(subType)) {
            lastMutationStepIdx = i;
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
        if (target && artPrefixes.length > 0) {
          const absT = resolveTargetPath(target, wsReal);
          if (artPrefixes.some((pfx) => absT.startsWith(pfx))) continue;
        }
        lastMutationStepIdx = i;
      }
    }

    if (lastMutationStepIdx < 0) {
      return { turnMutated: false, auditedAfterMutation: true };
    }

    let auditedAfterMutation = false;
    for (let i = lastMutationStepIdx; i < turnSteps.length; i++) {
      const st = turnSteps[i];
      if (st.type === 'SYSTEM_MESSAGE' && typeof st.content === 'string' && auditSubagentIds.size > 0) {
        const sm = SENDER_ID_RE.exec(st.content);
        if (sm && auditSubagentIds.has(sm[1])) {
          auditedAfterMutation = true;
          break;
        }
      }
      if (!Array.isArray(st.tool_calls)) continue;
      for (const tc of st.tool_calls) {
        if (!tc || typeof tc !== 'object') continue;
        const nm = String(tc.name || tc.toolName || '');
        const args = tc.args && typeof tc.args === 'object' ? tc.args : {};
        if (nm === 'invoke_subagent' && AUDIT_SUBAGENTS.has(unwrapArg(args.TypeName))) {
          auditedAfterMutation = true;
          break;
        }
        if (nm === 'send_message' && auditSubagentIds.has(unwrapArg(args.Recipient))) {
          auditedAfterMutation = true;
          break;
        }
        if (nm === 'run_command') {
          const cmd = unwrapArg(args.CommandLine);
          if (AUDIT_CLI_SCRIPTS.some((s) => cmd.includes(s))) {
            auditedAfterMutation = true;
            break;
          }
        }
      }
      if (auditedAfterMutation) break;
    }

    return { turnMutated: true, auditedAfterMutation };
  } catch {
    return { turnMutated: false, auditedAfterMutation: true };
  }
}

function unquotePorcelainPath(rawPath) {
  let p = rawPath.trim();
  const arrowIdx = p.indexOf(' -> ');
  if (arrowIdx >= 0) {
    p = p.slice(arrowIdx + 4).trim();
  }
  if (p.startsWith('"') && p.endsWith('"')) {
    try {
      p = JSON.parse(p);
    } catch {
      p = p.slice(1, -1);
    }
  }
  return p.replace(/\\/g, '/');
}

function findEmptyCatch(lines) {
  for (const text of lines) {
    const trimmed = text.trim();
    if (trimmed.includes('// fallback:ok')) continue;
    if (/catch\s*(?:\([^)]*\))?\s*\{\s*\}/.test(trimmed)) {
      return trimmed;
    }
  }
  return null;
}

function runGate(payload) {
  const term = String(payload.terminationReason || '');
  if (term !== 'NO_TOOL_CALL' && term !== 'model_stop') {
    allow();
    return;
  }
  if (payload.fullyIdle !== true) {
    allow();
    return;
  }

  const wsRaw = Array.isArray(payload.workspacePaths) && payload.workspacePaths[0] ? String(payload.workspacePaths[0]) : '';
  if (!wsRaw || !existsSync(wsRaw)) {
    allow();
    return;
  }
  const wsReal = safeRealpath(wsRaw);

  const { turnMutated, auditedAfterMutation } = inspectTranscript(
    payload.transcriptPath,
    payload.artifactDirectoryPath,
    wsReal,
  );
  if (!turnMutated) {
    allow();
    return;
  }

  try {
    execFileSync('git', ['rev-parse', '--is-inside-work-tree'], { cwd: wsReal, stdio: 'ignore' });
  } catch {
    allow();
    return;
  }

  let statusOut = '';
  try {
    statusOut = execFileSync('git', ['-c', 'core.quotePath=false', 'status', '--porcelain', '-uall'], {
      cwd: wsReal,
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'ignore'],
    });
  } catch {}

  const trackedSrc = [];
  const untrackedSrc = [];
  for (const line of statusOut.split('\n')) {
    if (line.length < 4) continue;
    const status = line.slice(0, 2);
    if (status.includes('D')) continue;
    const relPath = unquotePorcelainPath(line.slice(3));
    if (!SRC_JS_TS_RE.test(relPath)) continue;
    if (status === '??') {
      untrackedSrc.push(relPath);
    } else {
      trackedSrc.push(relPath);
    }
  }

  if (trackedSrc.length === 0 && untrackedSrc.length === 0) {
    allow();
    return;
  }

  const candidateAddedLines = [];
  if (trackedSrc.length > 0) {
    let diffOut = '';
    try {
      diffOut = execFileSync('git', ['diff', 'HEAD', '-U0', '--', ...trackedSrc], {
        cwd: wsReal,
        encoding: 'utf-8',
        stdio: ['ignore', 'pipe', 'ignore'],
      });
    } catch {
      try {
        diffOut = execFileSync('git', ['diff', '-U0', '--', ...trackedSrc], {
          cwd: wsReal,
          encoding: 'utf-8',
          stdio: ['ignore', 'pipe', 'ignore'],
        });
      } catch {}
    }
    for (const line of diffOut.split('\n')) {
      if (line.startsWith('+') && !line.startsWith('+++')) {
        candidateAddedLines.push(line.slice(1));
      }
    }
  }

  for (const relPath of untrackedSrc) {
    const full = join(wsReal, relPath);
    if (existsSync(full)) {
      try {
        candidateAddedLines.push(...readFileSync(full, 'utf-8').split('\n'));
      } catch {}
    }
  }

  const badCatch = findEmptyCatch(candidateAddedLines);
  if (badCatch) {
    if (checkCooldown(payload.conversationId, 'catchgate')) {
      allow();
      return;
    }
    console.log(
      JSON.stringify({
        decision: 'continue',
        reason: `[PROCESS GATE] src/ 内の追加行に空の catch 握り潰しが検知されました: "${badCatch}"。エラーを伝搬するか、正当な理由がある場合は // fallback:ok を付与してください。`,
      }),
    );
    return;
  }

  if (!auditedAfterMutation) {
    if (checkCooldown(payload.conversationId, 'procgate')) {
      allow();
      return;
    }
    console.log(
      JSON.stringify({
        decision: 'continue',
        reason:
          '[PROCESS GATE] src/ 配下にロジック変更差分が存在しますが、変更後のクリーンルーム監査サブエージェント（carbon-verifier / second-opinion-lite / multi-agent-opinion 等）の実行記録が確認できません。§3.5 に従い Tier 1 以上の独立検証を実行してください。',
      }),
    );
    return;
  }

  allow();
}
