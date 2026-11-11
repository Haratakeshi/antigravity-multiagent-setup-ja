#!/usr/bin/env node

/**
 * Antigravity Process Review Gate (Optional Global Stop Hook)
 *
 * Enforces GEMINI.md §3.5:
 * 1. Resolves active workspace from payload.workspacePaths[0] (or process.cwd())
 * 2. Checks for uncommitted/branch changes in src/
 * 3. Scans added lines (^+ only) for unsanctioned empty catch swallows (bypassed with // fallback:ok)
 * 4. Checks transcript for clean-room / second-opinion review execution trace
 */

import { execSync } from 'child_process';
import { readFileSync, existsSync } from 'fs';

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
    console.log(JSON.stringify({ decision: 'allow' }));
  }
});

function runGate(payload) {
  const reason = String(payload.terminationReason || '');
  if (reason && reason !== 'NO_TOOL_CALL' && reason !== 'model_stop') {
    console.log(JSON.stringify({ decision: 'allow' }));
    return;
  }
  if (payload.fullyIdle === false) {
    console.log(JSON.stringify({ decision: 'allow' }));
    return;
  }

  const workspace =
    Array.isArray(payload.workspacePaths) && payload.workspacePaths.length > 0
      ? payload.workspacePaths[0]
      : process.cwd();

  if (!workspace || !existsSync(workspace)) {
    console.log(JSON.stringify({ decision: 'allow' }));
    return;
  }

  let hasGit = false;
  try {
    execSync('git rev-parse --is-inside-work-tree', { cwd: workspace, stdio: 'ignore' });
    hasGit = true;
  } catch {
    hasGit = false;
  }

  if (!hasGit) {
    console.log(JSON.stringify({ decision: 'allow' }));
    return;
  }

  let changedFiles = '';
  try {
    changedFiles = execSync('git status --porcelain', { cwd: workspace, encoding: 'utf-8' });
    try {
      changedFiles += '\n' + execSync('git diff --name-only origin/main...HEAD', { cwd: workspace, encoding: 'utf-8' });
    } catch {
      try {
        changedFiles += '\n' + execSync('git diff --name-only HEAD~1...HEAD', { cwd: workspace, encoding: 'utf-8' });
      } catch {}
    }
  } catch {}

  const srcChanged = changedFiles
    .split('\n')
    .some((f) => f.includes('src/') && (f.endsWith('.ts') || f.endsWith('.tsx') || f.endsWith('.js') || f.endsWith('.jsx')));

  if (!srcChanged) {
    console.log(JSON.stringify({ decision: 'allow' }));
    return;
  }

  let diffContent = '';
  try {
    diffContent = execSync('git diff -U0', { cwd: workspace, encoding: 'utf-8' });
  } catch {}

  const addedLines = diffContent
    .split('\n')
    .filter((line) => line.startsWith('+') && !line.startsWith('+++'));

  for (const line of addedLines) {
    const text = line.substring(1).trim();
    if (text.includes('// fallback:ok')) continue;

    if (/catch\s*\([^)]*\)\s*\{\s*\}/.test(text)) {
      console.log(
        JSON.stringify({
          decision: 'continue',
          reason: `[PROCESS GATE] src/ 内の追加行に空の catch 握り潰しが検知されました: "${text}"。エラーを伝搬するか、正当な理由がある場合は // fallback:ok を付与してください。`,
        }),
      );
      return;
    }
  }

  const transcriptPath = payload.transcriptPath;
  let audited = false;

  if (transcriptPath && existsSync(transcriptPath)) {
    try {
      const transcript = readFileSync(transcriptPath, 'utf-8');
      if (
        transcript.includes('clean-room-auditor') ||
        transcript.includes('multi-agent-opinion') ||
        transcript.includes('zero-bias-audit') ||
        transcript.includes('second-opinion') ||
        transcript.includes('carbon-verifier') ||
        transcript.includes('senior-architect-advisor') ||
        transcript.includes('claude-exit-review') ||
        transcript.includes('exit_review.py')
      ) {
        audited = true;
      }
    } catch {}
  } else {
    audited = true;
  }

  if (!audited) {
    console.log(
      JSON.stringify({
        decision: 'continue',
        reason:
          '[PROCESS GATE] src/ 配下にロジック変更差分が存在しますが、独立レビュー（second-opinion-lite / multi-agent-opinion / carbon-verifier 等）の実行記録が確認できません。GEMINI.md §3.5 に従い、完了報告前にクリーンルーム監査を実施してください。',
      }),
    );
    return;
  }

  console.log(JSON.stringify({ decision: 'allow' }));
}
