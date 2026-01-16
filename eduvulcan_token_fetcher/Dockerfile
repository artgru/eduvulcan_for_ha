ARG BUILD_FROM
FROM $BUILD_FROM

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY app/requirements.txt /app/requirements.txt
RUN apk add --no-cache python3 py3-pip chromium nss freetype harfbuzz ca-certificates ttf-freefont \
    && pip3 install --no-cache-dir -r /app/requirements.txt \
    && python3 -m playwright install chromium \
    && rm -rf /root/.cache

COPY app /app
COPY run.sh /run.sh
RUN chmod +x /run.sh

ENTRYPOINT ["/run.sh"]
