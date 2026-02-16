# 构建上下文建议为 protocol 目录： docker build -f web/Dockerfile .
FROM python:3.11-slim

WORKDIR /app
COPY web/ /app/web/
RUN pip install --no-cache-dir -r /app/web/backend/requirements.txt

ENV DATA_DIR=/data
VOLUME /data
WORKDIR /app/web/backend
EXPOSE 1989
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "1989"]
