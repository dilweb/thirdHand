"""Tests for parse_input pending-task behavior."""

from src.thirdhand.agent.nodes.parse_input import looks_like_pending_browser_followup, parse_input_node
from src.thirdhand.agent.schemas import TaskAnalysis
from src.thirdhand.agent.state import AgentState


class TestParseInputNode:
    def test_looks_like_followup_accepts_sentence_sized_reply(self) -> None:
        text = "продолжай с этого места, я подтвердил номер"
        assert looks_like_pending_browser_followup(text) is True

    def test_looks_like_followup_accepts_single_newline_in_message(self) -> None:
        text = "зайди на hh вакансии\nвот номер для входа"
        assert looks_like_pending_browser_followup(text) is True

    def test_continue_pending_browser_task_is_llm_decision(self, monkeypatch) -> None:
        def fake_safe_invoke(chain, llm_input, fallback=None):
            return TaskAnalysis(
                intent="browser_task",
                browser_goal="Продолжить вход на hh.ru",
                user_goal="Продолжить вход на hh.ru",
                requires_browser=True,
                requires_web_search=False,
                continue_pending_task=True,
            )

        monkeypatch.setattr("src.thirdhand.agent.nodes.parse_input.safe_invoke", fake_safe_invoke)
        state = AgentState(
            user_id=123,
            message_text="продолжай",
            pending_task={
                "intent": "browser_task",
                "requires_browser": True,
                "browser_goal": "Вход на hh.ru",
                "canonical_user_objective": "Вход на hh.ru",
                "blocker_type": "missing_info",
                "browser_final_url": "https://hh.ru/account/login",
                "awaiting_user_step": True,
            },
        )

        result = parse_input_node(state)

        assert result["intent"] == "browser_task"
        assert result["continue_pending_task"] is True
        assert result["requires_browser"] is True
        assert "USER_OBJECTIVE" in result["browser_goal"]
        assert "https://hh.ru/account/login" in result["browser_goal"]
        assert result["user_goal"] == "Вход на hh.ru"

    def test_chat_question_can_stay_inside_pending_browser_task(self, monkeypatch) -> None:
        def fake_safe_invoke(chain, llm_input, fallback=None):
            return TaskAnalysis(
                intent="chat",
                user_goal="Спросить про предыдущий browser-шаг",
                requires_browser=False,
                requires_web_search=False,
                continue_pending_task=True,
            )

        monkeypatch.setattr("src.thirdhand.agent.nodes.parse_input.safe_invoke", fake_safe_invoke)
        state = AgentState(
            user_id=123,
            message_text="ты использовал тул для распознавания картинки?",
            pending_task={
                "intent": "browser_task",
                "requires_browser": True,
                "browser_goal": "Откликнуться на вакансии Python на hh.ru",
                "canonical_user_objective": "Откликнуться на вакансии Python на hh.ru",
                "browser_final_url": "https://hh.ru/search/vacancy",
                "awaiting_user_step": True,
            },
        )

        result = parse_input_node(state)

        assert result["intent"] == "chat"
        assert result["continue_pending_task"] is True
        assert result["preserve_pending_task"] is True
        assert result["active_task_intent"] == "browser_task"
        assert result["active_task_context"]["browser_final_url"] == "https://hh.ru/search/vacancy"

    def test_new_browser_task_does_not_get_forced_into_old_pending_context(self, monkeypatch) -> None:
        def fake_safe_invoke(chain, llm_input, fallback=None):
            return TaskAnalysis(
                intent="browser_task",
                browser_goal="Открыть Gmail и проверить входящие",
                user_goal="Проверить Gmail",
                requires_browser=True,
                requires_web_search=False,
                continue_pending_task=False,
            )

        monkeypatch.setattr("src.thirdhand.agent.nodes.parse_input.safe_invoke", fake_safe_invoke)
        state = AgentState(
            user_id=123,
            message_text="теперь зайди в gmail и проверь письма",
            pending_task={
                "intent": "browser_task",
                "requires_browser": True,
                "browser_goal": "Оформить заказ",
                "canonical_user_objective": "Оформить заказ",
                "browser_final_url": "https://shop.example/checkout",
                "awaiting_user_step": True,
            },
        )

        result = parse_input_node(state)

        assert result["intent"] == "browser_task"
        assert result["continue_pending_task"] is False
        assert "shop.example" not in result["browser_goal"]
        assert result["user_goal"] == "Проверить Gmail"

    def test_browser_task_always_forces_requires_browser_true(self, monkeypatch) -> None:
        """Classifier sometimes sets requires_browser=false for browser continuations; parser normalizes it."""

        def fake_safe_invoke(chain, llm_input, fallback=None):
            return TaskAnalysis(
                intent="browser_task",
                browser_goal="Continue login on hh.ru",
                user_goal="login",
                requires_browser=False,
                requires_web_search=False,
                missing_context=["password_or_code"],
            )

        monkeypatch.setattr("src.thirdhand.agent.nodes.parse_input.safe_invoke", fake_safe_invoke)
        state = AgentState(user_id=1, message_text="попробуй еще раз")
        result = parse_input_node(state)

        assert result["intent"] == "browser_task"
        assert result["requires_browser"] is True
        assert result["entities"]["requires_browser"] is True

    def test_browser_missing_context_relaxed_for_autonomous_search(self, monkeypatch) -> None:
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
