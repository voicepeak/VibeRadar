FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    VIBERADAR_DATA_DIR=/var/lib/viberadar

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY web ./web
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 viberadar \
    && mkdir -p /var/lib/viberadar \
    && chown -R viberadar:viberadar /var/lib/viberadar
USER viberadar

EXPOSE 8000

# Web 服务（公开站 + 反馈 API）；调度器与 CLI 通过 compose/任务计划另行执行
CMD ["sh", "-c", "python -m uvicorn 'viberadar.web.app:create_app' --factory --host 0.0.0.0 --port 8000"]
