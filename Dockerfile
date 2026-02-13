FROM docker:29-cli

RUN apk update && apk upgrade && apk add github-cli python3 py3-pip openssh-client rsync curl

RUN pip install wheel --break-system-packages && pip install uv colorama requests pyzmq msgspec --break-system-packages

COPY src /root/src
WORKDIR /root/src

RUN git config --global advice.detachedHead false

ENV PYTHONUNBUFFERED=1
CMD [ "python3", "main.py" ]

EXPOSE 8124
