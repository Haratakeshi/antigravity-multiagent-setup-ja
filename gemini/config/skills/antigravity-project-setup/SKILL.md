---
name: antigravity-project-setup
description: Bootstrap project rules, guidelines, and subagents.
---

# Antigravity Gemini Project Bootstrapper Skill

This skill bootstraps a local repository/workspace with Antigravity plugin structure, operating constraints, subagent guidelines, local project skills, and package supply chain protections.

## How to Trigger
Invoke this skill whenever the user requests:
- "Initialize the project for Gemini / Antigravity"
- "Setup project guidelines"
- "Bootstrap the workspace"
- "Configure specialized subagents"
- "Prepare /antigravity-setup" or similar commands.

---

## Execution Guide

Execute the zero-dependency Node.js setup bootstrapper script installed at `~/.agent/antigravity-project-setup/setup.js` together with its `templates/` directory.

### Step 1: Gather Parameters
Retrieve the project's high-level purpose and specific technical requirements from the user's conversation history if available.
- `PURPOSE`: Brief description of the project (e.g., "Premium dashboard for analytics").
- `SPECS`: Specific constraints (e.g., "Use PostgreSQL database, deploy to Firebase App Hosting").
- Include service-readiness requirements when known: Web OGP/SEO/DB/Auth, Native app icons/store assets, Game sprites/transparent backgrounds/style references, AGY native image generation needs, deployment provider, official plugin/MCP needs.

### Step 2: Execute Setup
Run the script non-interactively using the `-y` flag from the **project root directory** (current workspace):

```bash
SCRIPT_PATH="$HOME/.agent/antigravity-project-setup/setup.js"
if [ ! -f "$SCRIPT_PATH" ]; then
  printf 'antigravity-project-setup setup.js was not found at %s.\n' "$SCRIPT_PATH" >&2
  exit 1
fi
node "$SCRIPT_PATH" -y \
  --purpose="$PURPOSE" \
  --specs="$SPECS"
```

### Step 3: Verify Assets
After the script completes, verify the created assets in the workspace:
1. **`GEMINI.md`**: Operating constraints — output discipline, side-effect boundary, audit independence, supply chain rules, and the 24,000-byte rule-file size limit.
2. **`AGENTS.md`**: Captures directories, stack context, project type, capability hints, CLI inventory, naming rules, and bans.
3. **`.agents/plugins/project-agent-kit/plugin.json`**: Marks the generated workspace customization as an official Antigravity workspace plugin.
4. **`.agents/plugins/project-agent-kit/agents/` & `skills/`**: Project-tailored subagents and skills.
5. **`scripts/deploy.sh` & `scripts/second-opinion-review.sh`**: Helper build/review scripts.

### Step 4: Legacy Config Handling
If legacy `.gemini/agents/`, `.gemini/skills/`, or a singular `AGENT.md` already exist in the target workspace, do **not** delete them immediately:
1. Compare them against the new `.agents/plugins/project-agent-kit/` content and migrate any hand-written project rules.
2. Move legacy files to a timestamped backup directory such as `.setup-backups/legacy-antigravity-setup-YYYYMMDD-HHMMSS/`.
