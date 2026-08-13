# L3 Procedural Memory Completion Plan

## Goal

Complete the L3 boundary before MCP, Meta Loop, or L4/L5 expansion. A skill must be
grounded in repeatable L2 evidence, have a reviewable SQLite-persisted procedure definition,
be selected within an explicit budget, and follow an observable lifecycle.

## Scope and acceptance criteria

1. **Repeatability gate**
   - Seed only active L2 knowledge with sufficient support evidence and at least one
     tool-specific, reviewable step draft.
   - Do not create a skill from general natural-language knowledge alone.

2. **SQLite procedure records and review projections**
   - Persist each skill's reviewed metadata and executable steps, including its version,
     in SQLite as the sole L3 source of truth.
   - Publish a versioned YAML projection from SQLite for human/Git review. YAML is not
     read for execution, lifecycle, or state reconstruction.
   - Publish a JSON registry view containing non-archived skill summaries.
   - Regenerate the registry from SQLite on every skill mutation; registry data is never
     read as an execution or lifecycle source.

3. **Selection and execution budget**
   - Rank active skills deterministically by query relevance, success rate, and
     recency, then return at most the configured context limit.
   - Enforce a maximum number of executable L3 steps per run before invoking tools.

4. **Lifecycle and archive policy**
   - Evaluate Active/Degrading recovery from execution samples.
   - Preserve Degrading skills and their artifacts/history until an operator explicitly
     archives them; no performance or inactivity policy may archive or delete a skill.

5. **L4/L5-directed growth**
   - Create new L3 Seeds only when L4 permits the L2 direction, L5 reports sufficient
     category capability, and the per-run growth budget has capacity.
   - Apply the M16 rate regulator only to new Seeds: freeze after a success-rate crash or
     multiple operational-load breaches; throttle on one load breach, persisted consolidation
     stagnation, or rapid-growth signals.
   - Preserve L1/L2 evidence when growth is deferred; only the new L3 Seed is blocked.

## Test shape

- Unit tests for repeatability rejection and promotion with a reviewed draft.
- Repository tests for SQLite versioning and registry synchronization.
- Memory-context tests for deterministic ranking.
- Execution tests proving the budget blocks tool invocation.
- Lifecycle tests for recent-window performance metrics, explicit archive, and history retention.

## Out of scope

- MCP adapters, HITL policy execution, L4 K-Scenario evaluation, and L5 updates.
- Generating or guessing tool arguments: reviewer-provided arguments remain required.
