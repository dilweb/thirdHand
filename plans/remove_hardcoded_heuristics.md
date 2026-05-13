# Удаление хардкода и эвристик из browser_core

## Принципы
- Никаких списков ключевых слов
- Никаких селекторов под конкретный сайт
- Никаких примеров на конкретном языке
- Только структурные свойства HTML и алгоритмические метрики

## Шаги

### Шаг 1: `cycle_detector.py` — удалить `hhtmFrom`

**Файл**: [`src/thirdhand/browser_core/cycle_detector.py`](../src/thirdhand/browser_core/cycle_detector.py:65)

**Было**: фильтрация конкретного query-параметра `hhtmFrom`

**Стало**: использовать только `pathname` без query-параметров.
Query-параметры не влияют на DOM-структуру; если они меняют контент,
это отразится на `headings`, `text_hash`, `element_counts`.

**Тесты**: обновить [`tests/test_cycle_detector.py`](../tests/test_cycle_detector.py) —
убрать тест, проверяющий разную сигнатуру для URL с разными query-параметрами.

### Шаг 2: `tools.py` — ACTION_RE → структурный поиск action-кнопки

**Файл**: [`src/thirdhand/browser_core/tools.py`](../src/thirdhand/browser_core/tools.py:429)

**Было**: регулярное выражение с ключевыми словами
`/apply|respond|отклик|купить|buy|add.to.cart|записаться|contact|hire/i`

**Стало**: структурные признаки HTML:
- `button[type='submit']`
- `input[type='submit']`
- `a[role='button']`
- `button:not([type='button'])` (по умолчанию type=submit)
- Fallback: первая кнопка или последняя ссылка в карточке

**Тесты**: обновить или добавить тест на `extract_page_items`
с мок-страницей, где action-кнопка определяется структурно.

### Шаг 3: `tools.py` — CSS fallback → удалить site-specific селекторы

**Файл**: [`src/thirdhand/browser_core/tools.py`](../src/thirdhand/browser_core/tools.py:469-492)

**Было**: блок `CARD_SELECTORS` с селекторами `data-qa`, `vacancy`, `card`

**Стало**: удалить весь блок. Если structural analysis дал < 2 карточек —
вернуть пустой массив. Вызывающий код при пустом результате использует
`use_visual_assist`.

### Шаг 4: `prompts.py` — удалить "Откликнуться"

**Файл**: [`src/thirdhand/browser_core/prompts.py`](../src/thirdhand/browser_core/prompts.py:158)

**Было**: пример с "Откликнуться" (hh.ru)

**Стало**: нейтральное описание без привязки к сайту или языку.

### Шаг 5: `page_classifier.py` — абсолютные пороги → относительные пропорции

**Файл**: [`src/thirdhand/browser_core/page_classifier.py`](../src/thirdhand/browser_core/page_classifier.py:49-64)

**Было**: жёсткие пороги `>= 4`, `>= 5`, `<= 3`, `<= 2`

**Стало**: относительные пропорции:
- `link_ratio > 0.6 and fillable_ratio < 0.3` → SEARCH_RESULTS
- `fillable_ratio > 0.5` → FORM_PAGE
- `total <= 8 and link_ratio < 0.4` → DETAIL_PAGE

**Тесты**: обновить [`tests/test_page_classifier.py`](../tests/test_page_classifier.py).

### Шаг 6: `tools.py` — `_find_by_substring` с ранжированием

**Файл**: [`src/thirdhand/browser_core/tools.py`](../src/thirdhand/browser_core/tools.py:745-765)

**Было**: первое совпадение по подстроке

**Стало**: ранжирование по длине совпадения (exact → longest substring).

### Шаг 7: `policy.py` — языковые подсказки → HTML-атрибуты

**Файл**: [`src/thirdhand/browser_core/policy.py`](../src/thirdhand/browser_core/policy.py:148-291)

**Было**: инструкции с текстом на русском/английском для поиска элементов

**Стало**: инструкции через HTML-стандарты и ARIA-атрибуты.

### Шаг 8: `recovery.py` + `agent_loop.py` — адаптивная эскалация

**Файлы**: [`src/thirdhand/browser_core/recovery.py`](../src/thirdhand/browser_core/recovery.py:133),
[`src/thirdhand/browser_core/agent_loop.py`](../src/thirdhand/browser_core/agent_loop.py:539)

**Было**: фиксированные пороги эскалации (2, 3, 4, 5)

**Стало**: динамические пороги = `max(2, min(8, estimated_steps // 10))`,
где `estimated_steps` приходит из planner'а.

## Порядок выполнения

1. Шаг 1 → Шаг 2 → Шаг 3 (критические, hh.ru-завязки)
2. Шаг 5 → Шаг 6 → Шаг 7 (эвристики, средний приоритет)
3. Шаг 4 → Шаг 8 (косметика + адаптация)
4. Прогнать все тесты: `poetry run pytest tests/ -x -v`
