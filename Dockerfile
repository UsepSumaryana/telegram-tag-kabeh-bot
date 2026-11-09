FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/members.db

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

RUN useradd --create-home --uid 10001 bot && mkdir -p /data && chown bot:bot /data
USER bot
VOLUME ["/data"]

CMD ["python", "bot.py"]
