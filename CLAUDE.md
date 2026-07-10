# Agent Instructions — Documentation-Driven Development

> Auto-loaded by **Claude Code** (`CLAUDE.md`) and **GitHub Copilot** (`.github/copilot-instructions.md → ../CLAUDE.md`).  
> Edit `CLAUDE.md` only — the Copilot path is a symlink.

---

## Core Principle

All project-specific context lives in `.ai/`. It is the **single source of truth**.  
Read it before writing code, proposing architecture, or making structural decisions.

### Folder Layout

```
.ai/
├── README.md                  ← human entry point; explains this system
├── 00_CONSTRAINTS.md          ← hard rules: forbidden patterns, required tech
├── 00_PHILOSOPHY.md           ← design principles, architectural style, rationale
├── 02_ROADMAP.md              ← current phase, scope boundaries
├── 03_CURRENT_STATUS.md       ← active state only: NOW / NEXT / KNOWN_ISSUES
├── decisions/
│   ├── index.md               ← one-liner per ADR + current status
│   └── ADR-NNN-title.md       ← one file per architectural decision
├── sessions/
│   └── YYYY-MM-DD.md          ← one file per session, never deleted
└── archive/
    └── YYYY-MM-status.md      ← monthly rotation of completed status entries
```

---

## When to Read `.ai/` Docs

| Request type | Action |
|---|---|
| Code generation, refactoring, debugging, architecture | **Must** run the doc check below |
| Explanation, comparison, general question | Load `03_CURRENT_STATUS.md` only if it helps context |
| User says "ignore docs" | Note the skip explicitly in your reply; resume checks on the next request |

---

## Doc Check (mandatory for code/architecture work)

Load in this order. Answer the listed question before moving to the next step.

**Step 1 — `.ai/03_CURRENT_STATUS.md`**  
What exists? What is broken? What is actively in progress or blocked?

**Step 2 — `.ai/00_CONSTRAINTS.md`**  
What technologies or patterns are forbidden? What is required?

**Step 3 — `.ai/02_ROADMAP.md`**  
Is this task in scope for the current phase?

**Step 4 — `.ai/decisions/index.md`**  
Has this approach been decided or rejected? If a relevant ADR is listed, load that individual file.

All steps clear and consistent → proceed to generation.

**Load `.ai/00_PHILOSOPHY.md` when:** the task involves architecture, new module design, or onboarding a new pattern. Not required for routine coding.

---

## Handling Problems

**File missing:**
```
⚠️ .ai/[path] not found.
I cannot proceed safely. Please create it — template is at the end of this file.
```

**File stale** (doc state contradicts the code):
```
⚠️ .ai/03_CURRENT_STATUS.md appears stale.
The code shows [X] but the doc says [Y].
Update it before I continue.
```

**Conflict detected** (request contradicts a constraint, scope, or ADR):
```
⚠️ CONFLICT: [what was requested] contradicts [rule] in [file].
Options:
  (a) I write code that conforms to the docs — say "proceed".
  (b) The docs are outdated — tell me what changed and I will draft the update.
Do not pick for me.
```

**When two `.ai/` files contradict each other:**  
`00_CONSTRAINTS.md` always wins over `decisions/` — hard constraints override past decisions.  
Exception: an ADR with an explicit `Waiver:` field that names and quotes the overridden constraint.

**When a decision is ambiguous:**  
Do not guess. Reason through the alternatives explicitly, state your recommendation, and ask for confirmation before generating code.

---

## Decisions: ADR Workflow

Every significant architectural choice must be recorded as an ADR.

**When to create an ADR:**
- Choosing between two non-trivial technical approaches
- Explicitly rejecting an approach (so it is not re-proposed later)
- Overriding a constraint from `00_CONSTRAINTS.md`
- Any choice that would surprise a new contributor

**Numbering:** increment from the highest existing number in `decisions/index.md`.

**In-code reference convention:**
```python
# ADR-007: why we avoid connection pooling here
```
```typescript
// ADR-012: pagination strategy — see .ai/decisions/ADR-012-pagination.md
```

This convention must be used whenever code directly implements or depends on an ADR.  
It costs nothing and gives both humans and agents a direct path from code to reasoning.

**When loading decisions:** always load `decisions/index.md` first. Load individual ADR files
only when the index indicates the decision is relevant to the current task. Never load all
ADR files speculatively.

---

## Session Workflow

### Opening a session
1. Load `03_CURRENT_STATUS.md` — what is the current state?
2. Create `sessions/YYYY-MM-DD.md` if it does not exist yet today.
3. Write the session goals to that file.

### During the session
- Append notes, findings, and dead ends to `sessions/YYYY-MM-DD.md` as you go.
- Dead ends are as important as progress — record what was tried and why it failed.

### Closing a session
Remind the user to:

1. **Update `03_CURRENT_STATUS.md`** — move done items to `archive/`, update blockers, revise NEXT
2. **Create or update ADRs** for any architectural choices made — update `decisions/index.md`
3. **Finalize `sessions/YYYY-MM-DD.md`** — add a carry-over section; do not delete the file

End every session with:
```
✅ Session complete.
Update: [which .ai/ files need changes]
New ADRs needed: [list any decisions made that are not yet recorded]
Carry-over: [unresolved conflicts or open TODOs for next session]
```

**Session files are never deleted.** They form the development diary of the project.

---

## Status File Lifecycle

`03_CURRENT_STATUS.md` contains only the **active** state: what is happening right now.

- Completed items are moved to `archive/YYYY-MM-status.md` (monthly rotation)
- The file should rarely exceed 40-50 lines; if it does, it is time to archive
- The `_Last updated:` field is the staleness signal — always update it

---

## Response Standards

- **Cite sources** — name every `.ai/` file consulted: "Per `00_CONSTRAINTS.md`, …"
- **Surface conflicts explicitly** — never silently work around them
- **Reference ADRs in generated code** — add the `# ADR-NNN` comment whenever relevant
- **Close the loop** — every code-generating response ends with a reminder to update `03_CURRENT_STATUS.md` and, if a new decision was made, to create an ADR

---

## Agent-Specific Behaviour

**Claude Code** — You have a large context window. On complex tasks, load all four core
`.ai/` files upfront and cross-check them before generating. Read actual source files
before making assumptions about the codebase. Ask for full stack traces and relevant logs
before debugging. Reason through alternatives explicitly before recommending one.

**GitHub Copilot** — Context budget is tighter. Load `03_CURRENT_STATUS.md` and
`00_CONSTRAINTS.md` first. Load `02_ROADMAP.md` and `decisions/index.md` only when the
task is planning-related or a conflict seems likely. Load individual ADR files only when
the index flags a direct hit.

---

## Per-Module Context (larger projects)

Services or modules can have their own `.ai/` subfolder:

```
repo/
├── .ai/                          ← global: constraints, philosophy, roadmap
└── services/
    ├── auth/
    │   └── .ai/                  ← auth-specific constraints and local ADRs
    └── billing/
        └── .ai/                  ← billing-specific context
```

**Loading rule:** load the nearest `.ai/` folder first, then the root `.ai/` folder.
Local rules override global ones, unless the global rule is marked `# GLOBAL — no local override`.

---

## Bootstrap: Starting a New Repository

If `.ai/` does not exist, create the full structure before writing any code.
Populate `00_CONSTRAINTS.md` and `03_CURRENT_STATUS.md` first — these are the minimum
viable context for the agent to work safely.

```bash
mkdir -p .ai/decisions .ai/sessions .ai/archive
touch .ai/README.md
touch .ai/00_CONSTRAINTS.md
touch .ai/00_PHILOSOPHY.md
touch .ai/02_ROADMAP.md
touch .ai/03_CURRENT_STATUS.md
touch .ai/decisions/index.md
```

---

## File Templates

### `.ai/README.md`
```markdown
# .ai/ — Project Memory

This folder is the single source of truth for project context.
It is read by AI coding agents (Claude Code, GitHub Copilot) and by human contributors.

## Files at a glance

| File | Read when |
|---|---|
| `00_CONSTRAINTS.md` | Every coding session — hard rules |
| `00_PHILOSOPHY.md` | Architecture work, onboarding, new patterns |
| `02_ROADMAP.md` | Planning, scope questions |
| `03_CURRENT_STATUS.md` | Start of every session — current state |
| `decisions/index.md` | Conflict checks, architecture decisions |
| `decisions/ADR-NNN-*.md` | When the index flags a relevant decision |
| `sessions/YYYY-MM-DD.md` | Debugging, "why did we do this" questions |
| `archive/` | Historical status — rarely needed |

## New contributor?
Start here: `00_PHILOSOPHY.md` → `02_ROADMAP.md` → `03_CURRENT_STATUS.md`

## New agent session?
Start here: `03_CURRENT_STATUS.md` → `00_CONSTRAINTS.md` → `02_ROADMAP.md` → `decisions/index.md`
```

---

### `.ai/00_CONSTRAINTS.md`
```markdown
# Constraints

Hard rules. These are non-negotiable unless an ADR with a Waiver field explicitly overrides one.

## Required Technologies
- Must use: [technology] — Reason: [why]

## Forbidden Technologies
- Never use: [technology] — Reason: [why]

## Forbidden Patterns
- [Pattern]: [why it is forbidden]

## External Boundaries
- [e.g. no outbound calls from worker threads, no synchronous DB access in request handlers]

_Last updated: YYYY-MM-DD_
```

---

### `.ai/00_PHILOSOPHY.md`
```markdown
# Design Philosophy

Guidance, not rules. These evolve as the project matures.

## Core Principles
- [Principle]: [What it means in practice for this codebase]

## Architectural Style
- [e.g. prefer explicit over implicit, flat over nested, boring over clever]

## What We Optimise For
- [e.g. readability over performance, correctness over speed of delivery]

## What We Accept as Trade-offs
- [e.g. some duplication is acceptable to avoid the wrong abstraction]

_Last updated: YYYY-MM-DD_
```

---

### `.ai/02_ROADMAP.md`
```markdown
# Roadmap

## Current Phase: [name]
**Goal:** [one sentence]
**Target date:** YYYY-MM-DD

### In scope
- [list]

### Out of scope
- [list]

## Upcoming Phases
- [Phase name]: [one-line goal]

## Completed Phases
- [Phase name] — completed YYYY-MM-DD
```

---

### `.ai/03_CURRENT_STATUS.md`
```markdown
# Current Status

## NOW
- [ ] What is actively in progress
- [ ] What is blocked — [reason]

## NEXT
- [ ] Next priority after NOW clears
- [ ] Then...

## KNOWN_ISSUES
- Bug: [description] — Workaround: [if any] — Impact: [low/medium/high]
- Debt: [description] — Impact: [low/medium/high]

_Last updated: YYYY-MM-DD_
```

---

### `.ai/decisions/index.md`
```markdown
# Decision Index

| ADR | Title | Status | Date |
|---|---|---|---|
| ADR-001 | [title] | Active | YYYY-MM-DD |
| ADR-002 | [title] | Superseded by ADR-005 | YYYY-MM-DD |
| ADR-003 | [title] | Rejected | YYYY-MM-DD |
```

---

### `.ai/decisions/ADR-NNN-title.md`
```markdown
# ADR-NNN: [Title]

- **Status:** Active | Rejected | Superseded by ADR-NNN
- **Date:** YYYY-MM-DD
- **Author:** [name or "session YYYY-MM-DD"]

## Context
[What situation or problem prompted this decision?]

## Decision
[What was decided and why?]

## Alternatives Considered
- [Alternative A]: rejected because [reason]
- [Alternative B]: rejected because [reason]

## Consequences
- Positive: [what becomes easier]
- Negative: [what becomes harder or is accepted as a trade-off]

## Waiver
[Only present if this ADR overrides a rule in 00_CONSTRAINTS.md.
Quote the exact constraint being waived and explain why it is acceptable here.]
```

---

### `.ai/sessions/YYYY-MM-DD.md`
```markdown
# Session: YYYY-MM-DD

## Goals
- [ ] ...

## Progress
- [What was done and how]

## Dead Ends
- [What was tried, why it failed — this is valuable, do not skip it]

## Decisions Made
- [Any architectural choices → create ADR if significant]

## Carry-over
- [ ] [What moves to the next session]
```
