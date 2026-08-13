# L3 Procedural Memory Completion Plan

## Goal

Complete the L3 boundary before MCP, Meta Loop, or L4/L5 expansion. A skill must be
grounded in repeatable L2 evidence, have a reviewable persisted procedure definition,
be selected within an explicit budget, and follow an observable lifecycle.

## Scope and acceptance criteria

1. **Repeatability gate**
   - Seed only active L2 knowledge with sufficient support evidence and at least one
     tool-specific, reviewable step draft.
   - Do not create a skill from general natural-language knowledge alone.

2. **Procedure artifacts and registry**
   - Persist each skill's reviewed metadata and executable steps in a versioned YAML
     artifact under the configured skills directory.
   - Publish a JSON registry view containing non-archived skill summaries.
   - Keep SQLite as the execution/history store; artifacts must be regenerated on each
     skill mutation so the two views do not silently diverge.

3. **Selection and execution budget**
   - Rank active skills deterministically by query relevance, success rate, and
     recency, then return at most the configured context limit.
   - Enforce a maximum number of executable L3 steps per run before invoking tools.

4. **Lifecycle and archive policy**
   - Evaluate Active/Degrading recovery from execution samples.
   - Archive Degrading skills only when both the configured low-performance and
     inactivity thresholds are met; retain artifacts and history.
   - Continue supporting explicit archive as an operator action.

## Test shape

- Unit tests for repeatability rejection and promotion with a reviewed draft.
- Repository tests for YAML artifact and registry synchronization.
- Memory-context tests for deterministic ranking.
- Execution tests proving the budget blocks tool invocation.
- Lifecycle tests for inactivity-gated auto archive and history retention.

## Out of scope

- MCP adapters, HITL policy execution, L4 K-Scenario evaluation, and L5 updates.
- Generating or guessing tool arguments: reviewer-provided arguments remain required.
