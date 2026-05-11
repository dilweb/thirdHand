"""New universal browser core."""

from src.thirdhand.browser_core.api import (
    BrowserTaskResult,
    discard_parked_browser_session_for_user,
    run_browser_task,
)
from src.thirdhand.browser_core.agent_loop import BrowserCoreRunResult, run_browser_core_loop
from src.thirdhand.browser_core.goal_context import (
    build_operational_browser_goal,
    derive_canonical_objective_from_pending,
    strip_continuation_slab,
    truncate_display_title,
)
from src.thirdhand.browser_core.reporting import (
    format_pending_browser_diagnostic_reply,
    format_run_summary_telegram,
)
from src.thirdhand.browser_core.session import BrowserSession
from src.thirdhand.browser_core.sub_intent import (
    BrowserSubIntent,
    infer_browser_sub_intent,
)
from src.thirdhand.browser_core.user_blocking import (
    discard_parked_browser_core_session_for_user,
    run_browser_core_task,
)

__all__ = [
    "BrowserTaskResult",
    "BrowserCoreRunResult",
    "BrowserSession",
    "BrowserSubIntent",
    "build_operational_browser_goal",
    "discard_parked_browser_core_session_for_user",
    "discard_parked_browser_session_for_user",
    "derive_canonical_objective_from_pending",
    "format_pending_browser_diagnostic_reply",
    "format_run_summary_telegram",
    "infer_browser_sub_intent",
    "run_browser_task",
    "run_browser_core_loop",
    "run_browser_core_task",
    "strip_continuation_slab",
    "truncate_display_title",
]
