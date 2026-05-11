# Принудительный use_visual_assist при застревании

## Проблема

Когда `no_progress_streak >= 2`, агент получает сообщение:
> "Последние шаги не продвинули задачу. Используй use_visual_assist чтобы понять что делать дальше."

Но LLM может **проигнорировать** это сообщение и продолжить делать то же самое. В логе:

```
step 4: type_text → no_progress_streak=1
step 5: type_text → no_progress_streak=2 → hint: "используй use_visual_assist"
step 6: type_text → no_progress_streak=3 → STOP (игнорировал hint)
```

## Решение: intercept-механизм

Когда `no_progress_streak >= 2`, следующий tool call перехватывается. Если агент пытается вызвать **тот же инструмент**, который привёл к застреванию, вызов **отклоняется** с сообщением, принуждающим использовать `use_visual_assist`.

### Детальная схема

```mermaid
flowchart TD
    A[LLM выбирает tool] --> B{no_progress_streak >= 2?}
    B -->|Нет| C[Выполнить tool нормально]
    B -->|Да| D{Тот же tool_name,\nчто вызвал застревание?}
    D -->|Нет, другой инструмент| C
    D -->|Да, тот же| E[Отклонить вызов]
    E --> F[Добавить ToolMessage с ошибкой]
    F --> G[Добавить HumanMessage:\n"Ты должен использовать\nuse_visual_assist"]
    G --> H[continue → LLM выберет\nзаново]
```

### Где вставляется intercept

В [`agent_loop.py:269`](src/thirdhand/browser_core/agent_loop.py:269), перед выполнением tool:

```python
# Текущий код:
tool = tools[tool_name]
try:
    result = await tool.ainvoke(args)
except Exception as exc:
    result = f"ERROR: {type(exc).__name__}: {exc}"

# Новый код:
if tracking.no_progress_streak >= 2 and _is_stuck_tool(tool_name, tracking):
    result = (
        "ERROR: Action rejected — you are repeating the same type of action "
        "without making progress.\n"
        "You MUST call use_visual_assist to understand the page before "
        "taking any other action."
    )
    # Сбрасывать no_progress_streak НЕ нужно — пусть растёт дальше
    # если агент продолжит игнорировать
else:
    tool = tools[tool_name]
    try:
        result = await tool.ainvoke(args)
    except Exception as exc:
        result = f"ERROR: {type(exc).__name__}: {exc}"
```

### Функция `_is_stuck_tool`

```python
_STUCK_OBSERVATION_TOOLS = {"open_browser", "goto_url", "click", "type_text", "press_key", "scroll", "wait"}

def _is_stuck_tool(tool_name: str, tracking: BrowserTrackingState) -> bool:
    """Проверить, является ли tool тем же типом, что вызвал застревание.
    
    Логика:
    - Если tool не входит в список observation tools → не блокируем
      (use_visual_assist, ask_user, finish_task — всегда разрешены)
    - Если tool_name совпадает с последним застрявшим tool → блокируем
    """
    if tool_name not in _STUCK_OBSERVATION_TOOLS:
        return False  # никогда не блокируем use_visual_assist, ask_user, finish_task
    
    # Блокируем, если это тот же тип инструмента, который вызвал застревание
    return tool_name == tracking.last_stuck_tool_name
```

### Новая переменная в BrowserTrackingState

```python
@dataclass
class BrowserTrackingState:
    # ... существующие поля ...
    last_stuck_tool_name: str = ""  # Инструмент, который вызвал застревание
    
    def check_progress(self, ...):
        if not progress:
            if self.no_progress_streak == 1:
                # Запомнили, какой инструмент вызвал первое отсутствие прогресса
                self.last_stuck_tool_name = tool_name
            self.no_progress_streak += 1
            return False
        else:
            self.no_progress_streak = 0
            self.last_stuck_tool_name = ""  # сброс при прогрессе
            return True
```

### Поведение для разных сценариев

| Сценарий | no_progress_streak | Поведение |
|----------|-------------------|-----------|
| type_text без Enter → снова type_text | 2 | Второй type_text отклонён → агент вынужден использовать use_visual_assist |
| type_text без Enter → click на "Найти" | 2 | click разрешён (другой tool) → прогресс |
| click на фильтр → снова click на фильтр | 2 | Второй click отклонён → агент вынужден использовать use_visual_assist |
| click на фильтр → scroll | 2 | scroll разрешён (другой tool) |
| Любой сценарий → use_visual_assist | 2+ | use_visual_assist всегда разрешён |

### Почему это масштабируемо

1. **Не зависит от сайта** — работает на hh.ru, avito, google.com, любом сайте
2. **Не зависит от языка** — use_visual_assist использует screenshot, а не текст
3. **Не хардкодит конкретные действия** — блокируется ТОЛЬКО повтор того же типа инструмента
4. **Агент может выйти из застревания** двумя способами:
   - Позвать `use_visual_assist` (посмотреть на страницу)
   - Позвать **другой** инструмент (например, вместо type_text нажать Enter или click)

### Тесты

```python
def test_intercept_same_tool_when_stuck():
    """При no_progress_streak=2, тот же tool_name блокируется."""
    tracking = BrowserTrackingState()
    tracking.no_progress_streak = 2
    tracking.last_stuck_tool_name = "type_text"
    assert _is_stuck_tool("type_text", tracking)  # блокируется

def test_allow_different_tool_when_stuck():
    """При no_progress_streak=2, другой tool_name разрешён."""
    tracking = BrowserTrackingState()
    tracking.no_progress_streak = 2
    tracking.last_stuck_tool_name = "type_text"
    assert not _is_stuck_tool("click", tracking)  # разрешён

def test_always_allow_visual_assist():
    """use_visual_assist никогда не блокируется."""
    tracking = BrowserTrackingState()
    tracking.no_progress_streak = 2
    tracking.last_stuck_tool_name = "type_text"
    assert not _is_stuck_tool("use_visual_assist", tracking)

def test_always_allow_ask_user():
    """ask_user никогда не блокируется."""
    tracking = BrowserTrackingState()
    tracking.no_progress_streak = 2
    tracking.last_stuck_tool_name = "type_text"
    assert not _is_stuck_tool("ask_user", tracking)

def test_no_intercept_when_not_stuck():
    """При no_progress_streak < 2, ничего не блокируется."""
    tracking = BrowserTrackingState()
    tracking.no_progress_streak = 1
    assert not _is_stuck_tool("type_text", tracking)
```

### Файлы для изменения

| Файл | Изменение |
|------|-----------|
| `src/thirdhand/browser_core/tracking.py` | Добавить `last_stuck_tool_name` в `BrowserTrackingState`, сохранять в `check_progress` |
| `src/thirdhand/browser_core/agent_loop.py` | Добавить intercept перед выполнением tool (строка 269), добавить `_is_stuck_tool()` |
| `tests/test_tracking.py` | Добавить тесты для `last_stuck_tool_name` |
| `tests/test_agent_loop.py` или новый тест | Добавить тесты для intercept-механизма |