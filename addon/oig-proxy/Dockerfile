FROM python:3.11-slim

WORKDIR /app

# Závislosti
COPY proxy/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Kód proxy a výchozí mapování
COPY proxy/main.py /app/main.py
COPY proxy/sensor_map.json /app/sensor_map.json

CMD ["python", "-u", "main.py"]
