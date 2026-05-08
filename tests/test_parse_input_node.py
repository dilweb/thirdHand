"""Tests for parse_input node fast paths."""

from src.thirdhand.agent.nodes.parse_input import (
    looks_like_pending_browser_followup,
    parse_input_node,
)
from src.thirdhand.agent.state import AgentState
from src.thirdhand.agent.schemas import TaskAnalysis


class TestParseInputNode:
    def test_looks_like_followup_accepts_sentence_sized_reply(self) -> None:
        text = "продолжай с этого места, я подтвердил номер"
        assert looks_like_pending_browser_followup(text) is True

    def test_looks_like_followup_accepts_single_newline_in_message(self) -> None:
        text = "зайди на hh вакансии\nвот номер для входа"
        assert looks_like_pending_browser_followup(text) is True

    def test_resumes_pending_browser_task_when_step_limit_blocker(self) -> None:
        state = AgentState(
            user_id=123,
            message_text="продолжай",
            pending_task={
                "intent": "browser_task",
                "requires_browser": True,
                "browser_goal": "Вход на hh.ru",
                "blocker_type": "missing_info",
                "browser_final_url": "https://hh.ru/account/login",
                "awaiting_user_step": True,
            },
        )

        result = parse_input_node(state)

        assert result["intent"] == "browser_task"
        assert "USER_OBJECTIVE" in result["browser_goal"]
        assert "продолжай" in result["browser_goal"]
        assert "https://hh.ru/account/login" in result["browser_goal"]
        assert "https://hh.ru/account/login" in state.pending_task.get("browser_final_url", "")

    def test_resumes_pending_browser_task_on_ready_message(self) -> None:
        state = AgentState(
            user_id=123,
            message_text="готово",
            pending_task={
                "intent": "browser_task",
                "requires_browser": True,
                "browser_goal": "Откликнуться на вакансии Python на hh.ru",
                "blocker_type": "login",
                "browser_final_url": "https://hh.ru/account/login",
                "awaiting_user_step": True,
            },
        )

        result = parse_input_node(state)

        assert result["intent"] == "browser_task"
        assert result["requires_browser"] is True
        assert result["requires_web_search"] is False
        assert "USER_OBJECTIVE" in result["browser_goal"]
        assert "Откликнуться на вакансии Python на hh.ru" in result["browser_goal"]
        assert result["user_goal"] == "Откликнуться на вакансии Python на hh.ru"

    def test_resumes_pending_browser_task_without_keyword_heuristic(self) -> None:
        state = AgentState(
            user_id=123,
            message_text="не могу пройти этот шаг, там другая форма",
            pending_task={
                "intent": "browser_task",
                "requires_browser": True,
                "browser_goal": "Оформить заказ",
                "blocker_type": "confirmation",
                "awaiting_user_step": True,
            },
        )

        result = parse_input_node(state)

        assert result["intent"] == "browser_task"
        assert "Оформить заказ" in result["browser_goal"]
        assert result["requires_browser"] is True

    def test_does_not_auto_resume_pending_browser_task_for_other_blocker(self) -> None:
        state = AgentState(
            user_id=123,
            message_text="откликнись на hh на вакансии python разработчик",
            pending_task={
                "intent": "browser_task",
                "requires_browser": True,
                "browser_goal": "Оформить заказ",
                "blocker_type": "other",
                "awaiting_user_step": True,
            },
        )

        result = parse_input_node(state)

        assert result["browser_goal"] != "Оформить заказ"

    def test_browser_task_always_forces_requires_browser_true(self, monkeypatch) -> None:
        """Classifier sometimes sets requires_browser=false for 'provide password' follow-ups; we always run the browser."""
        def fake_safe_invoke(chain, llm_input, fallback=None):
            return TaskAnalysis(
                intent="browser_task",
                browser_goal="Continue login on hh.ru",
                user_goal="login",
                requires_browser=False,
                requires_web_search=False,
                missing_context=["password_or_code"],
            )

        monkeypatch.setattr(
            "src.thirdhand.agent.nodes.parse_input.safe_invoke",
            fake_safe_invoke,
        )
        state = AgentState(user_id=1, message_text="попробуй еще раз")
        result = parse_input_node(state)

        assert result["intent"] == "browser_task"
        assert result["requires_browser"] is True
        assert result["entities"]["requires_browser"] is True
        state = AgentState(
            user_id=123,
            message_text="ты сам должен это сделать поищи на headhunter",
        )

        def fake_safe_invoke(chain, llm_input, fallback=None):
            return TaskAnalysis(
                intent="browser_task",
                browser_goal="Search for 'python разработчик' vacancies on hh.ru and apply to them using the saved resume link.",
                user_goal="Apply to Python Developer vacancies on hh.ru",
                requires_browser=True,
                required_context=["vacancy_links", "resume_link"],
                missing_context=["vacancy_links"],
                clarification_question="Дай ссылки на вакансии",
            )

        monkeypatch.setattr("src.thirdhand.agent.nodes.parse_input.safe_invoke", fake_safe_invoke)

        result = parse_input_node(state)

        assert result["intent"] == "browser_task"
        assert result["requires_browser"] is True
        assert result["missing_context"] == []
        assert result["clarification_question"] == ""
