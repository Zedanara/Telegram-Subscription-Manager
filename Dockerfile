FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.12-slim AS final

RUN groupadd --system bot && useradd --system --gid bot --create-home bot

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --chown=bot:bot . .

USER bot

ENTRYPOINT ["python", "main.py"]
