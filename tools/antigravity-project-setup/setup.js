#!/usr/bin/env node

/**
 * Antigravity Gemini Project Setup Tool
 * Zero-dependency interactive project setup CLI tool.
 * Detects language, frameworks, package managers, and environments to deploy
 * optimized configurations and custom agents for Google Antigravity Gemini.
 */

const fs = require('fs');
const path = require('path');
const readline = require('readline');
const crypto = require('crypto');
const os = require('os');

// Windows platform check
if (process.platform === 'win32') {
  console.error('\x1b[1m\x1b[31m[Antigravity Setup] ✘ CRITICAL ERROR: Windows platform is currently not supported. This tool requires a POSIX environment.\x1b[0m');
  process.exit(1);
}

const PINNED_VERSIONS = Object.freeze({
  // Pinned versions for third-party CLIs executed dynamically.
  // Updated on 2026-05-22. Re-evaluate quarterly or when new platform features are required.
  firebase: '13.12.0',
  vercel: '37.2.1'
});

const KNOWN_CLI_PATHS = Object.freeze({
  claude: process.env.HOME
    ? [path.join(process.env.HOME, '.local/bin/claude'), '/opt/homebrew/bin/claude', '/usr/local/bin/claude']
    : ['/opt/homebrew/bin/claude', '/usr/local/bin/claude'],
  agy: ['agy', 'antigravity']
});

// ANSI Color Codes for Premium Console UI
const COLORS = {
  reset: '\x1b[0m',
  bold: '\x1b[1m',
  green: '\x1b[32m',
  blue: '\x1b[34m',
  yellow: '\x1b[33m',
  red: '\x1b[31m',
  cyan: '\x1b[36m',
  magenta: '\x1b[35m'
};

const TRUSTED_RAW_SYMBOL = Symbol('trustedRaw');

function trustedRaw(content) {
  const obj = Object.create(null);
  Object.defineProperty(obj, TRUSTED_RAW_SYMBOL, {
    value: true,
    enumerable: false,
    configurable: false,
    writable: false
  });
  Object.defineProperty(obj, 'content', {
    value: content,
    enumerable: true,
    configurable: false,
    writable: false
  });
  return obj;
}

function sanitizeForConsole(str) {
  if (typeof str !== 'string') return '';
  return str.replace(/[\x00-\x1f\x7f-\x9f\u001b\u009b\u202a-\u202e\u2066-\u2069]/g, c => {
    const code = c.charCodeAt(0);
    if (code <= 0xff) {
      return `\\x${code.toString(16).padStart(2, '0')}`;
    } else {
      return `\\u${code.toString(16).padStart(4, '0')}`;
    }
  });
}

function resolveInside(root, candidate) {
  const resolved = path.resolve(candidate);
  const resolvedRoot = fs.realpathSync.native(path.resolve(root));
  
  // Lexical containment check against the realpath of the root
  const rootPrefix = resolvedRoot + path.sep;
  const isLexicalContained = resolved === resolvedRoot || resolved.startsWith(rootPrefix);
  
  // Realpath containment check
  let isRealContained = false;
  try {
    const realCandidate = fs.realpathSync.native(resolved);
    isRealContained = realCandidate === resolvedRoot || realCandidate.startsWith(rootPrefix);
  } catch (e) {
    // If it doesn't exist yet, we check the closest existing parent directory
    let current = resolved;
    while (true) {
      const parent = path.dirname(current);
      if (!parent || parent === current) {
        break;
      }
      try {
        const realParent = fs.realpathSync.native(parent);
        isRealContained = realParent === resolvedRoot || realParent.startsWith(rootPrefix);
        break; // Containment of the closest existing ancestor determines containment of the candidate
      } catch (pe) {
        current = parent;
      }
    }
  }
  
  if (!isLexicalContained && !isRealContained) {
    throw new Error(`Directory traversal attempt blocked: ${candidate}`);
  }
  return resolved;
}

function assertParentStillInsideRoot(targetPath) {
  const parentDir = path.dirname(path.resolve(targetPath));
  const resolvedRoot = fs.realpathSync.native(TARGET_DIR);
  const rootPrefix = resolvedRoot + path.sep;
  try {
    const realParent = fs.realpathSync.native(parentDir);
    if (realParent !== resolvedRoot && !realParent.startsWith(rootPrefix)) {
      throw new Error(`Parent directory containment violation: parent ${parentDir} resolves to ${realParent}`);
    }
  } catch (e) {
    if (e.code !== 'ENOENT') {
      throw e;
    }
  }
}

function closeWrittenFdOrThrow(fd, filePath) {
  try {
    fs.closeSync(fd);
  } catch (e) {
    error(`CRITICAL: Failed to securely close file descriptor for ${filePath}: ${e.message}`);
    throw new Error(`Failed to close written file securely: ${filePath} (${e.message})`);
  }
}

function assertNoSymlinkComponents(targetPath) {
  const absolutePath = path.resolve(targetPath);
  const parts = absolutePath.split(path.sep);
  let current = absolutePath.startsWith('/') ? '/' : '';
  
  for (let i = 0; i < parts.length; i++) {
    if (!parts[i]) continue;
    current = path.join(current, parts[i]);
    try {
      const stat = fs.lstatSync(current);
      if (stat.isSymbolicLink()) {
        error(`CRITICAL SECURITY ALERT: Symlink component detected at: ${sanitizeForConsole(current)}`);
        throw new Error(`Symbolic link component rejected: ${sanitizeForConsole(current)}`);
      }
    } catch (e) {
      if (e.code === 'ENOENT') {
        break;
      }
      throw e;
    }
  }
}

const LOG_PREFIX = `${COLORS.bold}${COLORS.magenta}[Antigravity Setup]${COLORS.reset} `;

function log(msg) {
  console.log(`${LOG_PREFIX}${msg}`);
}

function success(msg) {
  console.log(`${LOG_PREFIX}${COLORS.green}✔ ${msg}${COLORS.reset}`);
}

function warn(msg) {
  console.log(`${LOG_PREFIX}${COLORS.yellow}⚠ ${msg}${COLORS.reset}`);
}

function error(msg) {
  console.log(`${LOG_PREFIX}${COLORS.red}✘ ${msg}${COLORS.reset}`);
}

function info(msg) {
  console.log(`${LOG_PREFIX}${COLORS.cyan}ℹ ${msg}${COLORS.reset}`);
}

function sanitizeForMarkdown(str) {
  if (typeof str !== 'string') return '';
  return str
    .replace(/[\r\n]+/g, ' ')
    .replace(/`/g, '\\`')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/#/g, '\\#')
    .replace(/\[/g, '\\[')
    .replace(/\]/g, '\\]')
    .trim();
}

function slugForShell(str, fallback = 'project') {
  const cleaned = String(str || '')
    .replace(/[^A-Za-z0-9._-]/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 80);
  return cleaned || fallback;
}

// Helper to securely read file contents, strictly rejecting symlinks
function safeReadFile(targetPath) {
  const resolved = path.resolve(targetPath);
  const isInsideTemplate = typeof TEMPLATE_DIR !== 'undefined' && (resolved === path.resolve(TEMPLATE_DIR) || resolved.startsWith(path.resolve(TEMPLATE_DIR) + path.sep));
  const root = isInsideTemplate ? TEMPLATE_DIR : TARGET_DIR;
  resolveInside(root, targetPath);
  assertNoSymlinkComponents(targetPath);
  let fd;
  try {
    fd = fs.openSync(targetPath, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    const stat = fs.fstatSync(fd);
    if (!stat.isFile()) {
      error(`CRITICAL SECURITY ALERT: Path ${targetPath} is not a regular file.`);
      throw new Error(`Path ${targetPath} is not a regular file.`);
    }
    return fs.readFileSync(fd, 'utf8');
  } catch (e) {
    if (e.code === 'ELOOP') {
      error(`CRITICAL SECURITY ALERT: Path ${targetPath} is a symbolic link. Symlinks are strictly rejected for security.`);
      throw new Error(`Symbolic link rejected at: ${targetPath}`);
    } else {
      error(`CRITICAL: Failed to read target path ${targetPath} securely: ${e.message}`);
      throw e;
    }
  } finally {
    if (fd !== undefined) {
      try {
        fs.closeSync(fd);
      } catch (closeErr) {
        warn(`Non-fatal: failed to close file descriptor for ${targetPath}: ${closeErr.message}`);
      }
    }
  }
}

function safeWriteFile(targetPath, content, mode = 0o644) {
  if (!Number.isInteger(mode) || mode < 0 || mode > 0o777) {
    throw new Error(`Invalid file mode specification: 0o${mode.toString(8)}`);
  }
  resolveInside(TARGET_DIR, targetPath);
  assertNoSymlinkComponents(targetPath);
  assertParentStillInsideRoot(targetPath);

  const targetParentDir = path.dirname(targetPath);
  let parentStat;
  try {
    parentStat = fs.lstatSync(targetParentDir);
    if (parentStat.isSymbolicLink()) {
      error(`CRITICAL SECURITY ALERT: Parent directory ${targetParentDir} is a symbolic link.`);
      throw new Error(`Symbolic link rejected at: ${targetParentDir}`);
    }
  } catch (e) {
    if (e.code !== 'ENOENT') throw e;
  }

  let targetDirStat;
  try {
    targetDirStat = fs.lstatSync(TARGET_DIR);
    if (targetDirStat.isSymbolicLink()) {
      error(`CRITICAL SECURITY ALERT: TARGET_DIR ${TARGET_DIR} is a symbolic link.`);
      throw new Error(`Symbolic link rejected at: ${TARGET_DIR}`);
    }
  } catch (e) {
    if (e.code !== 'ENOENT') throw e;
  }

  let existingContent;
  let origMode = 0o600; // secure fallback default
  let fd;

  try {
    assertParentStillInsideRoot(targetPath);
    fd = fs.openSync(targetPath, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    const stat = fs.fstatSync(fd);
    if (!stat.isFile()) {
      error(`CRITICAL SECURITY ALERT: Path ${targetPath} is not a regular file.`);
      throw new Error(`Path ${targetPath} is not a regular file.`);
    }
    origMode = stat.mode & 0o777;
    existingContent = fs.readFileSync(fd, 'utf8');
  } catch (e) {
    if (e.code === 'ENOENT') {
      // File does not exist, safe to proceed
    } else if (e.code === 'ELOOP') {
      error(`CRITICAL SECURITY ALERT: Path ${targetPath} is a symbolic link. Symlinks are strictly rejected for security.`);
      throw new Error(`Symbolic link rejected at: ${targetPath}`);
    } else {
      error(`CRITICAL: Failed to open target path ${targetPath} securely: ${e.message}. Aborting to prevent data loss or silent hijack.`);
      throw e;
    }
  } finally {
    if (fd !== undefined) {
      try {
        fs.closeSync(fd);
      } catch (closeErr) {
        warn(`Non-fatal: failed to close file descriptor: ${closeErr.message}`);
      }
    }
  }

  if (existingContent !== undefined) {
    const baseTimestamp = new Date().toISOString().replace(/[:.]/g, '-');
    const randomSuffix = crypto.randomBytes(4).toString('hex');
    const backupPath = `${targetPath}.${baseTimestamp}_${randomSuffix}.bak`;

    resolveInside(TARGET_DIR, backupPath);
    assertNoSymlinkComponents(backupPath);
    assertParentStillInsideRoot(backupPath);
    const backupParent = path.dirname(backupPath);
    let backupParentStat;
    try {
      backupParentStat = fs.lstatSync(backupParent);
      if (backupParentStat.isSymbolicLink()) {
        error(`CRITICAL SECURITY ALERT: Backup parent directory ${backupParent} is a symbolic link.`);
        throw new Error(`Symbolic link rejected at backup path parent: ${backupParent}`);
      }
    } catch (e) {
      if (e.code !== 'ENOENT') throw e;
    }

    let backupFd;
    try {
      assertParentStillInsideRoot(backupPath);
      backupFd = fs.openSync(backupPath, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, origMode);
      fs.writeFileSync(backupFd, existingContent, { encoding: 'utf8' });
      fs.fchmodSync(backupFd, origMode);
      fs.fsyncSync(backupFd);
      success(`Existing file found at ${path.basename(targetPath)}. Created secure backup at ${path.basename(backupPath)} with mode 0o${origMode.toString(8)}.`);
    } catch (e) {
      error(`CRITICAL: Failed to create backup for ${path.basename(targetPath)}: ${e.message}. Aborting write operation to prevent data loss.`);
      throw new Error(`Backup failed: ${e.message}`);
    } finally {
      if (backupFd !== undefined) {
        closeWrittenFdOrThrow(backupFd, backupPath);
      }
    }
  }

  const tempPath = `${targetPath}.tmp-${crypto.randomBytes(8).toString('hex')}`;
  resolveInside(TARGET_DIR, tempPath);
  assertNoSymlinkComponents(tempPath);
  assertParentStillInsideRoot(tempPath);

  let tempFd;
  try {
    assertParentStillInsideRoot(tempPath);
    tempFd = fs.openSync(tempPath, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o644);
    fs.writeFileSync(tempFd, content, { encoding: 'utf8' });
    fs.fchmodSync(tempFd, mode);
    fs.fsyncSync(tempFd);
  } catch (e) {
    try {
      let tempStat;
      try {
        tempStat = fs.lstatSync(tempPath);
      } catch (_) {}
      if (tempStat && !tempStat.isSymbolicLink()) {
        fs.unlinkSync(tempPath);
      }
    } catch (unlinkErr) {
      warn(`Non-fatal: failed to remove temporary file ${tempPath}: ${unlinkErr.message}`);
    }
    error(`CRITICAL: Failed to write file securely to ${path.basename(targetPath)}: ${e.message}`);
    throw e;
  } finally {
    if (tempFd !== undefined) {
      closeWrittenFdOrThrow(tempFd, tempPath);
    }
  }

  try {
    resolveInside(TARGET_DIR, targetPath);
    assertNoSymlinkComponents(targetPath);
    assertParentStillInsideRoot(targetPath);
    assertParentStillInsideRoot(tempPath);
    
    fs.renameSync(tempPath, targetPath);
    let dirFd;
    try {
      const parentDir = path.dirname(targetPath);
      assertParentStillInsideRoot(targetPath);
      dirFd = fs.openSync(parentDir, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
      fs.fsyncSync(dirFd);
    } catch (e) {
      warn(`Non-fatal: failed to fsync parent directory of ${path.basename(targetPath)}: ${e.message}`);
    } finally {
      if (dirFd !== undefined) {
        try {
          fs.closeSync(dirFd);
        } catch (closeErr) {
          warn(`Non-fatal: failed to close parent directory file descriptor: ${closeErr.message}`);
        }
      }
    }
  } catch (e) {
    try {
      let tempStat;
      try {
        tempStat = fs.lstatSync(tempPath);
      } catch (_) {}
      if (tempStat && !tempStat.isSymbolicLink()) {
        fs.unlinkSync(tempPath);
      }
    } catch (unlinkErr) {
      warn(`Non-fatal: failed to clean up temp file ${tempPath}: ${unlinkErr.message}`);
    }
    error(`CRITICAL: Failed to rename file to ${path.basename(targetPath)}: ${e.message}`);
    throw e;
  }
}

function safeCreateDir(dirPath) {
  const resolvedPath = resolveInside(TARGET_DIR, dirPath);
  assertNoSymlinkComponents(resolvedPath);
  const parts = resolvedPath.split(path.sep);
  let current = '';
  const isAbs = path.isAbsolute(resolvedPath);
  if (isAbs) {
    current = parts[0] === '' ? '/' : parts[0];
  }
  
  for (let i = (isAbs ? 1 : 0); i < parts.length; i++) {
    if (!parts[i]) continue;
    current = path.join(current, parts[i]);
    
    let stat;
    try {
      stat = fs.lstatSync(current);
    } catch (e) {
      if (e.code !== 'ENOENT') throw e;
      stat = null;
    }
    
    if (stat) {
      if (stat.isSymbolicLink()) {
        error(`CRITICAL SECURITY ALERT: Path ${current} is a symbolic link. Symlinks are strictly rejected.`);
        throw new Error(`Symbolic link rejected at intermediate path: ${current}`);
      }
      if (!stat.isDirectory()) {
        error(`CRITICAL: Path ${current} exists but is a file, not a directory. Aborting setup.`);
        throw new Error(`Path ${current} is not a directory`);
      }
      const realCurrent = fs.realpathSync.native(current);
      const resolvedRoot = fs.realpathSync.native(TARGET_DIR);
      const isBelow = realCurrent === resolvedRoot || realCurrent.startsWith(resolvedRoot + path.sep);
      const isParent = resolvedRoot.startsWith(realCurrent + path.sep) || resolvedRoot === realCurrent;
      if (!isBelow && !isParent) {
        error(`CRITICAL SECURITY ALERT: Directory containment violation at ${current} (realpath: ${realCurrent})`);
        throw new Error(`Directory containment violation: ${current}`);
      }
    } else {
      try {
        fs.mkdirSync(current, { mode: 0o755 });
        // Immediately double check after directory creation to block race condition symlink injection
        const postStat = fs.lstatSync(current);
        if (postStat.isSymbolicLink()) {
          error(`CRITICAL SECURITY ALERT: Symlink injection detected at ${current} immediately after creation.`);
          throw new Error(`Symlink injection detected at intermediate path: ${current}`);
        }
        const realCurrent = fs.realpathSync.native(current);
        const resolvedRoot = fs.realpathSync.native(TARGET_DIR);
        const isBelow = realCurrent === resolvedRoot || realCurrent.startsWith(resolvedRoot + path.sep);
        const isParent = resolvedRoot.startsWith(realCurrent + path.sep) || resolvedRoot === realCurrent;
        if (!isBelow && !isParent) {
          error(`CRITICAL SECURITY ALERT: Directory containment violation at ${current} (realpath: ${realCurrent})`);
          throw new Error(`Directory containment violation: ${current}`);
        }
      } catch (e) {
        error(`CRITICAL: Failed to create directory ${current}: ${e.message}. Aborting setup.`);
        throw e;
      }
    }
  }
}

// Global configurations
const SCRIPT_DIR = __dirname;
const TEMPLATE_DIR = path.join(SCRIPT_DIR, 'templates');
const TARGET_DIR = process.cwd();

// Verify templates directory exists
if (!fs.existsSync(TEMPLATE_DIR)) {
  error(`Templates directory not found at ${TEMPLATE_DIR}. Please check the folder setup.`);
  process.exit(1);
}

// Helper to read templates with targeted diagnostics
function readTemplate(relPath) {
  const fullPath = path.join(TEMPLATE_DIR, relPath);
  resolveInside(TEMPLATE_DIR, fullPath);
  assertNoSymlinkComponents(TEMPLATE_DIR);
  try {
    return safeReadFile(fullPath);
  } catch (e) {
    error(`CRITICAL: Failed to read required template file at ${relPath} (${fullPath}): ${e.message}`);
    throw new Error(`Template missing: ${relPath}. Please reinstall the plugin.`);
  }
}

// Technical Stack Registry / Detector
const DETECTOR = {
  packageManagers: [
    { file: 'pnpm-lock.yaml', name: 'pnpm', type: 'js', priority: 10 },
    { file: 'uv.lock', name: 'uv', type: 'python', priority: 9 },
    { file: 'pyproject.toml', name: 'uv', type: 'python', priority: 8 },
    { file: 'pubspec.yaml', name: 'flutter pub', type: 'dart', priority: 7 },
    { file: 'go.mod', name: 'go', type: 'go', priority: 6 },
    { file: 'Cargo.toml', name: 'cargo', type: 'rust', priority: 5 },
    { file: 'Package.swift', name: 'swiftpm', type: 'swift', priority: 4 },
    { file: 'yarn.lock', name: 'yarn', type: 'js', priority: 3 },
    { file: 'package-lock.json', name: 'pnpm', type: 'js', priority: 2, warn: 'package-lock.json detected. npm directly is banned by policy. We will generate pnpm commands instead.' },
    { file: 'requirements.txt', name: 'uv', type: 'python', priority: 1, warn: 'requirements.txt detected. pip directly is banned. We will generate uv commands instead.' }
  ],
  frameworks: [
    { file: 'next.config.js', name: 'Next.js', type: 'frontend' },
    { file: 'next.config.mjs', name: 'Next.js', type: 'frontend' },
    { file: 'vite.config.js', name: 'Vite', type: 'frontend' },
    { file: 'vite.config.ts', name: 'Vite', type: 'frontend' },
    { file: 'nuxt.config.js', name: 'Nuxt.js', type: 'frontend' },
    { file: 'nuxt.config.ts', name: 'Nuxt.js', type: 'frontend' },
    { file: 'svelte.config.js', name: 'SvelteKit', type: 'frontend' },
    { file: 'angular.json', name: 'Angular', type: 'frontend' },
    { file: 'tsconfig.json', name: 'TypeScript', type: 'lang' },
    { file: 'pubspec.yaml', name: 'Flutter', type: 'frontend' },
    { file: 'fastapi', name: 'FastAPI', type: 'backend', isDir: false },
    { file: 'django', name: 'Django', type: 'backend', isDir: false }
  ],
  infrastructure: [
    { file: 'firebase.json', name: 'Firebase', type: 'deploy' },
    { file: 'vercel.json', name: 'Vercel', type: 'deploy' },
    { file: 'wrangler.toml', name: 'Cloudflare', type: 'deploy' },
    { file: 'Fastfile', name: 'Fastlane', type: 'deploy' },
    { file: 'fastlane', name: 'Fastlane', type: 'deploy', isDir: true },
    { file: 'Gemfile', name: 'Fastlane', type: 'deploy' },
    { file: 'Dockerfile', name: 'Docker', type: 'infra' },
    { file: 'docker-compose.yml', name: 'Docker Compose', type: 'infra' },
    { file: '.github/workflows', name: 'GitHub Actions', type: 'cicd', isDir: true }
  ]
};

function hasSecureRegularFile(name) {
  const fullPath = path.join(TARGET_DIR, name);
  try {
    const stat = fs.lstatSync(fullPath);
    return stat.isFile() && !stat.isSymbolicLink();
  } catch (_) {
    return false;
  }
}

function isExecutableFile(filePath) {
  try {
    const realPath = fs.realpathSync.native(filePath);
    fs.accessSync(realPath, fs.constants.X_OK);
    const stat = fs.statSync(realPath);
    return stat.isFile();
  } catch (_) {
    return false;
  }
}

function resolveCommand(name) {
  if (name.includes(path.sep)) {
    return isExecutableFile(name) ? name : null;
  }

  const pathEntries = (process.env.PATH || '').split(path.delimiter).filter(Boolean);
  for (const dir of pathEntries) {
    const candidate = path.join(dir, name);
    if (isExecutableFile(candidate)) return candidate;
  }
  return null;
}

function detectGlobalCapabilities() {
  const home = os.homedir();
  const skillChecks = [
    { name: 'agy-imagegen', paths: [
      path.join(home, '.claude/skills/agy-imagegen/SKILL.md'),
      path.join(home, '.agents/skills/agy-imagegen/SKILL.md'),
      path.join(home, '.gemini/config/skills/agy-imagegen/SKILL.md')
    ] },
    { name: 'second-opinion', paths: [
      path.join(home, '.agents/skills/second-opinion/SKILL.md'),
      path.join(home, '.gemini/config/skills/second-opinion/SKILL.md'),
      path.join(home, '.gemini/config/plugins/second-opinion/skills/second-opinion/SKILL.md')
    ] }
  ];

  const skills = {};
  skillChecks.forEach(skill => {
    const foundPath = skill.paths.find(p => {
      try {
        return fs.lstatSync(p).isFile();
      } catch (_) {
        return false;
      }
    });
    skills[skill.name] = foundPath || null;
  });

  const cli = {};
  Object.entries(KNOWN_CLI_PATHS).forEach(([name, candidates]) => {
    cli[name] = candidates.map(resolveCommand).find(Boolean) || null;
  });

  return { skills, cli };
}

// Scan helper
function scanProject() {
  // W2-1: Verify TARGET_DIR is not a symbolic link before reading
  if (fs.existsSync(TARGET_DIR) && fs.lstatSync(TARGET_DIR).isSymbolicLink()) {
    error(`CRITICAL SECURITY ALERT: TARGET_DIR ${TARGET_DIR} is a symbolic link. Scanning aborted.`);
    throw new Error(`Symbolic link rejected at TARGET_DIR`);
  }

  const result = {
    languages: [],
    packageManager: null,
    frameworks: [],
    infrastructure: [],
    databases: [],
    directories: [],
    scripts: [],
    projectTypes: [],
    capabilityHints: [],
    pnpmLockExists: false,
    hasLegacyLockfile: false,
    uvLockExists: false,
    pubspecLockExists: false
  };

  const files = fs.readdirSync(TARGET_DIR);
  const detectedPMs = [];

  if (hasSecureRegularFile('pnpm-lock.yaml')) {
    result.pnpmLockExists = true;
  }
  if (hasSecureRegularFile('yarn.lock') || hasSecureRegularFile('package-lock.json')) {
    result.hasLegacyLockfile = true;
  }
  if (hasSecureRegularFile('uv.lock')) {
    result.uvLockExists = true;
  }
  if (hasSecureRegularFile('pubspec.lock')) {
    result.pubspecLockExists = true;
  }

  // Scan files and directories
  files.forEach(f => {
    const fullPath = path.join(TARGET_DIR, f);
    let stat;
    try { 
      stat = fs.lstatSync(fullPath); 
    } catch (e) { 
      warn(`Failed to inspect file/directory ${f}: ${e.message}. Scan results might be incomplete.`);
      return; 
    }

    // 1. Detect Package Manager
    const pm = DETECTOR.packageManagers.find(item => item.file === f);
    if (pm) {
      detectedPMs.push(pm);
      
      // Map language
      if (pm.type === 'js' && !result.languages.includes('TypeScript/JavaScript')) result.languages.push('TypeScript/JavaScript');
      if (pm.type === 'python' && !result.languages.includes('Python')) result.languages.push('Python');
      if (pm.type === 'dart' && !result.languages.includes('Dart')) result.languages.push('Dart');
      if (pm.type === 'go' && !result.languages.includes('Go')) result.languages.push('Go');
      if (pm.type === 'rust' && !result.languages.includes('Rust')) result.languages.push('Rust');
      if (pm.type === 'swift' && !result.languages.includes('Swift')) result.languages.push('Swift');
    }

    // 2. Detect Frameworks
    const fw = DETECTOR.frameworks.find(item => item.file === f);
    if (fw) {
      if (!result.frameworks.includes(fw.name)) result.frameworks.push(fw.name);
    }

    // 3. Detect Infrastructure/Deploy
    // W2-3: Fix GitHub Actions detection since readdirSync only yields top-level elements (.github)
    // W2-4: Ensure Gemfile Fastlane detection only triggers when fastlane is explicitly present
    const infra = DETECTOR.infrastructure.find(item => {
      if (item.file === '.github/workflows') {
        if (f !== '.github') return false;
        const dotGithub = path.join(TARGET_DIR, '.github');
        const githubWorkflows = path.join(dotGithub, 'workflows');
        try {
          const dotGithubStat = fs.lstatSync(dotGithub);
          if (dotGithubStat.isDirectory() && !dotGithubStat.isSymbolicLink()) {
            const workflowsStat = fs.lstatSync(githubWorkflows);
            return workflowsStat.isDirectory() && !workflowsStat.isSymbolicLink();
          }
        } catch (_) {}
        return false;
      }
      return item.file === f && (item.isDir ? stat.isDirectory() : true);
    });
    if (infra) {
      if (infra.name === 'Fastlane' && f === 'Gemfile') {
        try {
          const gemfileBody = safeReadFile(fullPath);
          if (gemfileBody.includes('fastlane')) {
            if (!result.infrastructure.includes(infra.name)) result.infrastructure.push(infra.name);
          }
        } catch (_) {}
      } else {
        if (!result.infrastructure.includes(infra.name)) result.infrastructure.push(infra.name);
      }
    }

    // Capture standard directory layout for AGENTS.md
    if (stat.isDirectory() && !f.startsWith('.') && f !== 'node_modules') {
      result.directories.push(f);
    }
  });

  // Resolve best package manager based on priority
  if (detectedPMs.length > 0) {
    // Sort descending by priority
    detectedPMs.sort((a, b) => b.priority - a.priority);
    const chosenPM = detectedPMs[0];
    if (chosenPM.name === 'yarn') {
      result.packageManager = 'pnpm';
      warn('yarn.lock detected. Direct use of yarn is banned by policy. We have normalized packageManager to pnpm.');
    } else {
      result.packageManager = chosenPM.name;
      if (chosenPM.warn) warn(chosenPM.warn);
    }
  } else {
    result.packageManager = null; // Default to null if no PM detected to prevent inappropriate code generation
  }

  // Additional Scan of package.json/pyproject.toml if present for deeper insights
  if (files.includes('package.json')) {
    try {
      const pkg = JSON.parse(safeReadFile(path.join(TARGET_DIR, 'package.json')));
      
      // CRITICAL: Strictly validate script names with alphanumeric whitelist to prevent shell injections
      result.scripts = pkg.scripts 
         ? Object.keys(pkg.scripts).filter(name => /^[a-zA-Z0-9._:\-]+$/.test(name)) 
         : [];
      
      const violatingScripts = [];
      if (pkg.scripts) {
        Object.entries(pkg.scripts).forEach(([name, body]) => {
          if (typeof body === 'string') {
            const hasNpm = /\b(npm|npx|pnpx|yarn)\b/.test(body);
            const hasPip = /\b(pip|pip3|pipx)\b|python\s+-m\s+pip\b/.test(body);
            const hasLatest = /@latest\b/.test(body);
            if (hasNpm || hasPip || hasLatest) {
              violatingScripts.push({ name, body });
            }
          }
        });
      }
      result.violatingScripts = violatingScripts;
      
      const allDeps = { ...pkg.dependencies, ...pkg.devDependencies };
      
      if (allDeps['react'] && !result.frameworks.includes('React')) result.frameworks.push('React');
      if (allDeps['vue'] && !result.frameworks.includes('Vue')) result.frameworks.push('Vue');
      if (allDeps['express'] && !result.frameworks.includes('Express')) result.frameworks.push('Express');
      if (allDeps['@nestjs/core'] && !result.frameworks.includes('NestJS')) result.frameworks.push('NestJS');
      if (allDeps['typescript'] && !result.languages.includes('TypeScript')) {
        result.languages.push('TypeScript');
      }
      
      // Detect DB
      if (allDeps['pg'] || (allDeps['prisma'] && pkg.dependencies?.['@prisma/client'])) {
        result.databases.push('PostgreSQL');
      }
      if (allDeps['mongoose'] || allDeps['mongodb']) {
        result.databases.push('MongoDB');
      }
      if (allDeps['firebase-admin'] || allDeps['firebase']) {
        result.databases.push('Firebase Firestore');
      }
      if (allDeps['@supabase/supabase-js']) {
        if (!result.databases.includes('Supabase PostgreSQL')) result.databases.push('Supabase PostgreSQL');
      }
      if (allDeps['drizzle-orm']) {
        if (!result.databases.includes('SQL via Drizzle ORM')) result.databases.push('SQL via Drizzle ORM');
      }
      if (allDeps['better-sqlite3'] || allDeps['sqlite3']) {
        if (!result.databases.includes('SQLite')) result.databases.push('SQLite');
      }
      if (allDeps['phaser'] || allDeps['pixi.js'] || allDeps['@pixi/react']) {
        if (!result.projectTypes.includes('Game')) result.projectTypes.push('Game');
        if (!result.capabilityHints.includes('sprite-assets')) result.capabilityHints.push('sprite-assets');
      }
      if (allDeps['next-seo'] || allDeps['@vercel/og']) {
        if (!result.capabilityHints.includes('ogp-metadata')) result.capabilityHints.push('ogp-metadata');
      }
    } catch (e) {
      warn(`Failed to parse package.json at ${TARGET_DIR}: ${e.message}. Deeper framework/dependency insights skipped.`);
    }
  }

  if (files.includes('pyproject.toml')) {
    try {
      const content = safeReadFile(path.join(TARGET_DIR, 'pyproject.toml'));
      if (content.includes('fastapi') && !result.frameworks.includes('FastAPI')) result.frameworks.push('FastAPI');
      if (content.includes('django') && !result.frameworks.includes('Django')) result.frameworks.push('Django');
      if (content.includes('flask') && !result.frameworks.includes('Flask')) result.frameworks.push('Flask');
      if (content.includes('psycopg2') || content.includes('asyncpg')) {
        if (!result.databases.includes('PostgreSQL')) result.databases.push('PostgreSQL');
      }
      if (content.includes('sqlalchemy') || content.includes('alembic')) {
        if (!result.databases.includes('SQL')) result.databases.push('SQL');
      }
    } catch (e) {
      warn(`Failed to parse pyproject.toml at ${TARGET_DIR}: ${e.message}. Deeper Python insights skipped.`);
    }
  }

  const secureDirExists = (name) => {
    try {
      const stat = fs.lstatSync(path.join(TARGET_DIR, name));
      return stat.isDirectory() && !stat.isSymbolicLink();
    } catch (_) {
      return false;
    }
  };

  const secureFileExists = (name) => hasSecureRegularFile(name);

  if (secureFileExists('schema.prisma')) {
    if (!result.databases.includes('SQL via Prisma')) result.databases.push('SQL via Prisma');
  }
  if (secureDirExists('prisma')) {
    if (!result.databases.includes('SQL via Prisma')) result.databases.push('SQL via Prisma');
  }
  if (secureFileExists('drizzle.config.ts') || secureFileExists('drizzle.config.js')) {
    if (!result.databases.includes('SQL via Drizzle ORM')) result.databases.push('SQL via Drizzle ORM');
  }
  if (secureDirExists('supabase')) {
    if (!result.databases.includes('Supabase PostgreSQL')) result.databases.push('Supabase PostgreSQL');
  }
  if (secureDirExists('migrations') && !result.capabilityHints.includes('db-migrations')) {
    result.capabilityHints.push('db-migrations');
  }

  const webFrameworks = ['Next.js', 'Vite', 'Nuxt.js', 'SvelteKit', 'Angular', 'React', 'Vue'];
  if (result.frameworks.some(fw => webFrameworks.includes(fw))) {
    result.projectTypes.push('Web App');
    if (!result.capabilityHints.includes('ogp-metadata')) result.capabilityHints.push('ogp-metadata');
  }

  if (result.frameworks.includes('Flutter') || secureDirExists('ios') || secureDirExists('android') || secureFileExists('Package.swift')) {
    result.projectTypes.push('Native/Mobile App');
    if (!result.capabilityHints.includes('app-icons')) result.capabilityHints.push('app-icons');
  }

  if (secureFileExists('project.godot') || (secureDirExists('Assets') && secureDirExists('ProjectSettings'))) {
    result.projectTypes.push('Game');
    if (!result.capabilityHints.includes('sprite-assets')) result.capabilityHints.push('sprite-assets');
  }

  result.projectTypes = [...new Set(result.projectTypes)];
  result.capabilityHints = [...new Set(result.capabilityHints)];

  // Safe Fallback defaults ONLY if absolutely empty
  if (result.languages.length === 0) {
    result.languages.push('TypeScript/JavaScript');
  }
  if (result.frameworks.length === 0) {
    if (result.languages.includes('Dart')) {
      result.frameworks.push('Flutter');
    } else {
      result.frameworks.push('Vite/Vanilla HTML5');
    }
  }

  return result;
}

// Generate Directory Structure tree string
function buildDirTree(dirs) {
  if (dirs.length === 0) return '.\n├── (No subdirectories detected yet)';
  return '.\n' + dirs.map((d, i) => {
    const isLast = i === dirs.length - 1;
    return `${isLast ? '└──' : '├──'} ${d}/`;
  }).join('\n');
}

// Setup Packages rules
function getPackageManagerRule(pm) {
  if (pm === 'pnpm' || pm === 'npm' || pm === 'yarn') {
    return `- \`npm\` / \`npx\` / \`npm exec\` / \`npm install -g\` / \`pnpx\` は使用禁止です。pnpm へ置換してください。
- JavaScript/TypeScript は \`/opt/homebrew/bin/pnpm\` を優先します。PATH が通っている場合のみ \`pnpm\` と書いて構いません。
- \`npm install\` は \`pnpm install --frozen-lockfile\`、\`npm install <pkg>\` は \`pnpm add <pkg>\`、\`npm install -D <pkg>\` は \`pnpm add -D <pkg>\`、\`npm run <script>\` は \`pnpm run <script>\`、\`npx <cmd>\` はローカル依存なら \`pnpm exec <cmd>\`、一時実行なら \`pnpm dlx <pkg>@<固定バージョン>\` に置換してください。
- \`@latest\` 指定は禁止です。実行前にバージョンを調査し、固定バージョンと lockfile を使ってください。
- 新規/更新プロジェクトでは \`packageManager\` に pnpm の固定バージョンを設定し、\`pnpm-lock.yaml\` を必ずコミットしてください。lockfile のない依存追加は行わないでください。`;
  } else if (pm === 'uv') {
    return `- Python は \`pip install\` / \`pip3 install\` / \`python -m pip install\` を直接実行禁止です。\`/opt/homebrew/bin/uv\` または \`~/.local/bin/uv\` を使い、\`uv add\`、\`uv sync --locked\`、\`uv run --locked\`、または \`uv pip compile\` + \`uv pip sync\` に置換してください。
- Python CLI ツールの一時実行は \`uvx <tool>@<固定バージョン>\` を優先してください。\`pipx install\` が必要な場合は理由を明示し、グローバル汚染がないことを確認してください。
- 既存プロジェクトが pip/requirements 運用を前提にしている場合は、勝手に実行せず、uv への移行案または一回限りの例外理由を提示してから進めてください。`;
  } else if (pm === 'flutter pub') {
    return `- Dart/Flutter プロジェクトでは、パッケージのインストール・更新に \`flutter pub\` 以外のコマンドを使用しないでください。
- 依存関係の解決には \`flutter pub get\` を使用し、\`pubspec.yaml\` には原則として固定バージョン（または安全な semantic versioning）を指定し、不確定なバージョン指定は避けてください。
- iOS/Android のネイティブビルドやデプロイ自動化には \`fastlane\` を活用し、Gemfile を用いた \`bundle exec fastlane\` での実行を徹底してください。`;
  } else {
    // Basic rules fallback
    return `- 使用するパッケージマネージャー: \`${pm || '未検出'}\`
- パッケージマネージャーが未検出の場合、依存追加や一時実行を勝手に行わず、プロジェクト方針を確認してから pnpm/uv 等の再現可能な手順を選定してください。
- パッケージ追加時は、必ず lockfile が生成/更新される標準的なコマンドを使用し、グローバルな汚染を避けてください。
- 一時的な実行コマンドがある場合は、固定バージョンを指定したクリーンランナーを使用してください。`;
  }
}

function getBuildCommands(pm, infra, scripts = [], pnpmLockExists = false, hasLegacyLockfile = false, uvLockExists = false, pubspecLockExists = false) {
  const commands = {
    install: '',
    lint: '',
    test: '',
    build: '',
    deploy: ''
  };

  if (!pm) {
    commands.install = '# No package manager detected. Clean install skipped.';
    commands.lint = '# No linter command configured';
    commands.test = '# No test command configured';
    commands.build = '# No build command configured';
    commands.deploy = '# No deploy command configured';
  }

  const hasJS = pm === 'pnpm' || pm === 'yarn' || pm === 'npm';

  if (hasJS) {
    if (pnpmLockExists) {
      commands.install = '"$PNPM_BIN" install --frozen-lockfile --ignore-scripts';
    } else if (hasLegacyLockfile) {
      commands.install = trustedRaw(`# Migration Pending: yarn.lock or package-lock.json exists, but pnpm-lock.yaml does not.
printf "\\x1b[1;33m⚠ Migration required: A legacy lockfile was found but pnpm-lock.yaml is missing.\\n\\x1b[0m" >&2
printf "\\x1b[1;33m  Please follow these steps to migrate securely:\\n\\x1b[0m" >&2
printf "\\x1b[1;33m  1. Run '/opt/homebrew/bin/pnpm import' to generate a pnpm-lock.yaml from your legacy lockfile.\\n\\x1b[0m" >&2
printf "\\x1b[1;33m  2. Review the generated pnpm-lock.yaml file changes carefully.\\n\\x1b[0m" >&2
printf "\\x1b[1;33m  3. Run '/opt/homebrew/bin/pnpm install --frozen-lockfile --ignore-scripts' to verify integrity.\\n\\x1b[0m" >&2
printf "\\x1b[1;33m  4. Commit the new pnpm-lock.yaml file.\\n\\x1b[0m" >&2
exit 1`);
    } else {
      commands.install = trustedRaw(`# Lockfile Missing: No pnpm-lock.yaml found.
printf "\\x1b[1;31m✘ Error: pnpm-lock.yaml is missing.\\n\\x1b[0m" >&2
printf "\\x1b[1;31m  Please initialize it by running '/opt/homebrew/bin/pnpm install --ignore-scripts', review the changes, and commit the pnpm-lock.yaml file.\\n\\x1b[0m" >&2
exit 1`);
    }

    commands.lint = scripts.includes('lint') ? '"$PNPM_BIN" run lint' : '# No "lint" script found in package.json';
    commands.test = scripts.includes('test') ? '"$PNPM_BIN" run test' : '# No "test" script found in package.json';
    commands.build = scripts.includes('build') ? '"$PNPM_BIN" run build' : '# No "build" script found in package.json';
    commands.deploy = scripts.includes('deploy') ? '"$PNPM_BIN" run deploy' : '# No "deploy" script found in package.json';
  } else if (pm === 'uv') {
    if (uvLockExists) {
      commands.install = '"$UV_BIN" sync --locked';
    } else if (fs.existsSync(path.join(TARGET_DIR, 'requirements.txt'))) {
      commands.install = trustedRaw(`# Lockfile Missing: requirements.txt is detected, but uv.lock is missing.
printf "\\x1b[1;33m⚠ Warning: uv.lock is missing in a pip-based requirements.txt project.\\n\\x1b[0m" >&2
printf "\\x1b[1;33m  Direct pip usage is banned by workstation security policies. Please migrate using uv:\\n\\x1b[0m" >&2
printf "\\x1b[1;33m  1. Compile requirements with hashes:\\n\\x1b[0m" >&2
printf "\\x1b[1;33m     \\"$UV_BIN\\" pip compile requirements.txt -o requirements.txt --generate-hashes\\n\\x1b[0m" >&2
printf "\\x1b[1;33m  2. Sync dependencies using the compile results:\\n\\x1b[0m" >&2
printf "\\x1b[1;33m     \\"$UV_BIN\\" pip sync requirements.txt\\n\\x1b[0m" >&2
printf "\\x1b[1;33m  Alternatively, initialize pyproject.toml and run \\"$UV_BIN\\" lock to generate uv.lock.\\n\\x1b[0m" >&2
exit 1`);
    } else {
      commands.install = trustedRaw(`# Lockfile Missing: pyproject.toml is detected, but uv.lock is missing.
printf "\\x1b[1;33m⚠ Warning: uv.lock was not found.\\n\\x1b[0m" >&2
printf "\\x1b[1;33m  If you are migrating or initializing a new Python project, run:\\n\\x1b[0m" >&2
printf "\\x1b[1;33m    \\"$UV_BIN\\" lock\\n\\x1b[0m" >&2
printf "\\x1b[1;33m  Review and commit the generated lockfile before running the deploy script.\\n\\x1b[0m" >&2
exit 1`);
    }

    let hasRuff = false;
    let hasPytest = false;
    try {
      const pyproject = safeReadFile(path.join(TARGET_DIR, 'pyproject.toml'));
      hasRuff = pyproject.includes('ruff');
      hasPytest = pyproject.includes('pytest');
    } catch (_) {}

    commands.lint = hasRuff ? '"$UV_BIN" run --locked ruff check' : '# No ruff configured in pyproject.toml';
    commands.test = hasPytest ? '"$UV_BIN" run --locked pytest' : '# No pytest configured in pyproject.toml';
    commands.build = '# No compilation build needed for Python';
    commands.deploy = '# Custom Python deploy command (e.g., "$UV_BIN" run --locked deploy_script.py or custom build & push)';
  } else if (pm === 'flutter pub') {
    if (pubspecLockExists) {
      commands.install = 'flutter pub get --enforce-lockfile';
    } else {
      commands.install = trustedRaw(`# Lockfile Missing: pubspec.yaml is detected, but pubspec.lock is missing.
printf "\\x1b[1;31m✘ Error: pubspec.lock is missing.\\n\\x1b[0m" >&2
printf "\\x1b[1;31m  Please generate it by running 'flutter pub get' locally, review the changes, and commit the pubspec.lock file.\\n\\x1b[0m" >&2
exit 1`);
    }
    commands.lint = 'flutter analyze';
    commands.test = 'flutter test';
    commands.build = 'flutter build apk';
    commands.deploy = '# Custom deploy command (e.g., bundle exec fastlane deploy, or flutter build ipa / apk)';
  } else if (pm === 'go') {
    commands.install = 'go mod download';
    commands.lint = 'go vet ./...';
    commands.test = 'go test ./...';
    commands.build = 'go build -o app';
    commands.deploy = '# Custom deploy commands for Go';
  } else if (pm === 'cargo') {
    commands.install = 'cargo fetch';
    commands.lint = 'cargo clippy -- -D warnings';
    commands.test = 'cargo test';
    commands.build = 'cargo build --release';
    commands.deploy = '# Custom deploy commands for Rust';
  } else if (pm === 'swiftpm') {
    commands.install = 'swift package resolve';
    commands.lint = '# swift package analyze (Use SwiftFormat or SwiftLint if configured)';
    commands.test = 'swift package test';
    commands.build = 'swift build -c release';
    commands.deploy = '# Custom deploy commands for Swift';
  } else {
    commands.install = `# Custom package manager ${pm} install
# Please configure clean install command here.`;
    commands.lint = '# No lint command configured';
    commands.test = '# No test command configured';
    commands.build = '# No build command configured';
    commands.deploy = '# No deploy command configured';
  }

  // Cloud/Deployment Tool Overrides - Enforce non-exclusive merging
  const deploySteps = [];

  if (infra.includes('Fastlane') || pm === 'flutter pub') {
    if (infra.includes('Fastlane')) {
      deploySteps.push({
        name: 'Fastlane',
        cmd: trustedRaw(`# Enforce bundler clean install and deployment mode using Gemfile.lock
if [ ! -f Gemfile.lock ]; then
  printf "\\x1b[1;31m✘ Error: Gemfile.lock is missing. Fastlane deployment requires a locked Gemfile.\\n\\x1b[0m" >&2
  exit 1
fi
if ! command -v bundle >/dev/null 2>&1; then
  printf "\\x1b[1;31m✘ Error: bundler is not found in PATH.\\n\\x1b[0m" >&2
  exit 1
fi
printf "Configuring bundler to strict deployment mode...\\n"
bundle config set --local deployment 'true' || { printf "\\x1b[1;31m✘ Failed to configure bundler to deployment mode.\\n\\x1b[0m"; exit 1; }
bundle config set --local frozen 'true' || { printf "\\x1b[1;31m✘ Failed to configure bundler to frozen lockfile mode.\\n\\x1b[0m"; exit 1; }
bundle install || { printf "\\x1b[1;31m✘ Failed to secure bundler dependencies.\\n\\x1b[0m"; exit 1; }
bundle exec fastlane deploy`)
      });
    } else {
      deploySteps.push({
        name: 'Flutter Build',
        cmd: 'flutter build apk'
      });
    }
  }

  if (infra.includes('Firebase')) {
    deploySteps.push({
      name: 'Firebase Deploy',
      cmd: trustedRaw(`# Secure supply chain: Enforce local installation in devDependencies and frozen lockfile.
if [ -z "\${PNPM_BIN:-}" ]; then
  PNPM_BIN="/opt/homebrew/bin/pnpm"
  if [ ! -x "\$PNPM_BIN" ]; then
    if command -v pnpm >/dev/null 2>&1; then
      PNPM_BIN="pnpm"
    else
      printf "\\x1b[1;31m✘ Error: pnpm was not found at /opt/homebrew/bin/pnpm or in PATH.\\x1b[0m\\n" >&2
      exit 1
    fi
  fi
fi
if [ ! -f pnpm-lock.yaml ] || [ -L pnpm-lock.yaml ]; then
  printf "\\x1b[1;31m✘ Error: pnpm-lock.yaml is missing or is a symbolic link. Supply-chain verification requires a secure regular file.\\n\\x1b[0m" >&2
  exit 1
fi
if [ ! -f package.json ] || [ -L package.json ]; then
  printf "\\x1b[1;31m✘ Error: package.json is missing or is a symbolic link. Secure verification requires a regular file.\\n\\x1b[0m" >&2
  exit 1
fi
# Ensure node_modules is securely populated and strictly frozen before we inspect it
"\$PNPM_BIN" install --frozen-lockfile --ignore-scripts || {
  printf "\\x1b[1;31m✘ Error: Failed to secure frozen node_modules.\\n\\x1b[0m" >&2
  exit 1
}
if ! command -v node >/dev/null 2>&1; then
  printf "\\x1b[1;31m✘ Error: Node.js is not found in PATH.\\n\\x1b[0m" >&2
  exit 1
fi
# Note: This local dependency version comparison is a lightweight runtime verification check.
# Package integrity and dependency mapping are verified at pnpm installation time using
# pnpm-lock.yaml checksums and pnpm cache policies.
EXIT_CODE=0
EXPECTED_FIREBASE_VERSION="${PINNED_VERSIONS.firebase}" node -e '
  try {
    const expected = process.env.EXPECTED_FIREBASE_VERSION;
    const pkg = require("./package.json");
    const ver = pkg.devDependencies?.["firebase-tools"];
    if (ver !== expected) process.exit(1);
    const installed = require("./node_modules/firebase-tools/package.json").version;
    if (installed !== expected) process.exit(2);
  } catch (e) {
    process.exit(3);
  }
' >/dev/null 2>&1 || EXIT_CODE=\$?

if [ \$EXIT_CODE -ne 0 ]; then
  if [ \$EXIT_CODE -eq 1 ]; then
    printf "\\x1b[1;31m✘ Error: firebase-tools version in package.json devDependencies is not locked exactly to ${PINNED_VERSIONS.firebase}.\\n\\x1b[0m" >&2
  elif [ \$EXIT_CODE -eq 2 ]; then
    printf "\\x1b[1;31m✘ Error: firebase-tools installed version does not match locked version ${PINNED_VERSIONS.firebase}.\\n\\x1b[0m" >&2
  else
    printf "\\x1b[1;31m✘ Error: firebase-tools is not installed locally in node_modules.\\n\\x1b[0m" >&2
  fi
  printf "\\x1b[1;31m  Please add/pin \\x22firebase-tools\\x22: \\x22${PINNED_VERSIONS.firebase}\\x22 in package.json devDependencies, run '/opt/homebrew/bin/pnpm install --frozen-lockfile --ignore-scripts', and regenerate/review your lockfile if needed.\\n\\x1b[0m" >&2
  exit 1
fi
# Enforce secure check of executable to prevent symbolic link hijack
# (pnpm creates symlinks in .bin, so we execute via pnpm exec to verify integrity through pnpm itself)
"\$PNPM_BIN" exec firebase deploy`)
    });
  }

  if (infra.includes('Vercel')) {
    deploySteps.push({
      name: 'Vercel Deploy',
      cmd: trustedRaw(`# Secure supply chain: Enforce local installation in devDependencies and frozen lockfile.
if [ -z "\${PNPM_BIN:-}" ]; then
  PNPM_BIN="/opt/homebrew/bin/pnpm"
  if [ ! -x "\$PNPM_BIN" ]; then
    if command -v pnpm >/dev/null 2>&1; then
      PNPM_BIN="pnpm"
    else
      printf "\\x1b[1;31m✘ Error: pnpm was not found at /opt/homebrew/bin/pnpm or in PATH.\\x1b[0m\\n" >&2
      exit 1
    fi
  fi
fi
if [ ! -f pnpm-lock.yaml ] || [ -L pnpm-lock.yaml ]; then
  printf "\\x1b[1;31m✘ Error: pnpm-lock.yaml is missing or is a symbolic link. Supply-chain verification requires a secure regular file.\\n\\x1b[0m" >&2
  exit 1
fi
if [ ! -f package.json ] || [ -L package.json ]; then
  printf "\\x1b[1;31m✘ Error: package.json is missing or is a symbolic link. Secure verification requires a regular file.\\n\\x1b[0m" >&2
  exit 1
fi
# Ensure node_modules is securely populated and strictly frozen before we inspect it
"\$PNPM_BIN" install --frozen-lockfile --ignore-scripts || {
  printf "\\x1b[1;31m✘ Error: Failed to secure frozen node_modules.\\n\\x1b[0m" >&2
  exit 1
}
if ! command -v node >/dev/null 2>&1; then
  printf "\\x1b[1;31m✘ Error: Node.js is not found in PATH.\\n\\x1b[0m" >&2
  exit 1
fi
# Note: This local dependency version comparison is a lightweight runtime verification check.
# Package integrity and dependency mapping are verified at pnpm installation time using
# pnpm-lock.yaml checksums and pnpm cache policies.
EXIT_CODE=0
EXPECTED_VERCEL_VERSION="${PINNED_VERSIONS.vercel}" node -e '
  try {
    const expected = process.env.EXPECTED_VERCEL_VERSION;
    const pkg = require("./package.json");
    const ver = pkg.devDependencies?.["vercel"];
    if (ver !== expected) process.exit(1);
    const installed = require("./node_modules/vercel/package.json").version;
    if (installed !== expected) process.exit(2);
  } catch (e) {
    process.exit(3);
  }
' >/dev/null 2>&1 || EXIT_CODE=\$?

if [ \$EXIT_CODE -ne 0 ]; then
  if [ \$EXIT_CODE -eq 1 ]; then
    printf "\\x1b[1;31m✘ Error: vercel version in package.json devDependencies is not locked exactly to ${PINNED_VERSIONS.vercel}.\\n\\x1b[0m" >&2
  elif [ \$EXIT_CODE -eq 2 ]; then
    printf "\\x1b[1;31m✘ Error: vercel installed version does not match locked version ${PINNED_VERSIONS.vercel}.\\n\\x1b[0m" >&2
  else
    printf "\\x1b[1;31m✘ Error: vercel is not installed locally in node_modules.\\n\\x1b[0m" >&2
  fi
  printf "\\x1b[1;31m  Please add/pin \\x22vercel\\x22: \\x22${PINNED_VERSIONS.vercel}\\x22 in package.json devDependencies, run '/opt/homebrew/bin/pnpm install --frozen-lockfile --ignore-scripts', and regenerate/review your lockfile if needed.\\n\\x1b[0m" >&2
  exit 1
fi
# Enforce secure check of executable to prevent symbolic link hijack
# (pnpm creates symlinks in .bin, so we execute via pnpm exec to verify integrity through pnpm itself)
"\$PNPM_BIN" exec vercel --prod`)
    });
  }

  if (deploySteps.length === 0) {
    commands.deploy = '# No deploy command configured';
  } else if (deploySteps.length === 1) {
    commands.deploy = deploySteps[0].cmd;
  } else {
    // Merge multiple deployment steps into a single combined trustedRaw block
    const contents = deploySteps.map(step => {
      let stepStr = '';
      if (typeof step.cmd === 'object' && step.cmd[TRUSTED_RAW_SYMBOL] === true) {
        stepStr = step.cmd.content;
      } else {
        stepStr = `${step.cmd} || { printf "\\x1b[1;31m✘ ${step.name} failed.\\n\\x1b[0m"; exit 1; }`;
      }
      return `# --- Deploy Step: ${step.name} ---\n${stepStr}`;
    }).join('\n\n');
    
    commands.deploy = trustedRaw(contents);
  }

  return commands;
}

// Helper to safely format commands into scripts/deploy.sh
function formatShellStep(cmd, runCmdPlaceholder, errorMsg, isOptional = true) {
  let isTrustedRaw = false;
  let cmdStr = '';

  if (cmd && typeof cmd === 'object' && cmd[TRUSTED_RAW_SYMBOL] === true) {
    isTrustedRaw = true;
    cmdStr = cmd.content;
  } else if (typeof cmd === 'string') {
    cmdStr = cmd;
  }

  if (!cmdStr) {
    return '# No command configured for this step';
  }

  const lines = cmdStr.split('\n');
  const nonCommentLines = lines.filter(l => l.trim() && !l.trim().startsWith('#'));

  if (nonCommentLines.length === 0) {
    // CRITICAL: Normalize every comment line in multiline comments to prevent raw execution bypasses
    const reasonLines = lines.map(l => {
      const clean = l.replace(/^#\s*/, '').trim();
      return `#   ${clean}`;
    });
    const reason = reasonLines.join('\n');
    if (isOptional) {
      return `# [WARNING] Optional validation step "${errorMsg}" is SKIPPED because no command is configured.
# Workstation guidelines recommend configuring tests and linters to prevent bad deployments.
${reason}
printf "\\x1b[1;33m⚠ Skipped: ${errorMsg} (no command configured).\\n\\x1b[0m" >&2
# To configure and enable this step, uncomment and edit the command below:
# ${runCmdPlaceholder} || { printf "\\x1b[1;31m✘ ${errorMsg}\\n\\x1b[0m"; exit 1; }`;
    } else {
      return `printf "\\x1b[1;31m✘ CRITICAL ERROR: Required step is not configured.\\n\\x1b[0m"
printf "\\x1b[1;31m  Reason:\\n${reason.replace(/([\\\\"\x60$])/g, '\\$&')}\\n\\x1b[0m"
exit 1`;
    }
  }

  // If this is a trusted raw block, return it directly to support complex script/control blocks.
  // NOTE: trustedRaw blocks bypass the normal || { ... exit 1; } wrapper because they are intended
  // to handle their own environment setup, multi-line conditional structures, or explicit fail-closed
  // logic (e.g., custom error output and exit status management). Developers using trustedRaw must
  // ensure the returned block is completely self-failing (e.g., via 'set -euo pipefail' or explicit 'exit 1' gates).
  if (isTrustedRaw) {
    return cmdStr;
  }

  // If the command is multiline, wrap in a subshell block to capture any failures with errorMsg safely
  if (cmdStr.includes('\n')) {
    return `(
${cmdStr}
) || { printf "\\x1b[1;31m✘ ${errorMsg}\\n\\x1b[0m"; exit 1; }`;
  }

  return `${cmdStr} || { printf "\\x1b[1;31m✘ ${errorMsg}\\n\\x1b[0m"; exit 1; }`;
}

// Main Interactive flow
async function main() {
  // W-9: Strictly sanitize and validate PINNED_VERSIONS mapping to prevent shell parameter injection
  for (const [tool, version] of Object.entries(PINNED_VERSIONS)) {
    if (!/^[0-9a-zA-Z\.\-]+$/.test(version)) {
      error(`CRITICAL SECURITY ALERT: Malicious character detected in PINNED_VERSIONS of ${tool}: "${version}". Aborting immediately.`);
      process.exit(1);
    }
  }

  console.log(`\n${COLORS.bold}${COLORS.cyan}================================================================`);
  console.log(`         ANTIGRAVITY GEMINI PROJECT BOOTSTRAPPER`);
  console.log(`================================================================${COLORS.reset}\n`);

  info('Scanning project directory...');
  const detected = scanProject();
  
  console.log(`\n${COLORS.bold}--- Detected Technical Stack ---${COLORS.reset}`);
  console.log(`  Languages: ${COLORS.green}${detected.languages.join(', ')}${COLORS.reset}`);
  console.log(`  Package Manager: ${COLORS.green}${detected.packageManager}${COLORS.reset}`);
  console.log(`  Frameworks: ${COLORS.green}${detected.frameworks.join(', ')}${COLORS.reset}`);
  console.log(`  Databases: ${COLORS.green}${detected.databases.join(', ') || 'None detected (Default: SQL/Firestore/None)'}${COLORS.reset}`);
  console.log(`  Infrastructure: ${COLORS.green}${detected.infrastructure.join(', ') || 'None detected (Default: Local/GitHub)'}${COLORS.reset}`);
  console.log(`--------------------------------\n`);
  
  if (detected.violatingScripts && detected.violatingScripts.length > 0) {
    error(`${COLORS.bold}${COLORS.red}SECURITY CRITICAL ERROR: Prohibited commands (npm, npx, pip, @latest) detected in package.json scripts:${COLORS.reset}`);
    detected.violatingScripts.forEach(s => {
      console.log(`    - script "${COLORS.bold}${sanitizeForConsole(s.name)}${COLORS.reset}": "${COLORS.yellow}${sanitizeForConsole(s.body)}${COLORS.reset}"`);
    });
    error(`Workstation security policies strictly prohibit direct usage of 'npm', 'npx', 'pip', or using '@latest'.`);
    error(`Please refactor these script bodies in package.json to use 'pnpm', 'uv', or pinned dependencies to pass workstation safety check.`);
    console.log(`--------------------------------\n`);
    // C-2: Execute a hard fail-closed abort on security policy violation
    process.exit(1);
  }

  const args = process.argv.slice(2);
  const isSilent = args.includes('--yes') || args.includes('-y') || process.env.NON_INTERACTIVE === 'true';

  let purposeArg = '';
  let specsArg = '';

  args.forEach(arg => {
    if (arg.startsWith('--purpose=')) {
      purposeArg = arg.substring(10);
    } else if (arg.startsWith('--specs=')) {
      specsArg = arg.substring(8);
    }
  });

  let purpose = purposeArg;
  let customSpecs = specsArg;

  if (!isSilent) {
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout
    });

    try {
      const question = (query) => new Promise((resolve) => rl.question(query, resolve));

      const runSetupPrompt = await question(`${COLORS.bold}Would you like to initiate setup with this stack? [Y/n]: ${COLORS.reset}`);
      if (runSetupPrompt.trim().toLowerCase() === 'n') {
        info('Setup aborted by user.');
        rl.close();
        process.exit(0);
      }

      const purposePrompt = await question(`\n${COLORS.bold}Briefly describe the purpose of this project${purpose ? ' (Default: "' + sanitizeForConsole(purpose) + '")' : ' (Optional)'}: ${COLORS.reset}`);
      purpose = purposePrompt.trim() || purpose;
      const specsPrompt = await question(`${COLORS.bold}Add any specific requirements${customSpecs ? ' (Default: "' + sanitizeForConsole(customSpecs) + '")' : ' (e.g. "Use PostgreSQL database", "deploy to Vercel") (Optional)'}: ${COLORS.reset}`);
      customSpecs = specsPrompt.trim() || customSpecs;
    } finally {
      rl.close();
    }
  } else {
    info('Silent setup mode active. Skipping interactive prompts.');
  }

  // Process requirements overriding
  const projectPurpose = purpose.trim() || 'A modern dynamic web application.';
  let targetDBs = [...detected.databases];
  let targetInfra = [...detected.infrastructure];
  let targetLang = [...detected.languages];
  let targetTypes = [...detected.projectTypes];
  let capabilityHints = [...detected.capabilityHints];

  if (customSpecs) {
    const specLower = customSpecs.toLowerCase();
    if (specLower.includes('postgres') && !targetDBs.includes('PostgreSQL')) targetDBs.push('PostgreSQL');
    if (specLower.includes('firestore') && !targetDBs.includes('Firebase Firestore')) targetDBs.push('Firebase Firestore');
    if (specLower.includes('supabase') && !targetDBs.includes('Supabase PostgreSQL')) targetDBs.push('Supabase PostgreSQL');
    if (specLower.includes('sqlite') && !targetDBs.includes('SQLite')) targetDBs.push('SQLite');
    if (specLower.includes('vercel') && !targetInfra.includes('Vercel')) targetInfra.push('Vercel');
    if (specLower.includes('firebase') && !targetInfra.includes('Firebase')) targetInfra.push('Firebase');
    if (specLower.includes('cloud run') && !targetInfra.includes('Google Cloud Run')) targetInfra.push('Google Cloud Run');
    if (specLower.includes('python') && !targetLang.includes('Python')) targetLang.push('Python');
    if (specLower.includes('typescript') && !targetLang.includes('TypeScript')) targetLang.push('TypeScript');
    if (/\b(web|website|seo|ogp|open graph|metadata)\b/.test(specLower)) {
      if (!targetTypes.includes('Web App')) targetTypes.push('Web App');
      if (!capabilityHints.includes('ogp-metadata')) capabilityHints.push('ogp-metadata');
    }
    if (/\b(native|ios|android|mobile|flutter|swift|kotlin)\b/.test(specLower)) {
      if (!targetTypes.includes('Native/Mobile App')) targetTypes.push('Native/Mobile App');
      if (!capabilityHints.includes('app-icons')) capabilityHints.push('app-icons');
    }
    if (/\b(game|sprite|spritesheet|character|pixel art|background transparent|transparent background)\b/.test(specLower)) {
      if (!targetTypes.includes('Game')) targetTypes.push('Game');
      if (!capabilityHints.includes('sprite-assets')) capabilityHints.push('sprite-assets');
      if (!capabilityHints.includes('style-reference-assets')) capabilityHints.push('style-reference-assets');
    }
    if (/\b(image|icon|og image|thumbnail|asset)\b/.test(specLower)) {
      if (!capabilityHints.includes('generated-assets')) capabilityHints.push('generated-assets');
    }
  }

  if (targetDBs.length === 0) targetDBs.push('Not specified');
  if (targetInfra.length === 0) targetInfra.push('Local Environment / GitHub');
  if (targetTypes.length === 0) targetTypes.push('General Software Project');

  targetTypes = [...new Set(targetTypes)];
  capabilityHints = [...new Set(capabilityHints)];
  const globalCapabilities = detectGlobalCapabilities();

  // Start Asset Generation
  info('Creating files...');

  const projectName = path.basename(TARGET_DIR) || 'project';
  const cleanProjectName = sanitizeForMarkdown(projectName);
  const shellProjectSlug = slugForShell(projectName);
  const cleanProjectPurpose = sanitizeForMarkdown(projectPurpose);
  const cleanCustomSpecs = sanitizeForMarkdown(customSpecs);
  const defaultedVanillaFramework = detected.frameworks.length === 1 && detected.frameworks[0] === 'Vite/Vanilla HTML5';
  const suppressDefaultWebFramework = defaultedVanillaFramework && !targetTypes.includes('Web App') && (targetTypes.includes('Native/Mobile App') || targetTypes.includes('Game'));
  const suppressDefaultLanguage = suppressDefaultWebFramework && detected.languages.length === 1 && detected.languages[0] === 'TypeScript/JavaScript' && !detected.packageManager;
  const languageOutput = suppressDefaultLanguage ? ['Not detected'] : targetLang;
  const frameworkOutput = suppressDefaultWebFramework ? ['Not detected'] : detected.frameworks;
  const cleanLanguages = languageOutput.map(sanitizeForMarkdown).join(', ');
  const cleanFrameworks = frameworkOutput.map(sanitizeForMarkdown).join(', ');
  const cleanDBs = targetDBs.map(sanitizeForMarkdown).join(', ');
  const cleanInfra = targetInfra.map(sanitizeForMarkdown).join(', ');
  const cleanPackageManager = sanitizeForMarkdown(detected.packageManager || 'Not detected');
  const cleanProjectTypes = targetTypes.map(sanitizeForMarkdown).join(', ');
  const cleanCapabilityHints = capabilityHints.length ? capabilityHints.map(sanitizeForMarkdown).join(', ') : 'None detected yet';
  const imageGenerationStatus = globalCapabilities.skills['agy-imagegen']
    ? `Available at ${sanitizeForMarkdown(globalCapabilities.skills['agy-imagegen'])}`
    : 'Not detected in global skills. If image generation is required, expose an image-generation skill before asset production.';
  const secondOpinionStatus = globalCapabilities.skills['second-opinion']
    ? `Available at ${sanitizeForMarkdown(globalCapabilities.skills['second-opinion'])}`
    : 'Generated locally by this setup. Global second-opinion skill was not detected.';
  const cliInventory = [
    `Claude CLI: ${globalCapabilities.cli.claude ? sanitizeForMarkdown(globalCapabilities.cli.claude) : 'not detected'}`,
    `Antigravity AGY: ${globalCapabilities.cli.agy ? sanitizeForMarkdown(globalCapabilities.cli.agy) : 'not detected'}`
  ].map(item => `- ${item}`).join('\n');
  const capabilityInventory = [
    `Image-generation skill: ${imageGenerationStatus}`,
    `Second-opinion skill: ${secondOpinionStatus}`,
    `Capability hints: ${cleanCapabilityHints}`
  ].map(item => `- ${item}`).join('\n');

  // 1. Generate GEMINI.md
  info('Generating GEMINI.md...');
  const geminiTemplate = readTemplate('GEMINI.md.template');
  const packageRule = getPackageManagerRule(detected.packageManager);
  
  const techStackSummary = `- Languages: ${cleanLanguages}
- Frameworks: ${cleanFrameworks}
- Package Manager: ${cleanPackageManager}
- Database: ${cleanDBs}
- Infrastructure: ${cleanInfra}
- Project Type: ${cleanProjectTypes}
- Capability Hints: ${cleanCapabilityHints}`;

  const geminiContent = geminiTemplate
    .replaceAll('{{PACKAGE_MANAGER_RULE}}', packageRule)
    .replaceAll('{{TECH_STACK_SUMMARY}}', techStackSummary)
    .replaceAll('{{CAPABILITY_INVENTORY}}', capabilityInventory)
    .replaceAll('{{CLI_INVENTORY}}', cliInventory);

  safeWriteFile(path.join(TARGET_DIR, 'GEMINI.md'), geminiContent);
  success('GEMINI.md generated successfully.');

  // 2. Generate AGENTS.md
  info('Generating AGENTS.md...');
  const agentTemplate = readTemplate('AGENTS.md.template');
  
  const agentContent = agentTemplate
    .replaceAll('{{PROJECT_NAME}}', cleanProjectName)
    .replaceAll('{{PROJECT_PURPOSE}}', cleanProjectPurpose)
    .replaceAll('{{LANGUAGES}}', cleanLanguages)
    .replaceAll('{{FRAMEWORKS}}', cleanFrameworks)
    .replaceAll('{{DATABASES}}', cleanDBs)
    .replaceAll('{{PACKAGE_MANAGER}}', cleanPackageManager)
    .replaceAll('{{DEPLOY_ENV}}', cleanInfra)
    .replaceAll('{{PROJECT_TYPES}}', cleanProjectTypes)
    .replaceAll('{{CAPABILITY_HINTS}}', cleanCapabilityHints)
    .replaceAll('{{CAPABILITY_INVENTORY}}', capabilityInventory)
    .replaceAll('{{CLI_INVENTORY}}', cliInventory)
    .replaceAll('{{DIRECTORY_STRUCTURE}}', buildDirTree(detected.directories.map(sanitizeForMarkdown)))
    .replaceAll('{{FILE_NAMING}}', detected.languages.includes('Python') ? 'snake_case (e.g. main_handler.py)' : 'kebab-case or camelCase (e.g. index.css, app-root.tsx)')
    .replaceAll('{{VARIABLE_NAMING}}', detected.languages.includes('Python') ? 'snake_case for variables, PascalCase for classes' : 'camelCase for variables, PascalCase for components/classes')
    .replaceAll('{{ARCHITECTURE_PATTERN}}', 'Feature-based modular architecture')
    .replaceAll('{{STATE_MANAGEMENT}}', 'Context APIs or local hooks, minimizing global state pollution')
    .replaceAll('{{BANNED_PACKAGES}}', 'Any package without explicitly specified fixed versions in package.json/lockfile')
    .replaceAll('{{SPECIFIC_CONSTRAINT}}', cleanCustomSpecs || 'No specific custom constraints.');

  safeWriteFile(path.join(TARGET_DIR, 'AGENTS.md'), agentContent);
  success('AGENTS.md generated successfully.');

  // 3. Setup official workspace plugin directory for custom agents and skills.
  // Antigravity discovers workspace plugins from .agents/plugins/<plugin-name>/.
  const agentsConfigDir = path.join(TARGET_DIR, '.agents');
  const workspacePluginsDir = path.join(agentsConfigDir, 'plugins');
  const projectPluginDir = path.join(workspacePluginsDir, 'project-agent-kit');
  const agentsDir = path.join(projectPluginDir, 'agents');
  const skillsDir = path.join(projectPluginDir, 'skills');

  safeCreateDir(agentsConfigDir);
  safeCreateDir(workspacePluginsDir);
  safeCreateDir(projectPluginDir);
  safeCreateDir(agentsDir);
  safeCreateDir(skillsDir);
  safeWriteFile(path.join(projectPluginDir, 'plugin.json'), JSON.stringify({
    name: 'project-agent-kit',
    version: '1.0.0',
    description: 'Generated project-specific Antigravity agents and skills for service readiness, verification, and release workflows.',
    author: { name: 'Antigravity Project Setup' },
    license: 'UNLICENSED'
  }, null, 2) + '\n');

  // 4. Generate Capability Research Agent and Skill
  info('Generating capability research assets...');
  const researchTemplate = readTemplate('agents/capability-researcher.md.template');
  const researchContent = researchTemplate
    .replaceAll('{{PROJECT_TYPES}}', cleanProjectTypes)
    .replaceAll('{{CAPABILITY_HINTS}}', cleanCapabilityHints)
    .replaceAll('{{PACKAGE_MANAGER}}', cleanPackageManager)
    .replaceAll('{{CAPABILITY_INVENTORY}}', capabilityInventory)
    .replaceAll('{{CLI_INVENTORY}}', cliInventory);
  safeWriteFile(path.join(agentsDir, 'capability-researcher.md'), researchContent);

  const capabilitySkillDir = path.join(skillsDir, 'capability-discovery-skill');
  safeCreateDir(capabilitySkillDir);
  const capabilitySkillTemplate = readTemplate('skills/capability-discovery-skill.md.template');
  const capabilitySkillContent = capabilitySkillTemplate
    .replaceAll('{{PROJECT_NAME}}', cleanProjectName)
    .replaceAll('{{PROJECT_TYPES}}', cleanProjectTypes)
    .replaceAll('{{CAPABILITY_HINTS}}', cleanCapabilityHints)
    .replaceAll('{{PACKAGE_MANAGER}}', cleanPackageManager)
    .replaceAll('{{CAPABILITY_INVENTORY}}', capabilityInventory)
    .replaceAll('{{CLI_INVENTORY}}', cliInventory);
  safeWriteFile(path.join(capabilitySkillDir, 'SKILL.md'), capabilitySkillContent);
  success('Capability Researcher and Discovery Skill configured.');

  info('Generating quality systems specialist and operational readiness skills...');
  const qualityTemplate = readTemplate('agents/quality-systems-specialist.md.template');
  const qualityContent = qualityTemplate
    .replaceAll('{{PROJECT_TYPES}}', cleanProjectTypes)
    .replaceAll('{{FRAMEWORKS}}', cleanFrameworks)
    .replaceAll('{{DATABASES}}', cleanDBs)
    .replaceAll('{{DEPLOY_ENV}}', cleanInfra)
    .replaceAll('{{CAPABILITY_HINTS}}', cleanCapabilityHints);
  safeWriteFile(path.join(agentsDir, 'quality-systems-specialist.md'), qualityContent);

  const qualitySkillTemplates = [
    ['security-privacy-skill', 'security-privacy-skill.md.template'],
    ['observability-skill', 'observability-skill.md.template'],
    ['test-strategy-skill', 'test-strategy-skill.md.template'],
    ['environment-secrets-skill', 'environment-secrets-skill.md.template'],
    ['release-store-skill', 'release-store-skill.md.template'],
    ['accessibility-skill', 'accessibility-skill.md.template']
  ];
  qualitySkillTemplates.forEach(([dirName, templateName]) => {
    const qualitySkillDir = path.join(skillsDir, dirName);
    safeCreateDir(qualitySkillDir);
    const qualitySkillContent = readTemplate(`skills/${templateName}`)
      .replaceAll('{{PROJECT_NAME}}', cleanProjectName)
      .replaceAll('{{PROJECT_TYPES}}', cleanProjectTypes)
      .replaceAll('{{FRAMEWORKS}}', cleanFrameworks)
      .replaceAll('{{DATABASES}}', cleanDBs)
      .replaceAll('{{DEPLOY_ENV}}', cleanInfra)
      .replaceAll('{{CAPABILITY_HINTS}}', cleanCapabilityHints);
    safeWriteFile(path.join(qualitySkillDir, 'SKILL.md'), qualitySkillContent);
  });
  success('Quality Systems Specialist and operational readiness skills configured.');

  const isWebApp = targetTypes.includes('Web App');
  const isGameProject = targetTypes.includes('Game');
  const needsAssets = isWebApp || targetTypes.includes('Native/Mobile App') || isGameProject || capabilityHints.some(h => h.includes('asset') || h.includes('icon') || h.includes('ogp'));

  if (needsAssets) {
    info('Generating asset pipeline specialist and skill...');
    const assetTemplate = readTemplate('agents/asset-pipeline-specialist.md.template');
    const assetContent = assetTemplate
      .replaceAll('{{PROJECT_TYPES}}', cleanProjectTypes)
      .replaceAll('{{CAPABILITY_HINTS}}', cleanCapabilityHints)
      .replaceAll('{{IMAGE_GENERATION_STATUS}}', imageGenerationStatus)
      .replaceAll('{{PACKAGE_MANAGER}}', cleanPackageManager);
    safeWriteFile(path.join(agentsDir, 'asset-pipeline-specialist.md'), assetContent);

    const assetSkillDir = path.join(skillsDir, 'asset-pipeline-skill');
    safeCreateDir(assetSkillDir);
    const assetSkillTemplate = readTemplate('skills/asset-pipeline-skill.md.template');
    const assetSkillContent = assetSkillTemplate
      .replaceAll('{{PROJECT_NAME}}', cleanProjectName)
      .replaceAll('{{PROJECT_TYPES}}', cleanProjectTypes)
      .replaceAll('{{CAPABILITY_HINTS}}', cleanCapabilityHints)
      .replaceAll('{{IMAGE_GENERATION_STATUS}}', imageGenerationStatus);
    safeWriteFile(path.join(assetSkillDir, 'SKILL.md'), assetSkillContent);
    success('Asset Pipeline Specialist and Skill configured.');
  }

  if (isWebApp) {
    info('Generating web service readiness skill...');
    const webSkillDir = path.join(skillsDir, 'web-service-readiness-skill');
    safeCreateDir(webSkillDir);
    const webSkillTemplate = readTemplate('skills/web-service-readiness-skill.md.template');
    const webSkillContent = webSkillTemplate
      .replaceAll('{{PROJECT_NAME}}', cleanProjectName)
      .replaceAll('{{FRAMEWORKS}}', cleanFrameworks)
      .replaceAll('{{DATABASES}}', cleanDBs)
      .replaceAll('{{DEPLOY_ENV}}', cleanInfra)
      .replaceAll('{{CAPABILITY_HINTS}}', cleanCapabilityHints);
    safeWriteFile(path.join(webSkillDir, 'SKILL.md'), webSkillContent);
    success('Web Service Readiness Skill configured.');
  }

  if (isGameProject) {
    info('Generating game developer specialist and asset workflow...');
    const gameTemplate = readTemplate('agents/game-developer.md.template');
    const gameContent = gameTemplate
      .replaceAll('{{PROJECT_TYPES}}', cleanProjectTypes)
      .replaceAll('{{CAPABILITY_HINTS}}', cleanCapabilityHints)
      .replaceAll('{{IMAGE_GENERATION_STATUS}}', imageGenerationStatus)
      .replaceAll('{{PACKAGE_MANAGER}}', cleanPackageManager);
    safeWriteFile(path.join(agentsDir, 'game-developer.md'), gameContent);

    const gameSkillDir = path.join(skillsDir, 'game-asset-pipeline-skill');
    safeCreateDir(gameSkillDir);
    const gameSkillTemplate = readTemplate('skills/game-asset-pipeline-skill.md.template');
    const gameSkillContent = gameSkillTemplate
      .replaceAll('{{PROJECT_NAME}}', cleanProjectName)
      .replaceAll('{{IMAGE_GENERATION_STATUS}}', imageGenerationStatus);
    safeWriteFile(path.join(gameSkillDir, 'SKILL.md'), gameSkillContent);
    success('Game Developer and Game Asset Pipeline Skill configured.');
  }

  // 5. Generate Mobile or Frontend Developer Agent
  const isMobile = detected.languages.includes('Dart') || detected.frameworks.includes('Flutter') || targetTypes.includes('Native/Mobile App');
  if (isMobile) {
    info('Generating .agents/plugins/project-agent-kit/agents/mobile-developer.md...');
    const mobTemplate = readTemplate('agents/mobile-developer.md.template');
    const mobileTechStack = (detected.frameworks.includes('Flutter') || detected.languages.includes('Dart'))
      ? 'Flutter (Dart)'
      : (cleanLanguages === 'Not detected' ? cleanProjectTypes : `${cleanProjectTypes}${cleanLanguages ? ` / ${cleanLanguages}` : ''}`);
    const mobContent = mobTemplate
      .replaceAll('{{MOBILE_TECH_STACK}}', mobileTechStack)
      .replaceAll('{{PACKAGE_MANAGER}}', cleanPackageManager);
    safeWriteFile(path.join(agentsDir, 'mobile-developer.md'), mobContent);
    success('Mobile Developer Subagent configured.');
  }

  const isFrontend = !isMobile && (isWebApp || (!targetTypes.includes('Native/Mobile App') && !isGameProject && (detected.frameworks.length > 0 || detected.languages.includes('TypeScript/JavaScript'))));
  if (isFrontend) {
    info('Generating .agents/plugins/project-agent-kit/agents/frontend-developer.md...');
    const feTemplate = readTemplate('agents/frontend-developer.md.template');
    const feContent = feTemplate
      .replaceAll('{{FRONTEND_TECH_STACK}}', cleanFrameworks)
      .replaceAll('{{PACKAGE_MANAGER}}', cleanPackageManager);
    safeWriteFile(path.join(agentsDir, 'frontend-developer.md'), feContent);
    success('Frontend Developer Subagent configured.');
  }

  // 6. Generate Backend Developer Agent
  const isBackend = detected.languages.includes('Python') || detected.languages.includes('Go') || detected.languages.includes('Rust') || detected.frameworks.includes('Express') || detected.frameworks.includes('NestJS') || customSpecs.toLowerCase().includes('backend');
  if (isBackend) {
    info('Generating .agents/plugins/project-agent-kit/agents/backend-developer.md...');
    const beTemplate = readTemplate('agents/backend-developer.md.template');
    const beContent = beTemplate
      .replaceAll('{{BACKEND_TECH_STACK}}', cleanLanguages)
      .replaceAll('{{DATABASE_TECH_STACK}}', cleanDBs)
      .replaceAll('{{PACKAGE_MANAGER}}', cleanPackageManager);
    safeWriteFile(path.join(agentsDir, 'backend-developer.md'), beContent);
    success('Backend Developer Subagent configured.');
  }

  // 7. Generate DB Specialist Agent and Skill
  if (targetDBs.filter(db => db !== 'Not specified').length > 0 || isWebApp) {
    info('Generating .agents/plugins/project-agent-kit/agents/db-specialist.md...');
    const dbTemplate = readTemplate('agents/db-specialist.md.template');
    const dbContent = dbTemplate
      .replaceAll('{{DATABASE_TECH_STACK}}', cleanDBs)
      .replaceAll('{{PACKAGE_MANAGER}}', cleanPackageManager);
    safeWriteFile(path.join(agentsDir, 'db-specialist.md'), dbContent);
    success('Database Specialist Subagent configured.');

    const dbSkillDir = path.join(skillsDir, 'db-setup-skill');
    safeCreateDir(dbSkillDir);
    const dbSkillTemplate = readTemplate('skills/db-setup-skill.md.template');
    const dbSkillContent = dbSkillTemplate
      .replaceAll('{{PROJECT_NAME}}', cleanProjectName)
      .replaceAll('{{DATABASES}}', cleanDBs)
      .replaceAll('{{FRAMEWORKS}}', cleanFrameworks)
      .replaceAll('{{PACKAGE_MANAGER}}', cleanPackageManager);
    safeWriteFile(path.join(dbSkillDir, 'SKILL.md'), dbSkillContent);
    success('Database Setup Skill configured.');
  }

  // 8. Generate Deploy Specialist Agent & Custom Deploy Skill
  info('Generating .agents/plugins/project-agent-kit/agents/deploy-specialist.md & skills...');
  const deployTemplate = readTemplate('agents/deploy-specialist.md.template');
  const cicdTool = targetInfra.includes('GitHub Actions') ? 'GitHub Actions' : 'Local / Manual Shell Trigger';
  const cleanCICDTool = sanitizeForMarkdown(cicdTool);
  const deployContent = deployTemplate
    .replaceAll('{{DEPLOY_ENV}}', cleanInfra)
    .replaceAll('{{CICD_TOOL}}', cleanCICDTool)
    .replaceAll('{{PACKAGE_MANAGER}}', cleanPackageManager);
  safeWriteFile(path.join(agentsDir, 'deploy-specialist.md'), deployContent);
  success('Deploy Specialist Subagent configured.');

  // Create Skill
  const deploySkillDir = path.join(skillsDir, 'deploy-skill');
  safeCreateDir(deploySkillDir);

  const skillTemplate = readTemplate('skills/deploy-skill.md.template');
  const cmds = getBuildCommands(
    detected.packageManager, 
    targetInfra, 
    detected.scripts, 
    detected.pnpmLockExists, 
    detected.hasLegacyLockfile,
    detected.uvLockExists,
    detected.pubspecLockExists
  );
  const resolveCommandStr = (c) => (c && typeof c === 'object' && c[TRUSTED_RAW_SYMBOL] === true) ? c.content : (c || '');
  const skillContent = skillTemplate
    .replaceAll('{{PROJECT_NAME}}', cleanProjectName)
    .replaceAll('{{DEPLOY_ENV}}', cleanInfra)
    .replaceAll('{{PACKAGE_MANAGER}}', cleanPackageManager)
    .replaceAll('{{INSTALL_COMMAND}}', resolveCommandStr(cmds.install))
    .replaceAll('{{LINT_COMMAND}}', resolveCommandStr(cmds.lint))
    .replaceAll('{{TEST_COMMAND}}', resolveCommandStr(cmds.test))
    .replaceAll('{{BUILD_COMMAND}}', resolveCommandStr(cmds.build))
    .replaceAll('{{DEPLOY_COMMAND}}', resolveCommandStr(cmds.deploy));

  safeWriteFile(path.join(deploySkillDir, 'SKILL.md'), skillContent);
  success('Deploy Skill (SKILL.md) configured.');

  // Create Second Opinion Skill
  info('Generating .agents/plugins/project-agent-kit/skills/second-opinion-skill/SKILL.md...');
  const secondOpinionSkillDir = path.join(skillsDir, 'second-opinion-skill');
  safeCreateDir(secondOpinionSkillDir);

  const opinionTemplate = readTemplate('skills/second-opinion-skill.md.template');
  const opinionContent = opinionTemplate
    .replaceAll('{{PROJECT_NAME}}', cleanProjectName)
    .replaceAll('{{CLI_INVENTORY}}', cliInventory);

  safeWriteFile(path.join(secondOpinionSkillDir, 'SKILL.md'), opinionContent);
  success('Second Opinion Skill (SKILL.md) configured.');

  // 9. Generate scripts/ build & deploy runners
  const scriptsDir = path.join(TARGET_DIR, 'scripts');
  safeCreateDir(scriptsDir);

  const secondOpinionRunner = `#!/bin/bash
# Generated independent review runner for ${shellProjectSlug}.
# It prefers the Claude CLI, then uses Antigravity AGY when a non-interactive mode is available.
# Antigravity's built-in reviewer subagents (second-opinion-lite / multi-agent-opinion) are the
# in-editor first choice; this script exists for terminal / CI use.
set -euo pipefail

PROJECT_NAME="${shellProjectSlug}"
OUT_DIR="\${TMPDIR:-/tmp}/antigravity-second-opinion-\${PROJECT_NAME}"
mkdir -p "$OUT_DIR"
DIFF_FILE="$OUT_DIR/input.diff"
PROMPT_FILE="$OUT_DIR/review-prompt.md"
SUMMARY_FILE="$OUT_DIR/README.txt"

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git diff HEAD > "$DIFF_FILE"
else
  printf "No git repository detected. Add target files or diff manually before running review.\\n" > "$DIFF_FILE"
fi

cat > "$PROMPT_FILE" <<'PROMPT_EOF'
You are an independent senior code reviewer.

Review the supplied diff/code for:
1. correctness bugs and boundary cases
2. security and data-loss risks
3. state races, async cleanup, and resource leaks
4. type-safety and validation gaps
5. unnecessary complexity that can be simplified without changing behavior

Findings format:
[LEVEL] file:line - concise issue and concrete fix
LEVEL: CRITICAL / WARNING / INFO

End with totals and one of: APPROVE / NEEDS_CHANGES.
PROMPT_EOF

run_claude() {
  local bin="$1"
  if [ -x "$bin" ]; then
    "$bin" -p "$(cat "$PROMPT_FILE")

Diff:
$(cat "$DIFF_FILE")" < /dev/null > "$OUT_DIR/claude-opinion.md"
    return 0
  fi
  return 1
}

find_cmd() {
  command -v "$1" 2>/dev/null || true
}

run_agy_fallback() {
  local bin
  bin="$(find_cmd agy)"
  if [ -z "$bin" ]; then bin="$(find_cmd antigravity)"; fi
  if [ -z "$bin" ]; then return 1; fi

  cat "$PROMPT_FILE" "$DIFF_FILE" > "$OUT_DIR/agy-review-input.md"

  # Current Antigravity CLI documentation is TUI-first. Use only common non-interactive flags
  # when the installed binary advertises them, otherwise fail closed with the prepared prompt.
  local help_text
  help_text="$("$bin" --help 2>&1 || true)"
  if printf "%s" "$help_text" | grep -Eq -- '(^|[[:space:]])(-p|--prompt)(,|[[:space:]])'; then
    "$bin" -p "$(cat "$OUT_DIR/agy-review-input.md")" > "$OUT_DIR/agy-opinion.md"
    return 0
  fi
  if printf "%s" "$help_text" | grep -Eq -- '(^|[[:space:]])exec(,|[[:space:]])'; then
    "$bin" exec "$(cat "$OUT_DIR/agy-review-input.md")" > "$OUT_DIR/agy-opinion.md"
    return 0
  fi

  printf "Antigravity AGY found at %s, but no supported non-interactive review mode was advertised.\\n" "$bin" > "$OUT_DIR/agy-opinion.md"
  printf "Open AGY manually and submit: %s\\n" "$OUT_DIR/agy-review-input.md" >> "$OUT_DIR/agy-opinion.md"
  return 2
}

review_count=0
run_claude "\${CLAUDE_CLI_PATH:-$(command -v claude || echo "$HOME/.local/bin/claude")}" && review_count=$((review_count + 1)) || true

if [ "$review_count" -eq 0 ]; then
  run_agy_fallback && review_count=$((review_count + 1)) || true
fi

{
  printf "Second opinion output directory: %s\\n" "$OUT_DIR"
  printf "Review files generated:\\n"
  find "$OUT_DIR" -maxdepth 1 -type f -name '*opinion.md' -print
} > "$SUMMARY_FILE"

cat "$SUMMARY_FILE"

if [ "$review_count" -eq 0 ]; then
  printf "No automated reviewer completed. Inspect %s and run the fallback manually.\\n" "$OUT_DIR/agy-review-input.md" >&2
  exit 2
fi
`;

  safeWriteFile(path.join(scriptsDir, 'second-opinion-review.sh'), secondOpinionRunner, 0o755);
  success('scripts/second-opinion-review.sh runner created & made executable.');

  // Setup shell placeholders matching detected PM
  const pmKey = (detected.packageManager === 'pnpm' || detected.packageManager === 'yarn' || detected.packageManager === 'npm') ? 'pnpm' : detected.packageManager;
  const placeholders = {
    pnpm: { lint: '"$PNPM_BIN" run lint', test: '"$PNPM_BIN" run test', build: '"$PNPM_BIN" run build', deploy: '"$PNPM_BIN" run deploy' },
    uv: { lint: '"$UV_BIN" run --locked ruff check', test: '"$UV_BIN" run --locked pytest', build: '# No build required', deploy: '"$UV_BIN" run --locked deploy_script.py' },
    'flutter pub': { lint: 'flutter analyze', test: 'flutter test', build: 'flutter build apk', deploy: 'bundle exec fastlane deploy' },
    go: { lint: 'go vet ./...', test: 'go test ./...', build: 'go build -o app', deploy: '# Custom deploy' },
    cargo: { lint: 'cargo clippy -- -D warnings', test: 'cargo test', build: 'cargo build --release', deploy: '# Custom deploy' },
    swiftpm: { lint: '# swift package analyze', test: 'swift package test', build: 'swift build -c release', deploy: '# Custom deploy' }
  };
  const ph = placeholders[pmKey] || { lint: '# run lint', test: '# run test', build: '# run build', deploy: '# run deploy' };

  const pnpmBinResolution = `# Resolve pnpm path securely (prefers /opt/homebrew/bin/pnpm)
PNPM_BIN="$(resolve_tool "/opt/homebrew/bin/pnpm" || resolve_tool "pnpm" || true)"
if [ -z "$PNPM_BIN" ]; then
  printf "\\x1b[1;31m✘ Error: pnpm was not found at /opt/homebrew/bin/pnpm or in PATH.\\x1b[0m\\n" >&2
  exit 1
fi

PNPM_VER="$("$PNPM_BIN" --version)"
if [[ ! "$PNPM_VER" =~ ^(9\\.|10\\.) ]]; then
  printf "\\x1b[1;31m✘ CRITICAL SECURITY ERROR: Unsupported pnpm version %s. Only pnpm versions 9.* and 10.* are allowed.\\n\\x1b[0m" "$PNPM_VER" >&2
  exit 1
fi

printf "Using pnpm binary resolved at: %s (version: %s)\\n" "$PNPM_BIN" "$PNPM_VER"

# W-6: Validate ignore-scripts setting is true in active pnpm config
if [ "$("$PNPM_BIN" config get ignore-scripts)" != "true" ]; then
  printf "\\x1b[1;31m✘ CRITICAL SECURITY ERROR: pnpm ignore-scripts is not configured to true.\\n\\x1b[0m" >&2
  printf "\\x1b[1;31m  Please configure ignore-scripts locally or globally: pnpm config set ignore-scripts true\\n\\x1b[0m" >&2
  exit 1
fi

`;

  let uvBinResolution = '';
  if (pmKey === 'uv') {
    uvBinResolution = `# Resolve uv path securely (prefers /opt/homebrew/bin/uv or ~/.local/bin/uv)
UV_BIN="$(resolve_tool "/opt/homebrew/bin/uv" || resolve_tool "$HOME/.local/bin/uv" || resolve_tool "uv" || true)"
if [ -z "$UV_BIN" ]; then
  printf "\\x1b[1;31m✘ Error: uv was not found at /opt/homebrew/bin/uv, ~/.local/bin/uv, or in PATH.\\x1b[0m\\n" >&2
  exit 1
fi

`;
  }

  let nodeToolchainInstall = '';
  if (pmKey !== 'pnpm') {
    nodeToolchainInstall = `
# If package.json is present, also enforce Node.js toolchain clean install (for Firebase/Vercel)
if [ -f package.json ]; then
  printf "\\x1b[1;34mRunning Node.js toolchain Clean Install...\\x1b[0m\\n"
  if [ ! -f pnpm-lock.yaml ]; then
    printf "\\x1b[1;31m✘ Error: pnpm-lock.yaml is missing. Node.js toolchain must be locked.\\x1b[0m\\n" >&2
    exit 1
  fi
  "\$PNPM_BIN" install --frozen-lockfile --ignore-scripts || { printf "\\x1b[1;31m✘ Node.js toolchain clean install failed.\\x1b[0m\\n"; exit 1; }
fi
`;
  }

  const hasPackageJson = fs.existsSync(path.join(TARGET_DIR, 'package.json'));
  const needsPnpm = pmKey === 'pnpm' || targetInfra.includes('Firebase') || targetInfra.includes('Vercel') || hasPackageJson;
  const pnpmBinResolutionText = needsPnpm ? pnpmBinResolution : '';

  const installPlaceholders = {
    pnpm: '"$PNPM_BIN" install --frozen-lockfile --ignore-scripts',
    uv: '"$UV_BIN" sync --locked',
    'flutter pub': 'flutter pub get',
    go: 'go mod download',
    cargo: 'cargo fetch',
    swiftpm: 'swift package resolve'
  };
  const instPh = installPlaceholders[pmKey] || '# run clean install';

  // Deploy shell script (Using ultra-portable printf instead of echo -e)
  const deployShContent = `#!/bin/bash
# Automatically generated build & deployment wrapper.
# Enforces supply-chain safety rules.
# Note: Verification steps execute trusted project-defined script bodies.

# Enforce strict fail-closed safety (exit immediately on any error or pipeline failures)
# NOTE on trustedRaw: Raw custom block executions bypass normal error trapping and rely on this.
set -euo pipefail

# Secure absolute tool path resolution function that recursively resolves symlinks
resolve_tool() {
  local name="$1"
  local resolved=""
  
  if [[ "$name" = /* ]]; then
    resolved="$name"
  else
    resolved="$(command -v "$name" 2>/dev/null || true)"
  fi
  
  if [ -z "$resolved" ]; then
    return 1
  fi
  
  # Recursively resolve symlink
  local target="$resolved"
  local visited=""
  while [ -L "$target" ]; do
    if [[ "$visited" =~ "(^| )$target( |$)" ]]; then
      # Circular symlink detected
      return 1
    fi
    visited="$visited $target"
    local link_dest
    link_dest="$(readlink "$target")"
    if [[ "$link_dest" = /* ]]; then
      target="$link_dest"
    else
      target="$(dirname "$target")/$link_dest"
    fi
    target="$(cd "$(dirname "$target")" && pwd)/$(basename "$target")"
  done
  
  # Check if absolute path is fully executable and a regular file
  if [ ! -f "$target" ] || [ ! -x "$target" ]; then
    return 1
  fi
  
  # Validate target directory is a trusted system path and NOT in temp/writable areas
  if [[ "$target" =~ ^/tmp/ ]] || [[ "$target" =~ ^/var/tmp/ ]] || [[ "$target" =~ ^/Users/[^/]+/Desktop/ ]]; then
    printf "\\x1b[1;31m✘ CRITICAL SECURITY ERROR: Resolved binary path %s is in an untrusted user directory.\\n\\x1b[0m" "$target" >&2
    return 1
  fi
  
  echo "$target"
  return 0
}

# Runtime Validation Gate: Strictly check package.json scripts for forbidden commands before execution
if [ -f package.json ] && [ ! -L package.json ]; then
  if ! command -v node >/dev/null 2>&1; then
    printf "\\x1b[1;31m✘ CRITICAL SECURITY ERROR: Node.js is required to audit package.json scripts but is missing.\\n\\x1b[0m" >&2
    exit 1
  fi
  node -e '
    const fs = require("fs");
    try {
      const pkg = JSON.parse(fs.readFileSync("package.json", "utf8"));
      if (pkg.scripts) {
        for (const [name, body] of Object.entries(pkg.scripts)) {
          if (typeof body === "string") {
            if (/\\b(npm|npx|pnpx|yarn)\\b/.test(body) || /\\b(pip|pip3|pipx)\\b|python\\s+-m\\s+pip\\b/.test(body) || /@latest\\b/.test(body)) {
              console.error(\`\\x1b[1;31m✘ CRITICAL SECURITY ERROR: Prohibited command (npm, npx, pip, @latest) detected in package.json script "\${name}": "\${body}"\\x1b[0m\`);
              process.exit(1);
            }
          }
        }
      }
    } catch (e) {
      console.error("Failed to parse package.json: " + e.message);
      process.exit(1);
    }
  ' || exit 1
fi

printf "\\x1b[1;35m[Antigravity Deployer] Starting verification & release...\\x1b[0m\\n"

${pnpmBinResolutionText}${uvBinResolution}# 1. Reproducible clean install
printf "\\x1b[1;34m[1/4] Running Clean Install...\\x1b[0m\\n"
${formatShellStep(cmds.install, instPh, 'Clean install failed.', false)}${nodeToolchainInstall}

# 2. Code Linting
printf "\\x1b[1;34m[2/4] Running Code Linting...\\x1b[0m\\n"
${formatShellStep(cmds.lint, ph.lint, 'Lint check failed. Fix errors before deployment.', true)}

# 3. Unit Tests execution
printf "\\x1b[1;34m[3/4] Running Unit Tests...\\x1b[0m\\n"
${formatShellStep(cmds.test, ph.test, 'Test suite failed. Deployment cancelled.', true)}

# 4. Compilation Build & Deploy
printf "\\x1b[1;34m[4/4] Building and Deploying Application...\\x1b[0m\\n"
${formatShellStep(cmds.build, ph.build, 'Application build failed.', true)}
${formatShellStep(cmds.deploy, ph.deploy, 'Deployment failed.', true)}

printf "\\x1b[1;32m✔ Verification and deployment finished successfully!\\x1b[0m\\n"
`;

  safeWriteFile(path.join(scriptsDir, 'deploy.sh'), deployShContent, 0o755);
  success('scripts/deploy.sh runner created & made executable.');

  console.log(`\n${COLORS.bold}${COLORS.green}================================================================`);
  console.log(`        🎉 ANTIGRAVITY GEMINI SETUP COMPLETED SUCCESSFULLY!`);
  console.log(`================================================================${COLORS.reset}`);
  console.log(`Created files:`);
  console.log(`  - ${COLORS.bold}GEMINI.md${COLORS.reset}: Project charter enforcing massive subagent cooperation.`);
  console.log(`  - ${COLORS.bold}AGENTS.md${COLORS.reset}: Comprehensive technical context for the agents.`);
  console.log(`  - ${COLORS.bold}.agents/plugins/project-agent-kit/agents/${COLORS.reset}: Custom subagent roles and requirements.`);
  console.log(`  - ${COLORS.bold}.agents/plugins/project-agent-kit/skills/capability-discovery-skill/${COLORS.reset}: Official-doc capability scouting and safe npx-to-pnpm decisions.`);
  console.log(`  - ${COLORS.bold}.agents/plugins/project-agent-kit/skills/asset-pipeline-skill/${COLORS.reset}: OGP, icon, image generation, sprite, and transparency asset workflows.`);
  console.log(`  - ${COLORS.bold}.agents/plugins/project-agent-kit/skills/db-setup-skill/${COLORS.reset}: Database/storage decision, migration, and validation workflows.`);
  console.log(`  - ${COLORS.bold}.agents/plugins/project-agent-kit/skills/security-privacy-skill/${COLORS.reset}: Auth, authorization, validation, PII, and privacy readiness.`);
  console.log(`  - ${COLORS.bold}.agents/plugins/project-agent-kit/skills/observability-skill/${COLORS.reset}: Logs, metrics, traces, crash reporting, and health checks.`);
  console.log(`  - ${COLORS.bold}.agents/plugins/project-agent-kit/skills/test-strategy-skill/${COLORS.reset}: Risk-based unit, integration, E2E, visual, native, and game test planning.`);
  console.log(`  - ${COLORS.bold}.agents/plugins/project-agent-kit/skills/environment-secrets-skill/${COLORS.reset}: Environment variable inventory, .env.example, and secret boundaries.`);
  console.log(`  - ${COLORS.bold}.agents/plugins/project-agent-kit/skills/release-store-skill/${COLORS.reset}: Versioning, store metadata, signing, Fastlane, and rollout checks.`);
  console.log(`  - ${COLORS.bold}.agents/plugins/project-agent-kit/skills/accessibility-skill/${COLORS.reset}: Web, native, and game accessibility readiness.`);
  console.log(`  - ${COLORS.bold}.agents/plugins/project-agent-kit/skills/deploy-skill/${COLORS.reset}: Tailored step-by-step deploy skills.`);
  console.log(`  - ${COLORS.bold}.agents/plugins/project-agent-kit/skills/second-opinion-skill/${COLORS.reset}: Independent double-check using Antigravity reviewer subagents and the Claude CLI.`);
  console.log(`  - ${COLORS.bold}scripts/deploy.sh${COLORS.reset}: Executable script wrapping clean install, lint, test & deploy.`);
  console.log(`  - ${COLORS.bold}scripts/second-opinion-review.sh${COLORS.reset}: Executable review runner for the Claude CLI and Antigravity AGY.`);
  console.log(`\nYour Google Antigravity Gemini is now armed to run at absolute peak performance.\n`);
}

main().catch((err) => {
  error(`Setup failed due to an error: ${err.message}`);
  console.error(err);
  process.exit(1);
});
