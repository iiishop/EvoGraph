# Scoped architecture / class-detail verification

## Workflow

Global navigation contains module architecture, with separate current-source and versioned target views. Class detail is requested only after selecting 1–3 components. Optional file selection further narrows the components’ explicit source mappings. Back and Close retain the overview; changing project, architecture version, source baseline or scope invalidates pending results.

No global class-model generation or rendering entry remains. Legacy diagrams and architecture histories are preserved. Stored local proposed designs are accessible only from their exact component set and architecture revision, with explicit DESIGN labeling.

## Automated checks

- Python tests cover exact scoped reads, no imported-file traversal, source hashes before/after rendering, incomplete repository scans, supported syntax adapters, legacy compatibility, source/design provenance, architecture versions and resource budgets
- Real Java/PlantUML integration tests render Python, C++, TypeScript and Vue source
- Frontend tests cover bounded selection, file subsets, stale requests, repeated navigation, saved designs, compact conversation, initial fit while Agent-follow is disabled, resize, and preservation of user camera state
- Run `.venv/bin/pytest -q`, `npm test`, `npm run build`, `npm run format:check`, `.venv/bin/ruff check backend tests`, and `uv lock --check --offline`

## Native GUI checks

Verified using the actual Qt/pywebview application, not a browser-only replacement:

- Single-module and two-module drill-down with collapsed outside dependencies
- Expanded image, Close, Back, and retained selected scope
- No-selection disabled action; oversized single-component feedback before rendering
- Source-missing design component: no fabricated source classes; separate saved DESIGN view
- Classless module: explicit empty result, without pretending functions are classes
- Architecture version list and switching; initial architecture fit and usable canvas controls

Synthetic examples contain no credentials and make no model calls. Separate read instrumentation on EvoGraph’s actual `application/uml.py` and `useScopedClassDetail.ts` confirmed that each selected file was read only for initial extraction and post-render freshness validation; no dependency implementation files were read.

## Honest limits

Static structure is not runtime or acceptance evidence. C++ preprocessor expansion, Unreal reflection/API macros, cross-file semantic resolution, dynamic members and inferred calls are not supported. Unsupported syntax fails explicitly. Vue Composition API declarations need not produce any classes. File/size/entity/member/render-text budgets intentionally reject large local scopes instead of drawing an unreadable global model.

A repository containing large binary resources may have an incomplete whole-project baseline. Local class detail can still verify selected indexed file hashes, with a visible caveat. Acceptance and evidence still require a complete baseline.
