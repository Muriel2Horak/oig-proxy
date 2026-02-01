FROM python:3.11-slim

WORKDIR /app

COPY server.py .

# No dependencies needed - pure Python asyncio

ENV DATA_DIR=/data
ENV RETENTION_DAYS=7
ENV PORT=5710
ENV LOG_LEVEL=INFO

EXPOSE 5710

CMD ["python", "-u", "server.py"]
