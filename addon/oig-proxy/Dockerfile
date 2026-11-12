ARG BUILD_FROM=ghcr.io/home-assistant/base:3.23
FROM ${BUILD_FROM}

WORKDIR /app

# The add-on uses Bashio for configuration. Remove the inherited template tool.
RUN apk upgrade --no-cache \
    && apk add --no-cache python3 py3-pip dnsmasq tcpdump \
    && rm -f /usr/bin/tempio

COPY requirements.txt /app/requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /app/requirements.txt

COPY . /app/

COPY run /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
