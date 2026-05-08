# Browser Autonomy Review Plan

## Goal

Make the browser agent behave like a general autonomous web operator:
- open arbitrary sites,
- inspect the live page,
- infer what is required,
- use context and saved secrets first,
- ask the user only for truly missing information or explicit confirmation,
- use screenshot/vision analysis before escalating when the DOM is weak or visually ambiguous.

## Step 1

Rewrite the browser-agent operating instructions:
- strengthen the browser system prompt,
- make `ask_user` a last-resort action in the prompt/tool descriptions,
- clarify that the agent must exhaust DOM, page text, scrolling, waiting, and vision-guided interpretation before asking the user,
- keep `finish_task` reserved for real completion or intentional safe checkpoints.

## Step 2

Add runtime guarding for premature `ask_user`:
- validate that the question is specific,
- reject vague escalation such as "what should I click?",
- if the model asks too early, inject a corrective runtime message and continue the loop instead of blocking the user.

## Step 3

Introduce a generic page-state layer:
- derive structured state from each observation,
- track `screen_kind`, `candidate_actions`, `required_inputs`, `missing_inputs`, `can_proceed_without_user`, `confidence`,
- use this state for both recovery and user-escalation decisions.

## Step 4

Refactor recovery/vision policy around confidence instead of keywords:
- if DOM evidence is weak or conflicting, force a vision pass,
- treat vision as a first-class interpretation layer, not just a post-failure hint,
- only escalate after DOM + vision still cannot produce a safe next step.

## Step 5

Split blocker handling into explicit classes:
- machine-resolvable,
- user-data-needed,
- manual-confirmation-needed,
- policy-forbidden-or-impossible.

This should replace broad heuristic branching for login/captcha/2FA-like surfaces.

## Step 6

Reduce site-specific logic to optional adapters:
- keep aliases, start URLs, and optional provider labels,
- avoid letting site profiles define core runtime policy,
- ensure unknown sites still follow the same autonomous decision loop.

## Step 7

Expand tests around generic autonomy:
- login form with missing password,
- OTP step after login,
- checkout/order flow with missing address,
- ambiguous CTA resolved through DOM/vision,
- logged-in shell with misleading "Sign in" text,
- visual challenge that should be interpreted before user escalation.

## Step 8
Introduce generic success-state detection before adding long-term memory.

### Goal

Allow runtime to mark a browser run as successfully completed even if the model did not explicitly call
`finish_task(status="completed")`, as long as the live page clearly transitioned into a terminal success state.

### Step 8.1 — Success Must Be State-Based

Do not treat success as a keyword match like `applied`, `saved`, or `submitted`.

Instead, evaluate success from page-state transition:
- page state before the action,
- page state after the action,
- whether the primary action surface disappeared,
- whether the task-specific required inputs disappeared,
- whether a result/confirmation/conversation/post-submit surface replaced the previous action surface.

Textual success markers may be used only as weak secondary evidence, never as the primary signal.

### Step 8.2 — Add Outcome Detector

Implement a runtime-owned detector, for example:
- `infer_terminal_outcome(...)`
- or `detect_success_state(...)`

Inputs should include:
- `sub_intent`
- `tool_name`
- `before_snapshot`
- `after_snapshot`
- `before_page_state`
- `after_page_state`
- `before_url`
- `after_url`

Output should be structured:
- `completed: bool`
- `confidence: float`
- `reason_code: str`
- `explanation: str`

### Step 8.3 — Generic Signals To Score

Score success using generic structural signals such as:
- primary CTA disappeared,
- screen kind changed from actionable form/list/login to post-action/result/conversation state,
- required inputs decreased or disappeared,
- missing inputs decreased or disappeared,
- authenticated capabilities increased after login,
- URL or dominant heading changed in a way consistent with task completion,
- previous action is no longer offered in the same way.

Generic text markers may increase confidence only when structural evidence already points toward success.

### Step 8.4 — Runtime Integration

Run the detector:
- after meaningful page-changing actions (`click`, `type_text` with submit, `press_key`, `goto_url` where relevant),
- before declaring `step_limit`,
- before returning a stalled result,
- before asking the user to resume a task that might already be done.

If success confidence is high enough:
- auto-finish the run as completed,
- generate structured facts like `success_detected_by_runtime`,
- avoid asking the user for another run.

If confidence is medium:
- let the model inspect once more,
- or trigger one more `inspect_page`-based verification pass.

If confidence is low:
- do not auto-complete.

### Step 8.5 — Sub-Intent Rules

Completion rules should depend on `sub_intent`:
- `APPLY / ACT`: runtime success detection is appropriate.
- `SELECT`: runtime may detect stable selection completion if a chosen item is clearly opened/selected.
- `DISCOVER`: prefer explicit `finish_task`, because success is semantic (summary delivered), not just UI-state.

### Step 8.6 — Safety Rules

Do not auto-complete when:
- the page still visibly asks for the same core action,
- required user inputs are still missing,
- captcha / manual challenge is still blocking,
- the result is ambiguous and could be a partial save, draft, or transient transition,
- confidence is below threshold.

### Step 8.7 — Tests

Add tests for:
- login success after auth wall disappears,
- apply success after action surface becomes post-apply / conversation-like,
- checkout success after form becomes confirmation-like,
- ambiguous partial transition that must NOT auto-complete,
- step-limit case where runtime should convert failure into completed because the page already shows terminal success.

## Step 9
Introduce successful-run memory as a runtime-owned subsystem, not a model-owned workflow.

### Goal

When the browser agent completes a task successfully, persist a compact normalized recipe so future runs can reuse proven patterns without hardcoding site logic.

### Step 9.1 — Success Memory V1

Persist successful cases only after a confirmed successful browser run:
- write memory from runtime after `finish_task(status="completed")`,
- do not let the LLM decide whether to save memory,
- do not store raw full transcripts as the primary artifact,
- store normalized patterns extracted from the successful run.

### Step 9.2 — Minimal Schema

Create a DB-backed success-memory record with fields like:
- `site_host`
- `site_key`
- `goal_class`
- `sub_intent`
- `screen_kinds_seen`
- `candidate_actions_used`
- `tool_sequence_summary`
- `required_inputs_seen`
- `successful_outcome`
- `trace_summary`
- `created_at`
- `success_score`

Keep the schema biased toward reusable patterns, not full historical logs.

### Step 9.3 — Runtime-Owned Write Path

Implement deterministic write logic in runtime:
- write only on real success,
- deduplicate similar successful recipes,
- skip writes for failed runs, stalled runs, or user-blocked runs,
- avoid storing secrets, OTPs, passwords, payment values, or full user-provided content.

Do not add an LLM tool for writing memory in V1.

### Step 9.4 — Retrieval V1

Add a runtime retrieval function for successful patterns:
- same `site_host` first,
- then same `goal_class`,
- then same `screen_kind` / `screen_kinds_seen`,
- then same `sub_intent`,
- return only the top 1-3 most relevant patterns.

This retrieval should be deterministic and cheap.

### Step 9.5 — Prompt Injection Policy

Inject retrieved memory into the browser prompt only when relevance is high:
- similar site,
- similar task class,
- similar current page state.

Inject compact hints, not full transcripts:
- “Similar successful runs on this site usually did X before Y.”
- “For `screen_kind=login`, successful runs often used these actions first: …”

Memory should act as a hint layer, never as ground truth over the live page.

### Step 9.6 — LLM Use Policy

Do not require an extra model call in V1 for retrieval or ranking.

If needed later:
- use a model only to compress successful traces into normalized summaries before storing,
- or to rerank borderline candidate memories after deterministic filtering.

This should be treated as V2, not a prerequisite for V1.

### Step 9.7 — Vector Search Decision

Do not require a vector database in V1.

Start with structured retrieval over SQL / JSON fields:
- host,
- goal class,
- sub-intent,
- screen kind,
- action summary.

Consider embeddings / vector search only if:
- the memory corpus grows large,
- deterministic filtering becomes too weak,
- useful matches are often cross-site but behaviorally similar.

### Step 9.8 — Safety Rules

Success memory must never store:
- passwords,
- OTP codes,
- card data,
- full addresses unless explicitly normalized and redacted,
- raw screenshots,
- raw confidential page text beyond what is needed for reusable pattern summaries.

### Step 9.9 — Tests

Add tests for:
- successful run writes one normalized record,
- failed or blocked runs do not write memory,
- retrieval prefers same-site and same-task patterns,
- prompt injection stays short and only happens when relevance threshold is met,
- secrets are redacted before persistence.
