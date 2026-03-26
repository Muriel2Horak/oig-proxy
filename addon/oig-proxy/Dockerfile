ARG BUILD_FROM
FROM ${BUILD_FROM}

WORKDIR /app

RUN apk add --no-cache python3 py3-pip dnsmasq tcpdump

COPY requirements.txt /app/requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /app/requirements.txt

COPY . /app/

COPY run /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
