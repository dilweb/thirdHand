# План рефакторинга browser agent

## Контекст

Агент застревает при работе с листингами (HH.ru, маркетплейсы, любые списки карточек).  
Выявлено 5 системных проблем. Каждое изменение — конкретный разрез файла с указанием
что вырезать и чем заменить.

---

## Изменение 1 — Sliding window для `messages` (критично)

**Файл:** [`src/thirdhand/browser_core/agent_loop.py`](../src/thirdhand/browser_core/agent_loop.py)  
**Проблема:** Список `messages` растёт неограниченно. Каждый авто-`inspect_page` добавляет
~30–50k токенов. За 15–18 шагов → 800k–1.1M токенов → ошибки 402/400.

### Что добавить (новая функция перед `run_browser_core_loop`)

```python
# agent_loop.py — добавить после импортов, перед run_browser_core_loop

_MAX_TOOL_MESSAGE_PAIRS = 8  # сколько пар (AI + Tool) оставлять в истории


def _trim_messages(messages: list) -> list:
    """Оставляет system prompt, преамбулу с целью и последние N пар AI+Tool.

    Структура messages:
      [0]  SystemMessage           — system prompt (never trim)
      [1]  HumanMessage (goal)     — задача пользователя (never trim)
      [2]  HumanMessage (browser ready + initial snapshot)  (never trim)
      [3+] чередование AIMessage, HumanMessage(snapshot/followup), ToolMessage
    """
    from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

    if len(messages) <= 4:
        return messages

    # Всегда сохраняем первые 3 сообщения (system + goal + browser_ready)
    protected = messages[:3]
    rest = messages[3:]

    # Оставляем только последние _MAX_TOOL_MESSAGE_PAIRS * 3 сообщений
    # (примерно: AI + ToolMessage + HumanMessage(snapshot) = 3 на шаг)
    keep = _MAX_TOOL_MESSAGE_PAIRS * 3
    if len(rest) > keep:
        rest = rest[-keep:]

    return protected + rest
```

### Где вызвать (внутри цикла, строка 191)

```python
# БЫЛО (строка ~191):
ai_message = await ainvoke_with_retry(llm, messages)

# СТАЛО — одна строка перед вызовом:
messages = _trim_messages(messages)
ai_message = await ainvoke_with_retry(llm, messages)
```

---

## Изменение 2 — Компактный авто-снапшот после click/type/press_key

**Файл:** [`src/thirdhand/browser_core/agent_loop.py`](../src/thirdhand/browser_core/agent_loop.py)  
**Проблема:** После каждого `click`/`type_text`/`press_key` код на строках 417–467 вызывает
`session.inspect_page()` и добавляет полный JSON (~50k токенов) в `messages`.

### Что добавить в `inspect.py`

```python
# inspect.py — добавить функцию compact_inspect_page

async def compact_inspect_page(page: PageLike) -> str:
    """Возвращает компактный снапшот: url, заголовки, диалоги, подсказки.

    Размер: ~1–2k токенов вместо 30–50k полного inspect_page.
    Используется для авто-снапшотов после каждого действия.
    """
    _COMPACT_JS = """
    () => {
      const clean = v => (v || "").replace(/\\s+/g, " ").trim();
      const isVisible = el => {
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.visibility !== "hidden" && s.display !== "none" && r.width > 0 && r.height > 0;
      };
      const headings = [];
      for (const el of document.querySelectorAll("h1,h2,h3")) {
        if (!isVisible(el)) continue;
        const t = clean(el.innerText || "");
        if (t) headings.push(t.slice(0, 120));
        if (headings.length >= 6) break;
      }
      const dialogs = [];
      for (const el of document.querySelectorAll("dialog,[role='dialog'],[aria-modal='true']")) {
        if (!isVisible(el)) continue;
        const t = clean(el.innerText || "");
        if (t) dialogs.push(t.slice(0, 200));
        if (dialogs.length >= 3) break;
      }
      const actionable = [];
      for (const el of document.querySelectorAll("a,button,[role='button']")) {
        if (!isVisible(el) || el.disabled) continue;
        if (!el.dataset.thirdhandId)
          el.dataset.thirdhandId = "th-" + Math.random().toString(36).slice(2,10);
        const text = clean(el.innerText || el.getAttribute("aria-label") || "").slice(0,60);
        if (text) actionable.push({ id: el.dataset.thirdhandId, text });
        if (actionable.length >= 15) break;
      }
      const fillable = [];
      for (const el of document.querySelectorAll("input,textarea,select,[contenteditable='true']")) {
        if (!isVisible(el)) continue;
        if (!el.dataset.thirdhandId)
          el.dataset.thirdhandId = "th-" + Math.random().toString(36).slice(2,10);
        const label = clean(el.getAttribute("aria-label") || "")
          || clean(el.closest("label,fieldset")?.innerText || "").slice(0,60)
          || clean(el.getAttribute("placeholder") || "").slice(0,60);
        fillable.push({ id: el.dataset.thirdhandId, label });
        if (fillable.length >= 8) break;
      }
      return {
        url: location.href,
        title: document.title || "",
        modal_open: dialogs.length > 0,
        headings,
        dialogs,
        actionable,
        fillable,
      };
    }
    """
    result = await page.evaluate(_COMPACT_JS)
    import json
    return json.dumps(result, ensure_ascii=False)
```

### Что изменить в `agent_loop.py` (строки 417–467)

```python
# БЫЛО — один и тот же блок для click/type_text/press_key и _OBSERVATION_TOOLS:
fresh_snapshot = await session.inspect_page()
...
messages.append(HumanMessage(content=f"Новое состояние страницы:\n{fresh_snapshot}"))

# СТАЛО — использовать compact snapshot для авто-снапшотов:
from src.thirdhand.browser_core.inspect import compact_inspect_page

fresh_snapshot = await compact_inspect_page(session.page)
latest_snapshot_text = fresh_snapshot
latest_snapshot = _parse_snapshot(fresh_snapshot)
trace.append("inspect_page(compact): {}")
messages.append(HumanMessage(content=f"Страница после действия:\n{fresh_snapshot}"))
# Далее без изменений — progress check, tracking и т.д.
```

> `inspect_page` как инструмент LLM остаётся прежним (полный JSON по запросу агента).
> Меняются только **авто-снапшоты**, которые система добавляет сама.

---

## Изменение 3 — Modal-scoped авто-ресолвер в `type_text`

**Файл:** [`src/thirdhand/browser_core/tools.py`](../src/thirdhand/browser_core/tools.py)  
**Проблема:** Авто-ресолвер `type_text` (строки 256–285) ищет поля по всему DOM.
Когда открыт диалог (форма отклика), он находит поле `Исключить слова` вместо
поля `Сопроводительное письмо` в диалоге.

### Что заменить в функции `type_text` (блок AUTO-DISCOVERY, строки 256–285)

```python
# БЫЛО:
snapshot_json = await session.inspect_page()
snapshot = json.loads(snapshot_json) if isinstance(snapshot_json, str) else snapshot_json
candidates = snapshot.get("fillable") or []
target = _find_by_substring(candidates, "label", label)
if not target:
    target = _find_by_substring(candidates, "placeholder", placeholder)

# СТАЛО — добавить modal-scope:
snapshot_json = await session.inspect_page()
snapshot = json.loads(snapshot_json) if isinstance(snapshot_json, str) else snapshot_json
all_fillable = snapshot.get("fillable") or []

# Если открыт диалог — ищем только внутри него
modal_open = bool(snapshot.get("dialogs"))
if modal_open:
    candidates = [el for el in all_fillable if el.get("modal")]
    if not candidates:
        candidates = all_fillable  # fallback если modal-флаг не проставлен
else:
    candidates = all_fillable

target = _find_by_substring(candidates, "label", label)
if not target:
    target = _find_by_substring(candidates, "placeholder", placeholder)
```

> Поле `modal` уже проставляется в [`inspect.py`](../src/thirdhand/browser_core/inspect.py) строка 184:
> `modal: Boolean(el.closest("[role='dialog'], dialog, [aria-modal='true']"))`.
> Достаточно фильтровать по этому флагу.

---

## Изменение 4 — Click с href-fallback

**Файл:** [`src/thirdhand/browser_core/tools.py`](../src/thirdhand/browser_core/tools.py)  
**Проблема:** `click(element_id=...)` на ссылку `<a href="...">` через Playwright иногда
открывает новую вкладку или падает с timeout если элемент перекрыт оверлеем.
Надёжнее использовать `goto_url`.

### Что добавить в начало функции `click` (после строки 113, до первого `session.click`)

```python
# Добавить перед первым вызовом session.click:

# Если элемент — это ссылка с абсолютным href, предпочесть goto_url
if element_id.strip():
    try:
        href = await session.page.evaluate(
            """(id) => {
                const el = document.querySelector(`[data-thirdhand-id="${id}"]`);
                if (!el) return null;
                const tag = el.tagName?.toLowerCase();
                if (tag === 'a') {
                    const href = el.href || el.getAttribute('href') || '';
                    if (href.startsWith('http')) return href;
                }
                return null;
            }""",
            element_id.strip(),
        )
        if href:
            logger.info(
                "browser_core_click_href_navigation",
                element_id=element_id.strip(),
                href=href[:200],
            )
            return await session.goto_url(href)
    except Exception:
        pass  # fallback к обычному click
```

---

## Изменение 5 — Инструмент `extract_page_items`

**Файл:** [`src/thirdhand/browser_core/tools.py`](../src/thirdhand/browser_core/tools.py)  
**Проблема:** Для листингов (вакансии, товары, статьи) LLM вынужден разбирать JSON из
177 элементов чтобы найти карточки. Нужен инструмент, который сразу возвращает
структурированный список `[{title, href, title_element_id, action_element_id}]`.

### Что добавить в `build_browser_core_tools` (новый инструмент)

**1. Новая Pydantic-схема** (добавить рядом с `ClickArgs`):

```python
class ExtractPageItemsArgs(BaseModel):
    max_items: int = Field(
        default=20,
        description="Maximum number of items to extract.",
    )
```

**2. Новая async-функция** (добавить рядом с `use_visual_assist`):

```python
async def extract_page_items(max_items: int = 20) -> str:
    """Извлекает структурированный список карточек/строк со страницы.

    Использует набор универсальных CSS-паттернов для обнаружения
    повторяющихся блоков (article, li.*, [data-qa], tr и т.д.).
    Возвращает JSON-список с полями title, href, title_element_id,
    action_element_id.  Работает на любом сайте — без хардкода.
    """
    _EXTRACT_JS = """
    (maxItems) => {
      const clean = v => (v || "").replace(/\\s+/g, " ").trim();
      const isVisible = el => {
        const s = window.getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return s.visibility !== "hidden" && s.display !== "none"
               && r.width > 0 && r.height > 0;
      };
      const ensureId = el => {
        if (!el.dataset.thirdhandId)
          el.dataset.thirdhandId = "th-" + Math.random().toString(36).slice(2,10);
        return el.dataset.thirdhandId;
      };

      // Универсальные селекторы для карточек в порядке приоритета
      const CARD_SELECTORS = [
        "article",
        "[role='article']",
        "[role='listitem']",
        "li.vacancy, li.job, li.item, li.product, li.card",
        "[data-qa*='vacancy'], [data-qa*='item'], [data-qa*='card']",
        ".card, .item, .vacancy",
        "tr[data-id], tr[data-item]",
      ];

      // Кнопки/ссылки отклика внутри карточки
      const ACTION_RE = /apply|respond|отклик|купить|buy|add.to.cart|записаться/i;

      let cardEls = [];
      for (const sel of CARD_SELECTORS) {
        try {
          const found = [...document.querySelectorAll(sel)].filter(isVisible);
          if (found.length >= 2) { cardEls = found; break; }
        } catch(e) {}
      }

      const items = [];
      for (const card of cardEls.slice(0, maxItems)) {
        // Ищем заголовок-ссылку
        const titleEl = card.querySelector("h1 a, h2 a, h3 a, h4 a")
                     || card.querySelector("a[data-qa*='title'], a.title, a.name")
                     || card.querySelector("a[href]");
        // Ищем кнопку действия
        const actionEl = [...card.querySelectorAll("button, a[href]")]
          .find(el => ACTION_RE.test(el.innerText || el.getAttribute("aria-label") || ""));

        const title = titleEl ? clean(titleEl.innerText || titleEl.getAttribute("aria-label") || "") : "";
        const href  = titleEl ? (titleEl.href || titleEl.getAttribute("href") || "") : "";

        items.push({
          title: title.slice(0, 120),
          href:  href.slice(0, 300),
          title_element_id:  titleEl  ? ensureId(titleEl)  : null,
          action_element_id: actionEl ? ensureId(actionEl) : null,
        });
      }
      return items;
    }
    """
    try:
        items = await session.page.evaluate(_EXTRACT_JS, max_items)
        return json.dumps(items, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"ERROR: extract_page_items failed: {exc}"
```

**3. Регистрация инструмента** (добавить в список `tools` рядом с остальными):

```python
StructuredTool.from_function(
    coroutine=extract_page_items,
    name="extract_page_items",
    args_schema=ExtractPageItemsArgs,
    description=(
        "Extract a structured list of cards/rows from the current page "
        "(vacancies, products, search results, table rows). "
        "Returns [{title, href, title_element_id, action_element_id}]. "
        "Use this BEFORE clicking individual items in a listing."
    ),
),
```

---

## Изменение 6 — Modal-open в детекторе прогресса

**Файл:** [`src/thirdhand/browser_core/tracking.py`](../src/thirdhand/browser_core/tracking.py)  
**Проблема:** `structural_signature` в `cycle_detector.py` хэширует `actionable_count`,
`headings`, `dialogs`, `text_hash`. Открытие overlay-панели на SPA (HH.ru side panel)
не меняет ни один из них → `progress_changed=False` хотя реально что-то изменилось.

### Что изменить в `cycle_detector.py` (метод `structural_signature`)

```python
# Найти метод structural_signature в cycle_detector.py и добавить modal_open:

def structural_signature(self, snapshot: dict[str, Any]) -> str:
    dialogs = snapshot.get("dialogs") or []
    return json.dumps(
        {
            "actionable_count": snapshot.get("metadata", {}).get("actionable_count", 0),
            "dialogs": dialogs[:3],
            "fillable_count": snapshot.get("metadata", {}).get("fillable_count", 0),
            "headings": (snapshot.get("headings") or [])[:4],
            "text_hash": hash(str(snapshot.get("text", ""))[:500]),
            # НОВОЕ — фиксирует открытие/закрытие modal/overlay
            "modal_open": len(dialogs) > 0,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
```

---

## Изменение 7 — System prompt: инструкция по `extract_page_items`

**Файл:** [`src/thirdhand/browser_core/prompts.py`](../src/thirdhand/browser_core/prompts.py)  
**Проблема:** LLM не знает о новом инструменте и продолжит угадывать текст карточек.

### Что добавить в `build_browser_core_system_prompt` (в блок "Tool usage rules")

```python
# Добавить в строку с правилами инструментов:
"- On listing pages (vacancies, products, search results, news feeds): "
"  FIRST call extract_page_items to get [{title, href, title_element_id, action_element_id}]. "
"  THEN use title_element_id or action_element_id from that list to click — never guess text.\n"
"- If title_element_id from extract_page_items is a link (has href), "
"  use goto_url(href) instead of click — more reliable for navigating to detail pages.\n"
```

---

## Порядок внедрения (приоритет)

| # | Изменение | Файл | Приоритет | Риск |
|---|-----------|------|-----------|------|
| 1 | Sliding window `_trim_messages` | `agent_loop.py` | 🔴 критично | низкий |
| 2 | Compact auto-snapshot | `inspect.py` + `agent_loop.py` | 🔴 критично | низкий |
| 3 | Modal-scoped `type_text` | `tools.py` | 🟠 высокий | низкий |
| 4 | `extract_page_items` tool | `tools.py` | 🟠 высокий | низкий |
| 5 | Click href-fallback | `tools.py` | 🟡 средний | низкий |
| 6 | `modal_open` в сигнатуре | `cycle_detector.py` | 🟡 средний | низкий |
| 7 | System prompt update | `prompts.py` | 🟡 средний | низкий |

---

## Что НЕ трогаем

- `session.py` — интерфейс Playwright остаётся как есть
- `cycle_detector.py` — кроме метода `structural_signature`
- `agent_loop.py` — логика finish_task / ask_user / stuck-interceptor  
- `sub_intent.py`, `goal_context.py`, `page_classifier.py` — не затронуты
- Миграции, модели БД, Telegram-бот

---

## Тесты которые нужно проверить после изменений

```bash
# Запустить все тесты одной командой
poetry run pytest tests/ -x -q
```

Особое внимание:
- [`tests/test_browser_agent.py`](../tests/test_browser_agent.py) — основной агент
- [`tests/test_auto_element_resolver.py`](../tests/test_auto_element_resolver.py) — авто-ресолвер
- [`tests/test_cycle_detector.py`](../tests/test_cycle_detector.py) — детектор цикла
- [`tests/test_stuck_interceptor.py`](../tests/test_stuck_interceptor.py) — stuck-логика
