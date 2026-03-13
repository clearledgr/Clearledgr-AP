# Claude Code Configuration

## gstack

Use the `/browse` skill from gstack for all web browsing. Never use `mcp__claude-in-chrome__*` tools.

Available gstack skills:
- `/plan-ceo-review` — CEO-level plan review
- `/plan-eng-review` — Engineering plan review
- `/review` — Code review
- `/ship` — Ship workflow
- `/browse` — Web browsing (use this instead of MCP chrome tools)
- `/retro` — Retrospective

If gstack skills aren't working, rebuild by running: `cd .claude/skills/gstack && ./setup`
