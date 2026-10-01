<h1 align="center">EvoGraph</h1>

<p align="center">
  <strong>Evidence-gated project evolution.</strong>
</p>

<p align="center">
  Plan a project as a milestone graph. Every milestone carries behaviors you can verify,<br/>
  and a verification only counts while the repository still has the fingerprint it was measured on.
</p>

<p align="center">
  <a href="./README.md">English</a> | <a href="./README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <img alt="Status: Alpha" src="https://img.shields.io/badge/status-alpha-f4b942" />
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" />
  <img alt="Vue 3" src="https://img.shields.io/badge/Vue-3-42b883?logo=vuedotjs&logoColor=white" />
  <img alt="Runtime: local first" src="https://img.shields.io/badge/runtime-local--first-1756d1" />
  <img alt="Acceptance: evidence gated" src="https://img.shields.io/badge/acceptance-evidence--gated-0f7d65" />
  <img alt="GitHub stars" src="https://img.shields.io/github/stars/iiishop/EvoGraph?style=flat-square" />
</p>

---

## Why this exists

Producing a plausible implementation got cheap. Knowing whether it actually holds did not.

A model can write "done" in one sentence, and nothing inside that sentence tells you whether it is true. Reading the diff more carefully does not scale either: the more of a project an agent touches, the more of it you have already stopped reading. What is left is a plan you believe in and a codebase you are guessing about.

EvoGraph moves the decision out of the conversation. The plan is a graph of milestones. Each milestone owns behaviors that can be verified. The acceptance contract is a target version listing the behaviors that must hold. And the only thing that can mark a behavior complete is a verification command you ran yourself, against a repository snapshot you accepted.

Change the code afterwards and that evidence goes stale. The artifact that passed is no longer the artifact that exists, so the milestone drops back into revalidation instead of staying green.

## Core concepts

| Concept | Meaning |
|---|---|
| Target version | The acceptance contract: the target statement plus the behaviors that must hold |
| Milestone | One independently verifiable unit of evolution: scope, resources, change types, prerequisite edges |
| Behavior revision | A stable behavior key with a versioned statement and acceptance scope. Editing either creates a new revision; the old one stays in history |
| Baseline | A snapshot of the repository: file count, git commit, and a sha256 fingerprint over relative paths and file contents |
| Evidence | The result of a local command, pinned to a milestone, the milestone's behavior revisions, and one baseline |
| Current evidence | Evidence whose baseline id and fingerprint both match the latest baseline and whose result is PASS. Everything else is stale |

## See what changed in a planning turn

The composer shows a compact, expandable turn summary derived from the saved project before and after the turn. It lists added, updated and removed milestones, prerequisite changes, final-target membership changes and architecture changes. It records the net result after target finalization, rather than repeating the model's explanation or counting transient edits. The same summary is retained in activity history and survives reopening the project.

Completed, waiting-for-answer, stopped and failed turns are distinguished. Stopping or failing does not roll back tool edits already saved: partial changes remain visible in the summary. The UI waits for the admitted turn's terminal result before refreshing after an interruption. Legacy history remains readable without fabricated summaries. This is a change report, not proof that code was implemented or acceptance passed.

## Final goals and step acceptance

A roadmap can contain temporary states. For example, a first PR may write inventory to CSV, while a later PR migrates writes to PostgreSQL and retires CSV persistence. Those two checks should not both become final requirements.

Each proposed behavior supports `acceptance_scope`:

- `target` (the default): a lasting requirement included in the final target contract
- `milestone`: a local or transitional check, mandatory for its milestone but excluded from the final target contract

Both incremental tools and full-plan proposals support this field. Set it explicitly when planning a transition; graph order does not determine final membership. The inspector labels each check, and the goal strip counts the exact behavior IDs in the latest committed target. A roadmap containing only milestone-scoped checks has no final acceptance contract yet and is never shown as achieved.

Existing projects and proposals without the field retain `target` semantics. A scope change creates a new behavior revision; previous targets and evidence are retained. An incremental update that omits the scope on an unchanged active behavior preserves that behavior's scope.

This distinction is about final-goal membership. It does not waive step acceptance, make old evidence current, or discharge historical prerequisites: milestone acceptance still checks every behavior, prerequisites still require current-baseline evidence, and evidence results remain aggregated per milestone. It does not automatically detect all contradictory requirements or make a completed final goal equivalent to every roadmap step passing.

## What the graph enforces

- Prerequisite edges only. Every edge carries a written reason and a type: implementation, migration or verification.
- Structural checks run before anything is saved. Duplicate milestone ids, one behavior key claimed by two milestones, unknown change types, a dependency without a reason, and dependency cycles are all rejected.
- Obligations generated from change types. general, api, data and auth each add their own checklist: an api change asks who consumes the API and whether the contract stays compatible, a data change asks about migration and rollback, an auth change asks about credential storage and failure paths.
- A tick needs a written basis. Resolving an obligation without a note is refused. The checkbox records an investigation, it does not replace one.
- One lease per resource. Two active milestones that declare the same resource cannot run at once, and a milestone with no declared resources conflicts with every active lease.
- Revalidation over silent drift. Refresh the baseline and every IN_PROGRESS or VERIFIED_COMPLETE milestone drops to REVALIDATION_REQUIRED.

## What the agent can and cannot do

| Tool | Effect |
|---|---|
| create_milestone, update_milestone, remove_milestone | Edit the graph one node at a time, keeping ids and behavior keys stable |
| add_dependency, remove_dependency | Add or drop a prerequisite edge, reason required |
| set_target | Change the target statement |
| read_project | Read the current target, milestones, behavior revisions and baselines |
| inspect_repository | Bounded file inventory plus README and manifest excerpts |
| read_repository_file | Read one source or test file: at most six per turn, secrets and symlinks refused |
| ask_user | Stop and ask when the intent is too thin for a reliable edit |

Each turn gets 12 model rounds, 24 tool calls and 6 source files, then stops. Two rounds without progress end the turn as well.

There is no shell tool and no file-write tool. The agent edits planning data and reads source code. It cannot run your tests, and it cannot tell you it did. Repository content and quoted text are passed in as untrusted data, never as instructions.


The 24-milestone proposal limit is a per-generation safety budget, not a lifetime project limit. Incremental edits support up to 256 active delivery milestones, with at most 24 direct prerequisites per milestone. This explicit working-set ceiling bounds full-graph validation, transitive ancestor sets and unvirtualized canvas layout; it is not a claim of unlimited scale. At capacity, refine or remove existing nodes, or split the roadmap into separate projects before adding more.

## Explore architecture locally

Architecture work follows the scope you request. Ask for a PR roadmap without an architecture diagram, or defer architecture until later, and the agent is instructed to continue roadmap-only without making architecture a prerequisite. Existing architecture, diagrams and component mappings are preserved; skipping architecture is not a deletion request. When architecture is requested or relevant to an unrestricted design change, it remains part of planning. These are model instructions, not a keyword filter or a guarantee of model compliance.

If no existing component honestly covers a new milestone, its mapping can stay empty as pending association. Review flags this as advisory; planning does not require an invented component or an unrelated mapping. Empty or omitted mappings preserve an existing milestone's nonempty association in both incremental edits and full-plan replacement. Unknown component IDs remain invalid, and architecture consistency obligations and acceptance evidence rules still apply.

The architecture view is the global module overview. Select 1–3 components, optionally narrow their linked files, then open local class structure in the same workspace. Extraction reads only the selected source paths; imports and outside architecture neighbors stay collapsed boundary nodes. There is no project-wide class diagram generation.

Python AST and tree-sitter adapters for TypeScript/Vue and C++ extract declarations and signatures without executing code. Parsing is bounded (12 files, 24 classes, 160 members); oversized scopes require narrowing. Static declarations do not establish runtime behavior, and missing source mappings never become invented classes. Source-backed structure and proposed design stay explicitly labeled SRC and DESIGN. Existing architecture revisions, evidence, migrations and archived legacy diagrams are preserved.

## Architecture

```text
┌──────────────────────────────────────────────────────────────────────────┐
│  Workspace: Vue 3 + Vue Flow, built to dist/ and served locally          │
└──────────────┬───────────────────────────────────────────────────────────┘
               │  desktop: pywebview js_api bridge
               │  browser: POST /api/command, /api/agent/stream (NDJSON)
┌──────────────▼───────────────────────────────────────────────────────────┐
│  Application: one command surface shared by both transports              │
│  projects · settings · planning · graph · execution · agent              │
└────┬───────────────┬─────────────────┬───────────────────┬───────────────┘
     │               │                 │                   │
     ▼               ▼                 ▼                   ▼
┌──────────┐  ┌──────────────┐  ┌─────────────┐  ┌───────────────────┐
│ SQLite   │  │ Baseline     │  │ Verifier    │  │ Provider          │
│ projects │  │ scanner      │  │ runs the    │  │ OpenAI-compatible │
│ events   │  │ sha256 over  │  │ command you │  │ Anthropic         │
│ evidence │  │ repo files   │  │ approve     │  │ Messages          │
└──────────┘  └──────────────┘  └─────────────┘  └───────────────────┘
```

## How a milestone becomes verified

1. Create a project and point it at a local repository path.
2. Discuss the target with the planning assistant. A proposal comes back as structured JSON: milestones with scopes, behavior statements, resources and typed dependencies. Structure is checked mechanically; semantic sufficiency is still on you.
3. Apply the proposal. Milestones land in the graph and a target version records the behaviors that must hold.
4. Claim a milestone. Readiness is checked first: prerequisite evidence on the current baseline, resolved obligations, a complete baseline, and no resource conflict. A blocked attempt is recorded rather than allowed through.
5. Run your own command. A PASS is stored as evidence pinned to the baseline fingerprint and to the milestone's behavior revisions, and the milestone becomes VERIFIED_COMPLETE.

Edit the repository between step 4 and step 5, or during step 5, and the result is not accepted.

## Quick Start

Requirements:

- Python 3.11 or newer
- Node.js and npm, to build the frontend
- uv is recommended; the repository ships a `uv.lock`

```bash
git clone https://github.com/iiishop/EvoGraph.git
cd EvoGraph
npm install
uv run python run.py
```

Start-up checks the frontend itself: if `dist/` is missing, or anything under `frontend/` is newer than the built output, it runs `npm run build` (about 15 seconds) before launching, so a frontend edit needs no manual rebuild. A failed build does not block start-up: npm's output is printed and the existing `dist/` is used; only a missing `dist/` is fatal.

Both launch modes:

```bash
uv run python run.py            # desktop window, opened with pywebview
uv run python run.py --browser  # local HTTP server on http://127.0.0.1:8765
```

Linux: use the explicit Qt setup, `uv sync --extra linux` then `uv run --extra linux python run.py --gui qt`. See [Linux desktop setup](docs/linux.md) for graphical-session, native-library, writable-state and credential-store requirements.


On first launch EvoGraph creates a demo project (认证工作台): a seven-milestone registration and login plan that is deliberately unexecuted, so acceptance starts at 0/7. It is there to be looked at and taken apart, not to be trusted.

Other flags: `--port` (default `8765`), `--data-dir` (default `~/.evograph`, or `EVOGRAPH_DATA_DIR`), `--build` (force a frontend rebuild first) and `--no-build` (skip the frontend check). Project state, events and evidence live in `evograph.sqlite3` inside that directory.

Desktop notes: pywebview needs a WebView backend, and its installation guide asks Linux users to choose one explicitly, for example `pip install "pywebview[gtk]"`. Windows uses WebView2 and macOS uses the system WebKit. Browser mode is the same application over local HTTP, so it runs without any of that.

### Local API

Both transports drive the same command surface, so anything the window can do is available to a script:

```bash
curl -X POST http://127.0.0.1:8765/api/command \
  -H 'content-type: application/json' \
  -d '{"action":"projects.list","params":{}}'
```

```json
{"ok":true,"data":[{"id":"a2cfffd56b104e2c","name":"认证工作台","is_demo":true,"milestone_count":7,"acceptance":{"passed":0,"total":7,"achieved":false}}]}
```

The HTTP transport binds to loopback, accepts only `127.0.0.1` and `localhost` hosts, checks the origin against an allowlist, and caps request bodies at 1 MB.

## Configuring a model provider

Open 设置 in the sidebar, then 模型服务.

| Adapter | Fields | Secret |
|---|---|---|
| OpenAI Compatible | base_url, model | API Key |
| Anthropic Messages | base_url (default `https://api.anthropic.com`), model | API Key |

Keys are written to the OS credential store through keyring, under the service name `EvoGraph`, scoped by adapter and base URL so that changing an endpoint never reuses an old key. They are never stored in SQLite and never logged.

Planning, proposal generation and the agent loop all need a provider that supports streaming tool calls. Both built-in adapters do. Project state works without any provider at all, so the demo project is usable before you configure one.

## How verification works

- The command is an argument array, executed with the repository root as the working directory and `shell=False`. At most 64 elements, no empty program name.
- Exit code 0 is PASS, a non-zero exit code is FAIL, and an OS error or a timeout is ERROR. The default timeout is 120 seconds, and on timeout the whole process group is killed.
- The last 30000 characters of combined output are kept.
- The repository is hashed before and after the run. If the fingerprint or the git commit changed during verification, the result is downgraded to ERROR: it no longer describes one fixed state.
- Results are only accepted on a complete scan. A snapshot caps at 10000 files, 10 MB per file and 200 MB in total; past any of those the baseline is incomplete and verification is refused.

## Repository layout

```text
backend/evograph/
  domain/            models and deterministic policies: obligations, readiness, acceptance
  application/       use cases: projects, planning, graph editor, execution, agent runtime
  agent_tools/       the tools the model is allowed to call
  infrastructure/    SQLite store, baseline scanner, local verifier
  providers/         OpenAI-compatible and Anthropic adapters
  transport/         pywebview bridge and FastAPI app
frontend/src/        Vue 3 workspace: sidebar, milestone graph, inspector, evidence panel, agent dock
tests/               pytest suite
run.py               run from source without an install
```

## Tests

```bash
uv run --extra test pytest
```

The suite covers the command surface, plan and proposal validation, provider streaming protocols, the agent streaming loop, and project and runtime policy.

## Current status

Version 0.1.0, alpha. The graph, baseline, evidence and readiness model is implemented and covered by tests; the desktop window and the browser transport both run from source. There is no packaged release, no installer and no CI pipeline yet, and the interface is in Chinese.

## Star History

<a href="https://www.star-history.com/?type=date&repos=iiishop%2FEvoGraph">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=iiishop/EvoGraph&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=iiishop/EvoGraph&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=iiishop/EvoGraph&type=date&legend=top-left" />
 </picture>
</a>

