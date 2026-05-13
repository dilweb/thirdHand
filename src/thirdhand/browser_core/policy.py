"""Local workflow policy — finite-state machine for the browser agent.

Replaces the ``build_adaptive_system_prompt()`` function that was
previously in ``prompts.py``.  The policy owns the current workflow
state and generates the appropriate prompt block based on context
(page type, progress, cycles).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.thirdhand.browser_core.page_classifier import PageType


class WorkflowState(str, Enum):
    """States in the browser agent's workflow finite-state machine."""

    START = "start"
    DISCOVER = "discover"
    ALTERNATE_SEARCH = "alternate_search"
    SELECT = "select"
    APPLY = "apply"
    MONITOR = "monitor"
    AWAIT_USER = "await_user"
    COMPLETE = "complete"


# Natural progression order — used to suggest the next state.
_STATE_CHAIN: list[WorkflowState] = [
    WorkflowState.START,
    WorkflowState.DISCOVER,
    WorkflowState.SELECT,
    WorkflowState.ALTERNATE_SEARCH,
    WorkflowState.APPLY,
    WorkflowState.MONITOR,
    WorkflowState.AWAIT_USER,
    WorkflowState.COMPLETE,
]

# Map PageType → suggested WorkflowState.
_PAGE_TYPE_TO_STATE: dict[PageType, WorkflowState] = {
    PageType.SEARCH_RESULTS: WorkflowState.DISCOVER,
    PageType.DETAIL_PAGE: WorkflowState.APPLY,
    PageType.FORM_PAGE: WorkflowState.APPLY,
    PageType.LOGIN_PAGE: WorkflowState.APPLY,
    PageType.GENERIC_PAGE: WorkflowState.DISCOVER,
}


# ---------------------------------------------------------------------------
# Prompt blocks for each state
# ---------------------------------------------------------------------------

_STATE_PROMPTS: dict[WorkflowState, str] = {
    WorkflowState.DISCOVER: (
        "\n---\n"
        "📋 STATE: DISCOVERY\n"
        "You are on a listing / search results page.\n"
        "1. Use extract_page_items to get structured item data.\n"
        "2. Click on item titles or links to open details.\n"
        "3. Do NOT click on filters, tabs, checkboxes, or sorting controls.\n"
        "4. If you don't see items, scroll down to reveal more.\n"
        "5. After opening an item, look for the primary action button."
    ),
    WorkflowState.ALTERNATE_SEARCH: (
        "\n---\n"
        "📋 STATE: ALTERNATE SEARCH\n"
        "No matching results found on this page.\n"
        "1. Try a different search query or filter combination.\n"
        "2. Look for alternative navigation paths.\n"
        "3. If truly no results, call ask_user for guidance."
    ),
    WorkflowState.SELECT: (
        "\n---\n"
        "📋 STATE: SELECTION\n"
        "You are choosing from visible options.\n"
        "1. Compare options carefully before navigating away.\n"
        "2. Finish with the selected option(s).\n"
        "3. Do NOT submit forms or apply yet."
    ),
    WorkflowState.APPLY: (
        "\n---\n"
        "📋 STATE: APPLY / ACT\n"
        "You are executing the primary action on a detail or form page.\n"
        "1. Look for the primary action button (apply, buy, submit, etc.).\n"
        "2. If a form is required, fill it using the user's provided data.\n"
        "3. Use type_text with element_id from inspect_page.\n"
        "4. After filling, look for a submit/save button.\n"
        "5. If blocked by credentials/OTP/captcha, call ask_user."
    ),
    WorkflowState.AWAIT_USER: (
        "\n---\n"
        "📋 STATE: AWAITING USER INPUT\n"
        "The workflow is paused waiting for user input.\n"
        "1. Do NOT retry actions that require user data.\n"
        "2. When the user responds, apply their input immediately.\n"
        "3. Verify the result after applying user input."
    ),
    WorkflowState.MONITOR: (
        "\n---\n"
        "📋 STATE: MONITOR\n"
        "You are watching for changes on the page.\n"
        "1. Call inspect_page periodically to check for updates.\n"
        "2. Report when the expected change occurs.\n"
        "3. Do NOT interact with the page — only observe.\n"
        "4. If the expected change is detected, call finish_task."
    ),
    WorkflowState.COMPLETE: (
        "\n---\n"
        "📋 STATE: COMPLETE\n"
        "The task objective has been met.\n"
        "1. Call finish_task with the final summary."
    ),
}


# ---------------------------------------------------------------------------
# Cycle / stuck prompt blocks
# ---------------------------------------------------------------------------

_CYCLE_WARNING = (
    "\n---\n"
    "⚠️ CYCLE DETECTED: You are repeating the same actions without progress.\n"
    "STOP. Take a completely different approach:\n"
    "- Use use_visual_assist to see what's on the page\n"
    "- Try scrolling to find new content\n"
    "- Look for elements you haven't tried yet\n"
    "- If truly stuck, call ask_user for guidance"
)

_STUCK_TIP = (
    "\n---\n"
    "💡 TIP: If you're on a list/search results page, click on an item title/link "
    "to open it. Don't click on filters, tabs, or sorting controls."
)


# ---------------------------------------------------------------------------
# ALTERNATIVE_POLICY prompt blocks — injected when RecoveryLayer detects
# a specific page situation (login, captcha, modal, etc.).
# Each block teaches the LLM how to analyse the page, form a batch of
# actions, and verify the result.  No hardcoded selectors — the LLM
# finds elements by their HTML attributes.
# ---------------------------------------------------------------------------

_ALTERNATIVE_POLICY_PROMPTS: dict[str, str] = {
    "login": (
        "\n---\n"
        "📋 SITUATION: LOGIN FORM DETECTED\n"
        "A login/authentication form is visible on the page.\n"
        "\n"
        "1. Analyse the page via inspect_page:\n"
        "   - Find the username/email field (fillable, autocomplete='username' or 'email')\n"
        "   - Find the password field (fillable, type='password')\n"
        "   - Find the submit button (actionable, type='submit' or role='button' "
        "inside a <form> element)\n"
        "\n"
        "2. Form a batch of actions:\n"
        "   - type_text(username) + type_text(password) + click(submit) — ONE batch\n"
        "   - All three actions are on the same page, safe to batch\n"
        "\n"
        "3. After the batch, verify:\n"
        "   - Did the page navigate to a new URL? → login successful, continue plan\n"
        "   - Is there an error message (aria-live='assertive' or role='alert')? → report to user\n"
        "   - Did the page not change at all? → use_visual_assist to understand why\n"
        "\n"
        "4. If key elements are not found:\n"
        "   - Use use_visual_assist for visual analysis of non-standard forms\n"
        "   - If 2FA/OTP is requested → ask_user for the code\n"
        "   - If no credentials available → ask_user"
    ),
    "captcha": (
        "\n---\n"
        "📋 SITUATION: CAPTCHA DETECTED\n"
        "A captcha or human verification challenge is visible.\n"
        "\n"
        "1. Call use_visual_assist ONCE to read the captcha text from the screenshot\n"
        "\n"
        "2. Form a batch:\n"
        "   - type_text(captcha_text, label/placeholder from vision) + click(submit button)\n"
        "\n"
        "3. After the batch, verify:\n"
        "   - Did the captcha disappear? → success, continue\n"
        "   - Is there a new captcha? → call use_visual_assist again (max 2 times)\n"
        "   - Still stuck after 2 attempts? → ask_user for manual solving\n"
        "\n"
        "4. Do NOT call use_visual_assist twice without taking a real action in between"
    ),
    "modal": (
        "\n---\n"
        "📋 SITUATION: MODAL/DIALOG OPEN\n"
        "A modal dialog is blocking the main page.\n"
        "\n"
        "1. Analyse via inspect_page — look for elements with 'modal': true\n"
        "   - Elements INSIDE the dialog have modal:true\n"
        "   - Elements on the background page do NOT have modal:true\n"
        "\n"
        "2. Form a batch:\n"
        "   - If the modal has fillable fields → type_text + click(submit inside modal)\n"
        "   - If the modal is informational → click(close/confirm button inside modal)\n"
        "   - PREFER elements with modal:true — background clicks will fail\n"
        "\n"
        "3. After the batch, verify:\n"
        "   - Did the modal close? → success\n"
        "   - Is the modal still open? → check for validation errors inside\n"
        "\n"
        "4. If the modal asks for data you don't have (cover letter, password):\n"
        "   - Do NOT close it — call ask_user for the required information"
    ),
    "pagination": (
        "\n---\n"
        "📋 SITUATION: PAGINATION / NEXT PAGE\n"
        "There are more results available on additional pages.\n"
        "\n"
        "1. Analyse via inspect_page:\n"
        "   - Look for links/buttons with rel='next' or aria-label containing navigation hints\n"
        "   - Look for a group of numbered links with role='navigation' or aria-label='pagination'\n"
        "   - Look for 'load more' / 'show more' buttons at the bottom of the list\n"
        "\n"
        "2. Form a batch:\n"
        "   - click(next/page button) — single action (changes the page)\n"
        "   - Do NOT batch with other actions — pagination navigates\n"
        "\n"
        "3. After the action, verify:\n"
        "   - Did new content appear? → continue collecting\n"
        "   - Same content as before? → you've reached the end\n"
        "   - Error/page not found? → stop pagination"
    ),
    "empty_results": (
        "\n---\n"
        "📋 SITUATION: EMPTY RESULTS / NO MATCHES\n"
        "The search or listing returned no results.\n"
        "\n"
        "1. Analyse via inspect_page:\n"
        "   - Look for a search/filter input field (fillable with role='searchbox' or type='search')\n"
        "   - Look for filter controls (dropdowns, checkboxes, chips)\n"
        "   - Look for empty-state indicators: role='status', aria-live='polite', "
        "or a heading suggesting absence of results\n"
        "\n"
        "2. Form a batch:\n"
        "   - type_text(new/alternative query) + click(search/submit button)\n"
        "   - Or: click on a filter chip to remove it, then retry\n"
        "\n"
        "3. After the batch, verify:\n"
        "   - Did results appear? → continue with discovery\n"
        "   - Still empty? → try a different approach or call ask_user\n"
        "\n"
        "4. If no search/filter controls found:\n"
        "   - Use use_visual_assist to understand the page layout"
    ),
    "form_error": (
        "\n---\n"
        "📋 SITUATION: FORM VALIDATION ERRORS\n"
        "The form was submitted but returned validation errors.\n"
        "\n"
        "1. Analyse via inspect_page:\n"
        "   - Look for fields with aria-invalid='true' or error messages nearby\n"
        "   - Look for fields with red border/error class styling\n"
        "   - Check the dialogs list for error messages\n"
        "\n"
        "2. Form a batch:\n"
        "   - type_text(corrected value) for each invalid field\n"
        "   - click(submit button) — all in one batch\n"
        "\n"
        "3. After the batch, verify:\n"
        "   - Did the errors disappear? → success\n"
        "   - Same errors still showing? → you need different data, ask_user\n"
        "   - New errors appeared? → fix those too\n"
        "\n"
        "4. If you don't have the correct data for a field:\n"
        "   - Call ask_user with the specific field name and error message"
    ),
    "rate_limit": (
        "\n---\n"
        "📋 SITUATION: RATE LIMITING / BLOCKED\n"
        "The site is blocking requests or showing a rate-limit message.\n"
        "\n"
        "1. Analyse via inspect_page:\n"
        "   - Look for HTTP 429 status or elements with aria-live='assertive'\n"
        "   - Look for a timer or countdown element suggesting retry-after\n"
        "\n"
        "2. Action:\n"
        "   - Call wait(seconds) with a reasonable delay (10-30s)\n"
        "   - Then inspect_page again to check if the block is gone\n"
        "\n"
        "3. After waiting, verify:\n"
        "   - Is the block gone? → continue the plan\n"
        "   - Still blocked? → call ask_user — manual intervention required\n"
        "\n"
        "4. Do NOT retry immediately — this will make the block worse"
    ),
}


# ---------------------------------------------------------------------------
# Allowed transitions
# ---------------------------------------------------------------------------

@dataclass
class WorkflowTransition:
    """A valid transition between two workflow states."""

    from_state: WorkflowState
    to_state: WorkflowState
    condition: str = ""


# All valid transitions in the state machine.
_ALLOWED_TRANSITIONS: list[WorkflowTransition] = [
    WorkflowTransition(WorkflowState.START, WorkflowState.DISCOVER, "initial"),
    WorkflowTransition(WorkflowState.START, WorkflowState.APPLY, "direct action"),
    WorkflowTransition(WorkflowState.START, WorkflowState.MONITOR, "watch page"),
    WorkflowTransition(WorkflowState.DISCOVER, WorkflowState.SELECT, "item found"),
    WorkflowTransition(WorkflowState.DISCOVER, WorkflowState.ALTERNATE_SEARCH, "no results"),
    WorkflowTransition(WorkflowState.ALTERNATE_SEARCH, WorkflowState.DISCOVER, "retry search"),
    WorkflowTransition(WorkflowState.SELECT, WorkflowState.APPLY, "item chosen"),
    WorkflowTransition(WorkflowState.APPLY, WorkflowState.DISCOVER, "next item"),
    WorkflowTransition(WorkflowState.APPLY, WorkflowState.COMPLETE, "done"),
    WorkflowTransition(WorkflowState.APPLY, WorkflowState.AWAIT_USER, "blocked"),
    WorkflowTransition(WorkflowState.MONITOR, WorkflowState.COMPLETE, "change detected"),
    WorkflowTransition(WorkflowState.MONITOR, WorkflowState.AWAIT_USER, "timeout/blocked"),
    WorkflowTransition(WorkflowState.COMPLETE, WorkflowState.DISCOVER, "restart"),
    WorkflowTransition(WorkflowState.AWAIT_USER, WorkflowState.APPLY, "user replied"),
]


class LocalWorkflowPolicy:
    """Finite-state machine for the browser agent workflow.

    Tracks the current state, validates transitions, and generates
    the appropriate prompt block for the current context.
    """

    def __init__(self, initial_state: WorkflowState = WorkflowState.START) -> None:
        self._state: WorkflowState = initial_state

    @property
    def state(self) -> WorkflowState:
        return self._state

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def transition_to(self, new_state: WorkflowState) -> bool:
        """Attempt a state transition. Returns True if allowed."""
        for t in _ALLOWED_TRANSITIONS:
            if t.from_state == self._state and t.to_state == new_state:
                self._state = new_state
                return True
        return False

    def suggest_transition(
        self,
        page_type: PageType,
        *,
        no_progress_streak: int = 0,
        cycle_detected: bool = False,
    ) -> WorkflowState | None:
        """Suggest a state transition based on the current context.

        Returns ``None`` when no transition is needed.
        """
        # Cycle → force ALTERNATE_SEARCH if currently discovering
        if cycle_detected and self._state == WorkflowState.DISCOVER:
            return WorkflowState.ALTERNATE_SEARCH

        # No progress → no automatic progression
        if no_progress_streak >= 2:
            return None

        # Page-type-based suggestion
        suggested = _PAGE_TYPE_TO_STATE.get(page_type)
        if suggested and suggested != self._state:
            # Only suggest if the transition is valid
            for t in _ALLOWED_TRANSITIONS:
                if t.from_state == self._state and t.to_state == suggested:
                    return suggested

        # Natural progression in the state chain
        try:
            idx = _STATE_CHAIN.index(self._state)
            if idx < len(_STATE_CHAIN) - 1:
                next_state = _STATE_CHAIN[idx + 1]
                for t in _ALLOWED_TRANSITIONS:
                    if t.from_state == self._state and t.to_state == next_state:
                        return next_state
        except ValueError:
            pass

        return None

    # ------------------------------------------------------------------
    # Prompt generation
    # ------------------------------------------------------------------

    def build_prompt_block(
        self,
        *,
        page_type: PageType | None = None,
        no_progress_streak: int = 0,
        cycle_detected: bool = False,
    ) -> str:
        """Build the adaptive prompt block for the current context.

        Returns a string to append to the base system prompt.
        """
        blocks: list[str] = []

        # State-specific guidance
        state_prompt = _STATE_PROMPTS.get(self._state, "")
        if state_prompt:
            blocks.append(state_prompt)

        # Page-type guidance overrides state guidance when the page
        # clearly indicates a different interaction mode.
        if page_type is not None and self._state in (WorkflowState.START, WorkflowState.DISCOVER):
            if page_type == PageType.LOGIN_PAGE:
                blocks.append(
                    "\n---\n"
                    "📋 PAGE TYPE: LOGIN\n"
                    "A login form is visible.\n"
                    "1. If you have credentials, fill them in.\n"
                    "2. If no credentials available, call ask_user."
                )
            elif page_type == PageType.FORM_PAGE and self._state != WorkflowState.APPLY:
                blocks.append(
                    "\n---\n"
                    "📋 PAGE TYPE: FORM\n"
                    "You need to fill in a form.\n"
                    "1. Fill required fields using data from the user's goal.\n"
                    "2. Use type_text with element_id from inspect_page.\n"
                    "3. After filling, look for a submit/save button."
                )

        # Anti-cycle warning
        if cycle_detected:
            blocks.append(_CYCLE_WARNING)
        elif no_progress_streak >= 1:
            blocks.append(_STUCK_TIP)

        return "\n".join(blocks)