FROM python:3.12-slim
WORKDIR /bot
COPY requirements.txt /bot/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && pip install --no-cache-dir -r /bot/requirements.txt && python -m playwright install-deps chromium
RUN useradd --create-home bot && mkdir -p /data && chown bot:bot /data /bot
COPY --chown=bot:bot app.py cardmarket_reader.py cloakbrowser_reader.py cloakbrowser_worker.py server_start.py /bot/
USER bot
ENV PYTHONPATH=/bot \
    DB_PATH=/data/watchlist_v4.db \
    PYTHONUNBUFFERED=1
EXPOSE 10000
CMD ["python", "/bot/server_start.py"]
