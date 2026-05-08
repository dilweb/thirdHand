FROM python:3.12-slim

WORKDIR /app

# Install poetry
RUN pip install poetry==2.0.0

# Copy project files
COPY pyproject.toml poetry.lock README.md ./
COPY src/ ./src/

# Install dependencies (skip current project install to avoid README issues)
RUN poetry config virtualenvs.create false && poetry install --no-interaction --no-ansi --no-root

# Install Playwright Chromium for browser automation in container environments
RUN poetry run playwright install --with-deps chromium

# Default command (overridden in docker-compose)
CMD ["python", "-m", "src.thirdhand.bot.main"]
