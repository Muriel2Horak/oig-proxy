ARG BUILD_FROM
FROM ${BUILD_FROM}

WORKDIR /app

# Základní balíčky: python3, pip, dnsmasq
RUN apk add --no-cache python3 py3-pip dnsmasq

# Závislosti Pythonu
COPY requirements.txt /app/requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /app/requirements.txt

# Kód proxy a výchozí mapování - všechny moduly
COPY main.py /app/main.py
COPY config.py /app/config.py
COPY models.py /app/models.py
COPY utils.py /app/utils.py
COPY parser.py /app/parser.py
COPY proxy.py /app/proxy.py
COPY cloud_session.py /app/cloud_session.py
COPY cloud_manager.py /app/cloud_manager.py
COPY mqtt_publisher.py /app/mqtt_publisher.py
COPY sensor_map.json /app/sensor_map.json

COPY run /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
