FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git cron && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
# torch (via argostranslate/stanza) defaults to CUDA wheels (~GBs) — the VPS has no GPU
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir -r requirements.txt \
 && python -c "import argostranslate.package as p; p.update_package_index(); p.install_package_for_language_pair('en','ar')"
COPY . .
ENV PYTHONUNBUFFERED=1
CMD ["python", "scripts/vps_loop.py"]
