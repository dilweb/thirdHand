# Browser Step Verification Roadmap

## Goal

Make browser automation steps verifiable in a generic, site-agnostic way:
- plan a step with an expected effect,
- execute the tool action,
- verify what changed,
- decide whether the step succeeded, likely succeeded, had no effect, got blocked, or failed technically.

This roadmap is intentionally generic and should work across hh.ru, email providers, admin panels, ecommerce, and other authenticated web flows.

## Step 1 — Universal Step Model

Introduce a domain model for browser steps instead of treating a tool call as the step itself.

Core entities:
- `TargetSnapshot`
- `StepExpectation`
- `VerificationEvidence`
- `StepOutcome`

The model must capture:
- user objective,
- action kind,
- action intent,
- target semantics,
- expected outcomes,
- final verification outcome.

## Step 2 — Explicit Step Pipeline

Make each meaningful step follow:

1. `plan`
2. `act`
3. `verify`
4. `decide`

`plan`:
- choose target,
- classify action intent,
- define expected outcomes.

`act`:
- execute the runtime tool.

`verify`:
- collect after-state,
- compare before/after evidence.

`decide`:
- map evidence into a structured outcome.

## Step 3 — Action Intents and Expected Effects

Use generic action intents instead of site-specific success logic:
- `submit`
- `toggle`
- `open_details`
- `navigate`
- `select`
- `dismiss`
- `delete`
- `reply`
- `download`
- `upload`
- `unknown`

Each intent should define generic expected effects such as:
- target changed,
- CTA disappeared,
- confirmation appeared,
- modal opened,
- new detail surface opened,
- row disappeared,
- selected state changed.

## Step 4 — Post-Action Verification Layer

Add a dedicated verification layer that inspects evidence after actions.

Evidence sources:
- target-level semantic diff,
- local container diff,
- page-level transition diff,
- visual before/after diff,
- runtime/tool signals.

The verifier should produce structured evidence even when it cannot yet decide final success with high confidence.

## Step 5 — Outcome Aggregator

Map verification evidence into:
- `success`
- `probable_success`
- `no_effect`
- `ambiguous`
- `blocked`
- `tool_failure`

Do not equate tool exceptions with user-step failure.
Example:
- stale click after DOM transition may be `probable_success`, not `tool_failure`.

## Step 6 — State Machine Integration

Integrate verification into the existing browser flow without rewriting the whole subsystem.

Minimal integration points:
- capture before-state before meaningful actions,
- run tool,
- rebuild after-state,
- evaluate step outcome,
- log/store evidence,
- use outcome later for runtime completion and recovery.

Recommended module split:
- `browser_flow.py`: orchestration
- `browser_runtime.py`: low-level execution and raw signals
- `browser_observation.py`: snapshots, crops, local extraction
- `browser_step_verification.py`: verification and aggregation logic

## Step 7 — Screenshots Before and After

Add optional screenshot capture as verification evidence, not as the primary source of truth.

Capture levels:
- full viewport,
- target crop,
- local container crop.

Use them:
- when DOM is weak,
- when runtime and semantic evidence conflict,
- when a target disappears unexpectedly,
- when a blocker/modal/challenge may have appeared.

Prefer in-memory base64 over disk persistence by default.

## Step 8 — Graceful Degradation

The system should remain usable when:
- vision provider returns `429`,
- vision is unavailable,
- snapshots are thin,
- locators go stale,
- actions partially succeed.

Priority order:
1. semantic DOM diff
2. runtime signals
3. visual verification

If visual verification is unavailable, the step should still yield a structured outcome, not collapse to an opaque failure.

## Step 9 — Incremental Rollout

Roll out in small safe increments:

1. add data structures and logging only
2. add target/container semantic diff
3. expose structured step outcomes in traces
4. connect step outcomes to runtime success detector
5. add targeted before/after screenshots
6. use evidence to improve recovery and blocked-state decisions

Each increment should be testable and independently deployable.

## Step 10 — Test Strategy

Add tests for:
- successful click with target state change
- stale-after-success
- no-effect click
- modal open after action
- redirect/navigation after action
- blocked state after action
- weak DOM + no vision
- conflicting semantic vs visual evidence

Tests should focus on structured evidence and step outcomes, not only final browser run status.
