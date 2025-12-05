ARG BUILD_FROM
FROM ${BUILD_FROM}

WORKDIR /app

# Základní balíčky: python3, pip, dnsmasq
RUN apk add --no-cache python3 py3-pip dnsmasq

# Závislosti Pythonu
COPY requirements.txt /app/requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /app/requirements.txt

# Kód proxy a výchozí mapování
COPY main.py /app/main.py
COPY sensor_map.json /app/sensor_map.json

COPY run /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
