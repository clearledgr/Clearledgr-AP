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

## Design System
Always read DESIGN.md before making any visual or UI decisions.
All font choices, colors, spacing, and aesthetic direction are defined there.
Do not deviate without explicit user approval.
In QA mode, flag any code that doesn't match DESIGN.md.
Brand colors (Solden rebrand, active 2026-05-02): navy `#001137` (sampled from `ui/web-app/public/solden-lockup-dark.png`) + teal `#18BFB0`. Use the CSS tokens `var(--cl-navy)` and `var(--cl-teal-500)`, not hardcoded hex. NOT terracotta/orange.

## API endpoints — strict-profile allowlist (READ BEFORE ADDING NEW ROUTES)

Production runs in **strict surface mode** (`STRICT_PROFILE_ACTIVE=True`).
On startup, `_apply_runtime_surface_profile()` in `main.py` walks every
mounted route and **silently drops** any path not on one of the allowlists
defined in `main.py:STRICT_PROFILE_ALLOWED_*`.

This means: a freshly registered endpoint can pass tests, ship, and
silently 404 in prod for weeks before anyone notices. It happened with
`/api/workspace/settings/match-config` (commit `b805591` → caught in
`7fb5d68`).

**When adding any new endpoint, add its full path to the matching
allowlist set in `main.py`:**

| Path prefix                       | Allowlist constant                          |
|----------------------------------|---------------------------------------------|
| `/api/workspace/<x>`             | `STRICT_PROFILE_ALLOWED_WORKSPACE_PATHS`    |
| `/api/ops/<x>`                   | `STRICT_PROFILE_ALLOWED_OPS_PATHS`          |
| `/api/ap/<x>`                    | `STRICT_PROFILE_ALLOWED_AP_PATHS`           |
| `/api/auth/<x>`                  | `STRICT_PROFILE_ALLOWED_AUTH_PATHS`         |
| Gmail extension paths            | `STRICT_PROFILE_ALLOWED_EXTENSION_PATHS`    |
| User-scoped paths                | `STRICT_PROFILE_ALLOWED_USER_PATHS`         |
| Agent intent / interactive paths | `STRICT_PROFILE_ALLOWED_AGENT_PATHS` etc.   |
| Dynamic prefixes (e.g. `/saml/`) | `STRICT_PROFILE_ALLOWED_PREFIXES`           |

Verify after adding:

```python
import main
print(any(getattr(r, "path", "") == "/api/workspace/your-new-endpoint"
          for r in main.app.routes))
```

If it prints `False`, the allowlist edit didn't take effect.
