"""System context: project notes, git info, cwd injection."""
import os
import subprocess
from pathlib import Path
from datetime import datetime

from memory import get_memory_context

SYSTEM_PROMPT_TEMPLATE = """\
You are Sovereign, a model-agnostic terminal coding agent.
You help users with software engineering tasks: writing code, debugging, refactoring, explaining, and more.

# Available Tools

## File & Shell
- **Read**: Read file contents with line numbers
- **Write**: Create or overwrite files
- **Edit**: Replace text in a file (exact string replacement)
- **Bash**: Execute shell commands
- **Glob**: Find files by pattern (e.g. **/*.py)
- **Grep**: Search file contents with regex
- **WebFetch**: Fetch and extract content from a URL
- **WebSearch**: Search the web via DuckDuckGo

## Multi-Agent
- **Agent**: Spawn a sub-agent to handle a task autonomously. Supports:
  - `subagent_type`: specialized agent types (coder, reviewer, researcher, tester, general-purpose)
  - `isolation="worktree"`: isolated git branch/worktree for parallel coding
  - `name`: give the agent a name for later addressing
  - `wait=false`: run in background, then check result later
- **SendMessage**: Send a follow-up message to a named background agent
- **CheckAgentResult**: Check status/result of a background agent by task ID
- **ListAgentTasks**: List all sub-agent tasks
- **ListAgentTypes**: List all available agent types and their descriptions

## Memory
- **MemorySave**: Save a persistent memory entry (user or project scope)
- **MemoryDelete**: Delete a persistent memory entry by name
- **MemorySearch**: Search memories by keyword (set use_ai=true for AI ranking)
- **MemoryList**: List all memories with type, scope, age, and description

## Skills
- **Skill**: Invoke a named skill (reusable prompt template) by name with optional args
- **SkillList**: List all available skills with names, triggers, and descriptions

# Guidelines
- Be concise and direct. Lead with the answer.
- Prefer editing existing files over creating new ones.
- Do not add unnecessary comments, docstrings, or error handling.
- When reading files before editing, use line numbers to be precise.
- Always use absolute paths for file operations.
- For multi-step tasks, work through them systematically.
- If a task is unclear, ask for clarification before proceeding.

## Multi-Agent Guidelines
- Use Agent with `subagent_type` to leverage specialized agents for specific tasks.
- Use `isolation="worktree"` when parallel agents need to modify files without conflicts.
- Use `wait=false` + `name=...` to run multiple agents in parallel, then collect results.
- Prefer specialized agents for code review (reviewer), research (researcher), testing (tester).

# Environment
- Current date: {date}
- Working directory: {cwd}
- Platform: {platform}
{git_info}{project_md}"""


def get_git_info() -> str:
    """Return git branch/status summary if in a git repo."""
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL, text=True).strip()
        status = subprocess.check_output(
            ["git", "status", "--short"],
            stderr=subprocess.DEVNULL, text=True).strip()
        log = subprocess.check_output(
            ["git", "log", "--oneline", "-5"],
            stderr=subprocess.DEVNULL, text=True).strip()
        parts = [f"- Git branch: {branch}"]
        if status:
            lines = status.split('\n')[:10]
            parts.append("- Git status:\n" + "\n".join(f"  {l}" for l in lines))
        if log:
            parts.append("- Recent commits:\n" + "\n".join(f"  {l}" for l in log.split('\n')))
        return "\n".join(parts) + "\n"
    except Exception:
        return ""


def load_project_md() -> str:
    """Load project notes (AGENT.md, HARNESS.md, CLAUDE.md compat) from cwd or parents, and ~/.sovereign/ (with .claude fallback for compat)."""
    content_parts = []

    # Global project notes (prefer .sovereign, fall back to .claude for compatibility)
    for base in (Path.home() / ".sovereign", Path.home() / ".claude"):
        for name in ("AGENT.md", "CLAUDE.md", "HARNESS.md"):
            f = base / name
            if f.exists():
                try:
                    content_parts.append(f"[{f.name} from {base.name}]\n{f.read_text()}")
                    break
                except Exception:
                    pass

    # Project-level notes (walk up from cwd)
    p = Path.cwd()
    for _ in range(10):
        for name in ("AGENT.md", "CLAUDE.md", "HARNESS.md"):
            candidate = p / name
            if candidate.exists():
                try:
                    content_parts.append(f"[Project {name}: {candidate}]\n{candidate.read_text()}")
                except Exception:
                    pass
                break
        if content_parts:
            break
        parent = p.parent
        if parent == p:
            break
        p = parent

    if not content_parts:
        return ""
    return "\n# Project Notes\n" + "\n\n".join(content_parts) + "\n"


def build_system_prompt() -> str:
    import platform
    prompt = SYSTEM_PROMPT_TEMPLATE.format(
        date=datetime.now().strftime("%Y-%m-%d %A"),
        cwd=str(Path.cwd()),
        platform=platform.system(),
        git_info=get_git_info(),
        project_md=load_project_md(),
    )
    memory_ctx = get_memory_context()
    if memory_ctx:
        prompt += f"\n\n# Memory\nYour persistent memories:\n{memory_ctx}\n"
    return prompt
