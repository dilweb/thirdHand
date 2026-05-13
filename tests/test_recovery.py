"""Tests for the RecoveryLayer."""

from src.thirdhand.browser_core.recovery import (
    RecoveryAction,
    RecoveryDecision,
    RecoveryLayer,
)


class TestIsToolBlocked:
    def test_not_blocked_when_streak_below_2(self) -> None:
        assert not RecoveryLayer.is_tool_blocked(
            "type_text",
            no_progress_streak=1,
            last_stuck_tool_name="type_text",
            visual_assist_called_during_stuck=False,
        )

    def test_blocks_same_tool_when_stuck(self) -> None:
        assert RecoveryLayer.is_tool_blocked(
            "type_text",
            no_progress_streak=2,
            last_stuck_tool_name="type_text",
            visual_assist_called_during_stuck=False,
        )

    def test_allows_different_tool_when_stuck(self) -> None:
        assert not RecoveryLayer.is_tool_blocked(
            "click",
            no_progress_streak=2,
            last_stuck_tool_name="type_text",
            visual_assist_called_during_stuck=False,
        )

    def test_always_allows_visual_assist(self) -> None:
        assert not RecoveryLayer.is_tool_blocked(
            "use_visual_assist",
            no_progress_streak=2,
            last_stuck_tool_name="type_text",
            visual_assist_called_during_stuck=False,
        )

    def test_blocks_ask_user_before_visual_assist(self) -> None:
        assert RecoveryLayer.is_tool_blocked(
            "ask_user",
            no_progress_streak=2,
            last_stuck_tool_name="type_text",
            visual_assist_called_during_stuck=False,
        )

    def test_allows_ask_user_after_visual_assist(self) -> None:
        assert not RecoveryLayer.is_tool_blocked(
            "ask_user",
            no_progress_streak=2,
            last_stuck_tool_name="type_text",
            visual_assist_called_during_stuck=True,
        )

    def test_always_allows_finish_task(self) -> None:
        assert not RecoveryLayer.is_tool_blocked(
            "finish_task",
            no_progress_streak=2,
            last_stuck_tool_name="type_text",
            visual_assist_called_during_stuck=False,
        )

    def test_always_allows_inspect_page(self) -> None:
        assert not RecoveryLayer.is_tool_blocked(
            "inspect_page",
            no_progress_streak=2,
            last_stuck_tool_name="type_text",
            visual_assist_called_during_stuck=False,
        )

    def test_not_blocked_with_empty_last_tool(self) -> None:
        assert not RecoveryLayer.is_tool_blocked(
            "type_text",
            no_progress_streak=2,
            last_stuck_tool_name="",
            visual_assist_called_during_stuck=False,
        )

    def test_blocks_scroll_when_stuck_on_scroll(self) -> None:
        assert RecoveryLayer.is_tool_blocked(
            "scroll",
            no_progress_streak=2,
            last_stuck_tool_name="scroll",
            visual_assist_called_during_stuck=False,
        )

    def test_blocks_click_when_stuck_on_click(self) -> None:
        assert RecoveryLayer.is_tool_blocked(
            "click",
            no_progress_streak=2,
            last_stuck_tool_name="click",
            visual_assist_called_during_stuck=False,
        )


class TestBuildBlockMessage:
    def test_block_message_for_ask_user(self) -> None:
        msg = RecoveryLayer.build_block_message("ask_user")
        assert "ERROR" in msg
        assert "use_visual_assist" in msg
        assert "ask_user" not in msg.lower().split("call")[-1]  # second mention only

    def test_block_message_for_other_tools(self) -> None:
        msg = RecoveryLayer.build_block_message("type_text")
        assert "ERROR" in msg
        assert "type_text" in msg
        assert "use_visual_assist" in msg


class TestAssessNoProgress:
    def test_returns_continue_when_streak_below_2(self) -> None:
        decision = RecoveryLayer.assess_no_progress(1, {})
        assert decision.action == RecoveryAction.CONTINUE

    def test_returns_vision_assist_at_streak_2(self) -> None:
        decision = RecoveryLayer.assess_no_progress(2, {})
        assert decision.action == RecoveryAction.VISION_ASSIST
        assert "use_visual_assist" in decision.message

    def test_returns_alternative_policy_at_streak_3(self) -> None:
        decision = RecoveryLayer.assess_no_progress(3, {})
        assert decision.action == RecoveryAction.ALTERNATIVE_POLICY
        assert decision.metadata.get("escalation_reason") == "alternative_policy"

    def test_returns_replan_at_streak_4(self) -> None:
        decision = RecoveryLayer.assess_no_progress(4, {})
        assert decision.action == RecoveryAction.REPLAN
        assert decision.metadata.get("escalation_reason") == "replan"

    def test_returns_human_intervention_at_streak_5(self) -> None:
        decision = RecoveryLayer.assess_no_progress(5, {})
        assert decision.action == RecoveryAction.HUMAN_INTERVENTION
        assert "Не удалось" in decision.message
        assert decision.metadata.get("escalation_reason") == "no_progress"

    def test_vision_assist_includes_hints(self) -> None:
        snapshot = {
            "dialogs": ["Какое-то модальное окно"],
            "clickable_hints": ["Откликнуться", "Посмотреть"],
            "fillable_hints": ["Имя", "Email"],
        }
        decision = RecoveryLayer.assess_no_progress(2, snapshot)
        assert decision.action == RecoveryAction.VISION_ASSIST
        assert "диалоги" in decision.message.lower() or "модал" in decision.message.lower()
        assert "Откликнуться" in decision.message


class TestAssessVisualAssistResult:
    def test_returns_none_for_normal_page(self) -> None:
        decision = RecoveryLayer.assess_visual_assist_result(
            {"task_type": "form", "next_action": "fill form"},
            visual_assist_same_page_streak=1,
        )
        assert decision is None

    def test_returns_none_for_empty_payload(self) -> None:
        decision = RecoveryLayer.assess_visual_assist_result(
            {},
            visual_assist_same_page_streak=1,
        )
        assert decision is None

    def test_detects_captcha_first_time(self) -> None:
        decision = RecoveryLayer.assess_visual_assist_result(
            {
                "task_type": "captcha",
                "captcha_text": "abc123",
                "label": "captcha",
                "button_hint": "Submit",
            },
            visual_assist_same_page_streak=1,
        )
        assert decision is not None
        assert decision.action == RecoveryAction.RETRY
        assert "abc123" in decision.message
        assert decision.metadata.get("task_type") == "captcha"

    def test_escalates_captcha_to_human_after_repeated_streak(self) -> None:
        decision = RecoveryLayer.assess_visual_assist_result(
            {
                "task_type": "captcha",
                "captcha_text": "abc123",
            },
            visual_assist_same_page_streak=2,
        )
        assert decision is not None
        assert decision.action == RecoveryAction.HUMAN_INTERVENTION
        assert decision.metadata.get("escalation_reason") == "captcha_visual_assist_stuck"

    def test_ignores_captcha_without_text(self) -> None:
        decision = RecoveryLayer.assess_visual_assist_result(
            {"task_type": "captcha", "captcha_text": ""},
            visual_assist_same_page_streak=1,
        )
        assert decision is None


class TestRecoveryActionEnum:
    def test_has_all_expected_values(self) -> None:
        values = {a.value for a in RecoveryAction}
        expected = {"continue", "retry", "alternative_policy", "vision_assist", "replan", "human_intervention"}
        assert values == expected

    def test_recovery_decision_defaults(self) -> None:
        d = RecoveryDecision(action=RecoveryAction.CONTINUE)
        assert d.message == ""
        assert d.metadata == {}