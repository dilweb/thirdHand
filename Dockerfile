FROM python:3.12-slim

WORKDIR /app

# Install poetry
RUN pip install poetry==2.0.0

# Copy project files
COPY pyproject.toml poetry.lock README.md ./
COPY src/ ./src/

# Install dependencies (skip current project install to avoid README issues)
RUN poetry config virtualenvs.create false && poetry install --no-interaction --no-ansi --no-root

# Default command (overridden in docker-compose)
CMD ["python", "-m", "src.thirdhand.bot.main"]
