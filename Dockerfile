# 91 Docker for easy move/deploy
FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends cron curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt flask gunicorn

COPY . .
RUN chmod +x scripts/*.sh scripts/*.py || true

ENV PYTHONUNBUFFERED=1
ENV LIGHT_MODE=0
ENV API_THRIFT=1

# default: run dashboard; override command for worker/cron
EXPOSE 8080
CMD ["gunicorn", "-b", "0.0.0.0:8080", "web.app:app"]
