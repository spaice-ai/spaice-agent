# Learnings Log

Captures patterns, corrections, and best practices discovered during sessions.

---

## [LRN-20260503-001] correction

**Logged**: 2026-05-03T09:00:00+10:00
**Priority**: high
**Status**: pending
**Area**: docs

### Summary
Conflated spaice-agent (framework) with Forge (Scope-rebuild) — filed v0.2 plan under `~/jarvis/spaice/forge/` and included Forge-on-Azure as a scope question for the spaice-agent installer.

### Details
Jozef asked 5 scope questions for spaice-agent v0.2.0. I included "Cron on remote Linux? If Forge deploys a spaice-agent to Azure (headless Linux)..." as Q3. Jozef: "What does this have to do with forge?" — correct: Forge is the Scope-rebuild (plan-marking engine for Azure), NOT a spaice-agent deployment target. The two systems share the SPAICE namespace but are completely separate projects.

Namespaces:
- `~/jarvis/spaice/forge/` → Scope-rebuild (plan-marking engine, Azure Container Apps)
- `~/jarvis/spaice/agent/` → spaice-agent framework (memory middleware, runs on any Hermes host)

### Suggested Action
Before writing multi-project plans, explicitly identify:
1. Which project this plan belongs to
2. Which namespace(s) its artifacts live in
3. If the plan references OTHER projects, clarify why and how they relate — don't silently mix.

Applied immediately: moved plan to `~/jarvis/spaice/agent/v0.2-PLAN.md`.

### Metadata
- Source: user_feedback
- Related Files: ~/jarvis/spaice/agent/v0.2-PLAN.md
- Tags: namespace-discipline, project-boundaries, spaice-structure
- Pattern-Key: harden.project_boundary_discipline
- Recurrence-Count: 1
- First-Seen: 2026-05-03
- Last-Seen: 2026-05-03

---

