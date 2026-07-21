FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN useradd --create-home --uid 10001 bot
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .
RUN mkdir -p /app/data /app/storage && chown -R bot:bot /app
USER bot
CMD ["python", "-m", "app.main"]
