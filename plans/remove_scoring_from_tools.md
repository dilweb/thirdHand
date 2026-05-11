# Редизайн click и type_text: только inspect_page, без скоринга

## Текущая проблема

В [`tools.py`](src/thirdhand/browser_core/tools.py) есть две функции с хардкод-скорингом:

### `_match_actionable()` — скоринг для click
```python
if exact_match:  score += 10
elif starts_with: score += 8
elif needle_in_text: score += 5
elif needle_in_label: score += 3
if short_text: score += 1
```

### `_best_match()` — скоринг для type_text
```python
if exact_label:   score += 10
elif partial:     score += 5
if exact_placeholder: score += 10
elif partial:     score += 5
if empty_field:   score += 2
```

Это **не масштабируется**, потому что:
- Веса взяты из головы (почему +10, а не +8?)
- Обрезка до 80 символов — хардкод
- Предпочтение коротких текстов — хардкод под hh.ru
- На другом сайте может понадобиться другая логика

## Новая архитектура

```mermaid
flowchart TD
    subgraph click
        A[click element_id?] -->|Да| B[session.click element_id]
        A -->|Нет| C[click_by_text text]
        C -->|Ошибка| D[inspect_page]
        D --> E[substring search по actionable]
        E -->|Найден| F[click по element_id]
        E -->|Не найден| G[Вернуть ошибку\nсо списком кликабельных]
    end
    
    subgraph type_text
        H[type_text element_id?] -->|Да| I[session.type_text]
        H -->|Нет| J[type_by_label или placeholder]
        J -->|Ошибка| K[inspect_page]
        K --> L[substring search по fillable]
        L -->|Найден| M[type_text по element_id]
        L -->|Не найден| N[Вернуть ошибку\nсо списком полей]
    end
```

### Принцип: никакого скоринга

Единственная операция — **проверка вхождения подстроки** (substring):

```python
def _find_by_substring(elements: list[dict], field: str, needle: str) -> dict | None:
    """Найти элемент, у которого поле field содержит needle.
    
    Никаких весов, никакого скоринга.
    Только in (подстрока).
    """
    if not needle:
        return elements[0] if elements else None
    needle_lower = needle.lower()
    for el in elements:
        value = (el.get(field) or "").lower()
        if needle_lower in value:
            return el
    return None
```

### Почему substring работает

В ``inspect_page`` у каждого элемента есть поля:
- `text` — видимый текст элемента
- `label` — aria-label или label
- `placeholder` — placeholder для input

Если агент передаёт `click(text='Откликнуться')`, substring найдёт элемент с text, содержащим "Откликнуться" — это и есть кнопка.

Если агент передаёт `click(text='Senior / Lead Full-Stack (200 символов...)')`, substring найдёт элемент с text, содержащим "Senior / Lead Full-Stack" — это вакансия. Тоже сработает.

### Что удаляем

| Функция | Файл | Действие |
|---------|------|----------|
| `_match_actionable()` | `tools.py:458` | Удалить |
| `_best_match()` | `tools.py:503` | Удалить |
| `_find_by_substring()` | `tools.py:458` | Добавить (замена) |
| `TestMatchActionable` | `tests/test_auto_element_resolver.py` | Заменить на `TestFindBySubstring` |

### Новая логика click

```python
async def click(element_id="", text="", exact=False):
    last_error = None
    
    # 1. element_id — приоритет
    if element_id:
        try:
            return await session.click(element_id)
        except Exception as exc:
            last_error = exc
            # fallback to text
            if text:
                try:
                    return await click_by_text(text, exact)
                except Exception:
                    pass
    
    # 2. text — если нет element_id
    if text and not element_id:
        try:
            return await click_by_text(text, exact)
        except Exception as exc:
            last_error = exc
    
    # 3. AUTO-DISCOVERY через inspect_page (без скоринга)
    snapshot = await session.inspect_page()
    data = json.loads(snapshot)
    candidates = data.get("actionable", [])
    
    # Простой substring поиск
    target = _find_by_substring(candidates, "text", text)
    if not target:
        target = _find_by_substring(candidates, "label", text)
    
    if target and target.get("id"):
        return await session.click(target["id"])
    
    # Ничего не нашли — отдаём ошибку с подсказкой
    hints = data.get("clickable_hints", [])[:5]
    raise ValueError(
        f"Cannot find element to click. Text={text[:100]!r}. "
        f"Available: {hints}"
    )
```

### Новая логика type_text

Аналогично — убираем `_best_match`, заменяем на `_find_by_substring`:

```python
# После element_id, label, placeholder — auto-discovery
snapshot = await session.inspect_page()
data = json.loads(snapshot)
candidates = data.get("fillable", [])

target = _find_by_substring(candidates, "label", label)
if not target:
    target = _find_by_substring(candidates, "placeholder", placeholder)

if target and target.get("id"):
    return await session.type_text(target["id"], text, submit=submit)
```

### Тесты

```python
def test_find_by_substring_exact():
    els = [{"id": "th-1", "text": "Откликнуться"}, {"id": "th-2", "text": "Найти"}]
    assert _find_by_substring(els, "text", "Откликнуться")["id"] == "th-1"

def test_find_by_substring_partial():
    els = [{"id": "th-1", "text": "Откликнуться без сопроводительного"}]
    assert _find_by_substring(els, "text", "Откликнуться")["id"] == "th-1"

def test_find_by_substring_no_match():
    els = [{"id": "th-1", "text": "Откликнуться"}]
    assert _find_by_substring(els, "text", "Найти") is None

def test_find_by_substring_empty_needle():
    els = [{"id": "th-1", "text": "A"}]
    assert _find_by_substring(els, "text", "")["id"] == "th-1"

def test_find_by_substring_empty_list():
    assert _find_by_substring([], "text", "A") is None
```

### Что остаётся

| Компонент | Статус |
|-----------|--------|
| `CycleDetector` | ✅ Без изменений |
| `PageClassifier` | ✅ Без изменений |
| `BrowserTrackingState` | ✅ Без изменений |
| `Stuck interceptor` | ✅ Без изменений |
| `AutoElementResolver` for type_text | 🔄 Переписать без скоринга |
| `AutoDiscovery` for click | 🔄 Переписать без скоринга |
| `_best_match` | ❌ Удалить |
| `_match_actionable` | ❌ Удалить |
| `_find_by_substring` | ✨ Добавить |
