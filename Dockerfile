FROM docker:27-dind

RUN apk update && apk upgrade && apk add github-cli python3 py3-pip openssh-client rsync curl

RUN pip install wheel --break-system-packages && pip install colorama requests pyzmq --break-system-packages
RUN curl -L -o /usr/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && \
	chmod +x /usr/bin/cloudflared

RUN mkdir -p /root/.ssh


COPY src /root/src
WORKDIR /root/src

RUN chmod 600 /root/src/id_ed25519* && git config --global advice.detachedHead false  

ENV DOCKER=1
ENV PYTHONUNBUFFERED=1
CMD [ "python3", "main.py" ]


EXPOSE 8124
EXPOSE 8131
