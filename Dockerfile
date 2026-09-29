FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN useradd --create-home --uid 10001 bot
COPY --chown=bot:bot recipe_bot /app/recipe_bot
USER bot
CMD ["python", "-m", "recipe_bot"]
