# thirdHand

Telegram AI-ассистент с браузерным агентом, памятью и веб-поиском. Понимает естественный язык, выполняет многошаговые действия на любых сайтах, запоминает контекст и возвращается к прерванным задачам.

---

## Возможности

**Браузерный агент** — автономный Playwright-агент, который открывает сайты, заполняет формы, кликает, скроллит и принимает решения. Не требует хардкода под конкретный сайт — работает на любой DOM-структуре.

**Умный анализ задач** — определяет намерение пользователя: browser task, поиск в интернете, напоминание, вопрос к профилю. Маршрутизирует в нужный узел графа.

**Память и контекст** — хранит историю диалогов, профиль пользователя, напоминания. При возобновлении задачи восстанавливает контекст и браузерную сессию.

**Веб-поиск** — ищет информацию в интернете, если задача требует актуальных данных.

---

## Архитектура

```
Telegram Bot (Aiogram)
    |
    v
LangGraph State Graph
    |
    +-- Parse Intent Node (Gemini)
    +-- Router Node
    +-- Browser Node (Playwright + LLM loop)
    +-- Search Node
    +-- Reminder Node
    +-- Response Node
```

Каждый узел графа — дискретный, перезапускаемый шаг. Граф может приостановиться в любой точке, дождаться ответа пользователя и продолжить с того же места.

---

## Браузерный агент: как работает

Цикл observe-act-observe:

1. LLM получает снапшот страницы (заголовки, диалоги, кликабельные элементы, поля ввода)
2. Решает, какой инструмент вызвать: click, type_text, scroll, goto_url, extract_page_items
3. Выполняет действие в Playwright
4. Делает компактный inspect страницы (~2K токенов) для проверки результата
5. Если страница не меняется 2+ шага — срабатывает cycle detector
6. При зацикливании вызывает use_visual_assist — vision-модель описывает скриншот текстом
7. LLM читает описание и находит element_id в DOM через inspect_page
8. Если агент в тупике — спрашивает пользователя через ask_user

---

## Ключевые особенности

**Сайт-агностик** — ни одного хардкода под конкретный сайт. Селекторы карточек используют структурный анализ DOM (группировка родительских элементов ссылок по тегам), а не CSS-классы.

**Modal-scoped click** — когда открыта модалка, click автоматически ищет кнопки внутри неё, игнорируя фоновые элементы, которые блокируются оверлеем.

**Pass-through vision** — gpt-4o-mini смотрит скриншот и описывает текстом что видит. Основная LLM сама находит element_id в DOM. Никакого угадывания идентификаторов.

**Parked-сессии** — браузер не закрывается при ожидании ответа пользователя. Сессия живёт в памяти до 30 минут, cookies и localStorage сохраняются.

**Persistent browser profile** — Chromium profile хранится в Docker volume. Сессии, cookies, авторизация не сбрасываются между запусками.

**Cycle detector** — анализирует структурную сигнатуру страницы (без URL). Детектит зацикливание за 3 шага.

**Sliding window history** — в контексте LLM остаётся только 8 последних шагов. Контроль токенов без потери важного контекста.

**Exponential backoff + jitter** — декоратор @retry_async на любую асинхронную функцию. 1 -> 2 -> 4 -> 8 секунд с рандомом.

---

## Стек

- Python 3.12
- LangGraph (граф состояний)
- LangChain (Runnable-цепи)
- Playwright (Chromium, persistent context)
- Aiogram 3 (Telegram Bot API)
- PostgreSQL + async SQLAlchemy
- Docker + docker-compose
- Celery (фоновые задачи)
- OpenRouter (LLM-прокси)
- Alembic (миграции)

**Модели:**
- deepseek/deepseek-v4-flash — основная LLM для browser-цикла
- google/gemini-2.5-flash-lite — анализ задач и намерений
- openai/gpt-4o-mini — vision-ассистент (скриншоты)

---

## Быстрый старт

```bash
git clone <repo>
cd thirdHand

# Настройка
cp .env.example .env
# Заполните BOT_TOKEN, OPENROUTER_API_KEY, DATABASE_URL

# Запуск
docker compose up --build
```

---

## Переменные окружения

| Переменная | Описание |
|-----------|----------|
| BOT_TOKEN | Токен Telegram бота |
| OPENROUTER_API_KEY | Ключ OpenRouter API |
| DATABASE_URL | PostgreSQL connection string |
| REDIS_URL | Redis connection string |
| BROWSER_PROFILE_DIR | Путь к persistent Chromium profile |
| BROWSER_HEADLESS | Режим headless (true/false) |
| BROWSER_MAX_STEPS | Максимум шагов browser-цикла |
| BROWSER_MODEL | Модель для browser-агента |
| PICTURE_RECOGNITION_MODEL | Модель для vision-ассистента |
| DEFAULT_MODEL | Модель по умолчанию |

---

## Тесты

```bash
docker compose exec app python -m pytest tests/ -q
# 150+ тестов, ~3 секунды
```

---

## Лицензия

WEBDIL
