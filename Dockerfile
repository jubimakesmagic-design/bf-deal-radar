FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY src ./src
COPY config.yaml ./

RUN pip install --no-cache-dir . && mkdir -p /app/data

# 기본 2분(120초) 간격으로 실시간 감시
CMD ["bf-radar", "watch", "--interval", "120"]
