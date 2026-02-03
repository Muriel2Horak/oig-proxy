FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn pyjwt

COPY registration_api.py .

EXPOSE 8081

CMD ["uvicorn", "registration_api:app", "--host", "0.0.0.0", "--port", "8081"]
