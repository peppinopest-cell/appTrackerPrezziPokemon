FROM ghcr.io/flaresolverr/flaresolverr:v3.5.2
USER root
WORKDIR /bot
COPY requirements.txt /bot/requirements.txt
RUN python -m venv --system-site-packages /opt/bot-venv && /opt/bot-venv/bin/pip install --no-cache-dir -r /bot/requirements.txt
RUN mkdir -p /data && chown flaresolverr:flaresolverr /data /bot
COPY --chown=flaresolverr:flaresolverr app.py cardmarket_reader.py flaresolverr_reader.py server_start.py /bot/
USER flaresolverr
ENV FLARESOLVERR_URL=embedded \
    PYTHONPATH=/bot:/app \
    DB_PATH=/data/watchlist_v4.db \
    PYTHONUNBUFFERED=1
EXPOSE 10000
CMD ["/opt/bot-venv/bin/python", "/bot/server_start.py"]
