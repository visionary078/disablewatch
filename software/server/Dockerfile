FROM python:3.12-slim

WORKDIR /app

COPY requirements-server.txt .
RUN pip install --no-cache-dir \
    --timeout 120 \
    --retries 10 \
    -i https://mirrors.cloud.tencent.com/pypi/simple \
    --trusted-host mirrors.cloud.tencent.com \
    -r requirements-server.txt

COPY server.py minicpmo_runner.py model_api.py prompts.py model_profiles.json ./
COPY services ./services
COPY static ./static

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8000"]
