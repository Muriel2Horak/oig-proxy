FROM python:3.11-slim

WORKDIR /app

# Závislosti
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Kód proxy a výchozí mapování
COPY main.py /app/main.py
COPY sensor_map.json /app/sensor_map.json

CMD ["python", "-u", "main.py"]
