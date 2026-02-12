---
name: devsquad-dispatch
description: Activated when facing research tasks, bulk file reading, codebase analysis, code generation, or testing work. Provides contextual delegation recommendations based on task type and agent capabilities. Returns JSON only.
version: 1.0.0
---

# DevSquad Dispatch Skill

Routes tasks to the most appropriate agent based on task type analysis.

## When to Use

Invoke when you need to decide which agent should handle a task:
- Research or investigation work
- Bulk file reading or codebase analysis
- Code generation or scaffolding
- Test writing or QA
- Implementation or refactoring

## Usage

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/devsquad-dispatch/scripts/route-task.sh" "<task description>"
```

## Output Format

Returns JSON with three fields:
```json
{
  "recommended_agent": "gemini-researcher|gemini-reader|gemini-developer|gemini-tester|codex-developer|self",
  "command": "@agent-name \"task description with bounds\"",
  "reason": "Why this agent is recommended"
}
```

## Decision Tree

| Task Pattern | Agent | Rationale |
|--------------|-------|-----------|
| research, investigate, find out, look up | @gemini-researcher | 1M context, web knowledge |
| read file, analyze file, understand codebase, summarize | @gemini-reader | 1M context for large files |
| write test, test coverage, unit test | @gemini-tester | QA with full codebase context |
| implement, refactor, code change | @gemini-developer | 1M context for codebases |
| generate, boilerplate, scaffold, prototype | @codex-developer | Fast code generation |
| synthesize, decide, integrate, architect | self | Requires Claude's judgment |

## Example

Task: "research the best approach for WebSocket implementation"

Output:
```json
{
  "recommended_agent": "gemini-researcher",
  "command": "@gemini-researcher \"research the best approach for WebSocket implementation. Under 500 words.\"",
  "reason": "Research task benefits from Gemini's 1M context and web knowledge."
}
```
