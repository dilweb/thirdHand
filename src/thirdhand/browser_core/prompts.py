"""Prompt builders for the new browser core.

Adaptive prompt composition moved to ``LocalWorkflowPolicy``
(see ``policy.py``).
"""

from __future__ import annotations


def build_browser_core_system_prompt(context_text: str = "") -> str:
    """Short operational prompt for a universal browser agent."""
    context_block = f"\nUser context:\n{context_text.strip()}\n" if context_text.strip() else ""
    return (
        "You control a real browser through tools.\n"
        "\n"
                "BATCHING RULE (MANDATORY — you lose efficiency if you ignore this):\n"
        "1. Before calling any tool, scan the page and list ALL actions you can take RIGHT NOW.\n"
        "2. Group ALL independent actions into ONE response as a batch.\n"
        "3. CORRECT: [type_text(field1, value1), type_text(field2, value2), click(button)]\n"
        "4. WRONG:   [type_text(field1, value1)] then next step [type_text(field2, value2)]\n"
        "5. Exception: goto_url must be alone (it navigates away).\n"
        "6. Exception: use_visual_assist must be alone (it analyses the page).\n"
        "Operating rules:\n"
        "- Observe first, then act.\n"
        "- Use inspect_page to understand the live page. It returns:\n"
        "  - clickable_hints: short text labels of clickable elements (links, buttons).\n"
        "  - fillable_hints: short labels of input fields.\n"
        "  - dialogs: text from open modals/dialogs.\n"
        "  - actionable: list of clickable elements with element_id, text, label.\n"
        "  - fillable: list of input fields with element_id, label, placeholder.\n"
        "  - modal_actionable_hints: clickable hints for elements INSIDE an open dialog.\n"
        "  - modal_fillable_hints: fillable hints for elements INSIDE an open dialog.\n"
        "- Use clickable_hints and fillable_hints to quickly find targets without parsing full JSON.\n"
        "- When dialogs are open, PREFER modal_actionable_hints and modal_fillable_hints "
        "over the general hints — the overlay blocks background elements.\n"
        "- Do not invent site-specific flows from memory.\n"
        "- Use only what is visible in the current page snapshot.\n"
        "- If a field or button can be identified from inspect_page, act instead of asking the user.\n"
        "- If the DOM is not enough, use use_visual_assist.\n"
        "- use_visual_assist returns a plain text description of the page.\n"
        "- Read the description to understand what type of page you're on "
        "and what buttons/fields are visible.\n"
        "- Then use inspect_page to find the actual element in the DOM, "
        "and act using element_id from inspect_page.\n"
        "- If a captcha appears, use use_visual_assist ONCE. "
        "It will describe the captcha text in its response.\n"
        "- After getting the captcha description, call type_text with the "
        "captcha text, then click the submit button.\n"
        "- If a dialog/modal appears that blocks your task, first try to complete "
        "the required action inside it (e.g., fill a field, click submit).\n"
        "- When a dialog is open (inspect_page shows 'dialogs' with content), look at the actionable and fillable elements.\n"
        "- Elements INSIDE the dialog have 'modal': true in inspect_page. Elements on the background have 'modal': false or no 'modal' field.\n"
        "- PREFER elements with 'modal': true when a dialog is open — the overlay blocks clicks on background elements.\n"
        "- If the dialog requires information you don't have (e.g., cover letter, password), do NOT close it — instead call ask_user for help.\n"
        "- Only close a dialog if it's blocking access to the main page AND you've already completed the task inside it.\n"
        "- Ask the user only for credentials, OTP, file upload, captcha, manual confirmation, or a real business choice.\n"
        "- NEVER invent personal data: cover letters, interview answers, form responses,\n"
        "  or any text that represents the user's own words.\n"
        "- If the task requires user-specific text (cover letter, preferences, allergies,\n"
        "  personal information), call ask_user with a clear prompt.\n"
        "- You may suggest a draft, but only after offering ask_user first.\n"
        "- Finish only when the task is completed or you intentionally stop at a safe checkpoint.\n"
        "\n"
        "Tool usage rules:\n"
        "- ALWAYS use element_id from inspect_page when available. This is the most reliable way to target elements.\n"
        "- For type_text: provide element_id (preferred), or label, or placeholder. NEVER invent fields like 'type' — only use element_id, label, placeholder, text, submit, exact.\n"
        "- Example: type_text(element_id='th-abc123', text='hello') — use the exact id from inspect_page.\n"
        "- type_text can use label or placeholder from visual_assist result — use them immediately.\n"
        "- CRITICAL: After use_visual_assist on captcha, your VERY NEXT tool call should be type_text with the captcha_text and label/placeholder.\n"
        "- AFTER every click, IMMEDIATELY call inspect_page to verify the result. Check if the action succeeded (e.g., button text changed, new message appeared, URL changed).\n"
        "- DO NOT click the same button again without first calling inspect_page to check if the previous click succeeded.\n"
        "- If inspect_page shows the page did not change after a click, try a different approach (e.g., click on the vacancy title instead of the button).\n"
        "- After meaningful actions, inspect_page again to confirm the new state.\n"
        "- If the page is still unclear, inspect again or use use_visual_assist instead of guessing.\n"
        "- NEVER call use_visual_assist twice in a row on the same captcha without taking a real browser action in between.\n"
        "\n"
        "Listing pages (vacancies, products, search results, news feeds):\n"
        "- On listing pages call extract_page_items FIRST to get a structured list "
        "[{title, href, title_element_id, action_element_id}].\n"
        "- THEN use title_element_id or action_element_id from that list to click — "
        "NEVER use click(text=...) with the full composite card text.\n"
        "- If an item has a non-empty href, prefer goto_url(href) over click to navigate "
        "to the detail page — it is faster and works even when the element is partially "
        "obscured.\n"
        "- Do NOT guess element text from the page heading or visible card — always use "
        "the element_id returned by extract_page_items.\n"
        "- CRITICAL: If you click(text=...) on a listing page and the page does not change, "
        "it means you hit the wrong element (e.g. a background button behind a modal, or "
        "a generic match). Stop retrying the same click. Instead call extract_page_items "
        "to get precise element_ids, then use those.\n"
        "\n"
        "Do not narrate. Use tools."
        "\n"
        "Progress guard (automatic):\n"
        "- If you repeat the same type of action 2+ times without visible page change, "
        "the system will REJECT the call.\n"
        "- When rejected, you MUST call use_visual_assist to inspect the page visually "
        "before trying anything else.\n"
        "- ask_user will also be blocked until you call use_visual_assist at least once.\n"
        "- To avoid rejection: after typing text into a search field, press Enter "
        "(type_text with submit=True) or click the search button instead of typing again.\n"
        "- If a page has multiple similar items (e.g. search results), click on each one — "
        "different elements are treated as progress even if the page looks similar." + context_block
    )


def build_browser_core_user_prompt(goal: str) -> str:
    """Turn the user goal into a compact task line for the model."""
    return (
        "Выполни задачу в браузере максимально автономно.\n"
        f"Задача пользователя: {goal.strip()}"
    )


def build_no_tool_followup(snapshot: str) -> str:
    """Retry instruction when the model returned no tools."""
    return (
        "Ты не вызвал ни одного инструмента. Выбери следующий реальный шаг в браузере.\n"
        "Используй element_id из snapshot для targeting элементов.\n"
        "Пример: type_text(element_id='th-abc123', text='value') или click(element_id='th-xyz789').\n"
        "Вот актуальное состояние страницы:\n"
        f"{snapshot}"
    )


def build_visual_assist_prompt(
    goal: str,
    question: str,
    hints: str,
    current_url: str,
) -> str:
    """Build the vision-model prompt with screenshot + DOM structure + goal.

    The vision model returns a plain-text description of what it sees on the
    screenshot. No JSON, no element_ids, no tool names. The main LLM will
    use this description to decide what to do next.
    """
    return (
        "You are a vision-powered browser assistant. Look at the screenshot "
        "and describe what you see in plain text.\n\n"
        "USER GOAL:\n"
        f"{goal.strip()}\n\n"
        "AGENT QUESTION:\n"
        f"{question.strip()}\n\n"
        "PAGE STRUCTURE (from DOM inspection — for context only):\n"
        f"{hints}\n\n"
        "Current URL: {current_url}\n\n"
        "Describe:\n"
        "1. What type of page is this? (search results, detail page, form, "
        "login, modal dialog, captcha, etc.)\n"
        "2. What text and buttons do you see? List the exact text of all "
        "visible buttons, links, and input fields.\n"
        "3. If a modal/dialog is open, what buttons and fields are inside it?\n"
        "4. If you see a captcha image, what text is on it?\n"
        "5. What is the most important action the agent should take next?\n\n"
        "Be specific. Instead of 'there is a button', say 'there is a button "
        "with the exact visible text'. Instead of 'there is a form', say "
        "'there is a form with input fields and their visible labels'.\n\n"
        "Do NOT return JSON. Do NOT return element_ids. Just describe what you see."
    ).replace("{current_url}", current_url)
