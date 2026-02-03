import zmq
from msgspec import json
from zmq.auth.thread import ThreadAuthenticator

from config import AUTH_TOKEN, DEBUG, LOG_PORT, STATUS_PORT
from jobs import Job

context = zmq.Context.instance()

auth = ThreadAuthenticator(context)
auth.start()
auth.configure_plain(domain="*", passwords={"user": AUTH_TOKEN})

status_pub = context.socket(zmq.PUB)
status_pub.setsockopt(zmq.PLAIN_SERVER, 1)
status_pub.bind(f"tcp://*:{STATUS_PORT}")
active_status: Job | None = None

log_pub = context.socket(zmq.PUB)
log_pub.setsockopt(zmq.PLAIN_SERVER, 1)
log_pub.bind(f"tcp://*:{LOG_PORT}")


def publish_logs(run_id: str, msg: str | bytes):
    if isinstance(msg, str):
        msg = msg.encode()
    log_pub.send_multipart([f"{run_id}-build".encode(), msg])


def publish_status(update_state: Job | None = None):
    from builder import BUILD_QUEUE

    if DEBUG:  # disable webhook while debugging
        return

    if update_state is not None:
        global active_status
        active_status = update_state
    status = {
        "active": [active_status.to_json()] if active_status is not None else [],
        "queue": [t.to_json() for t in list(BUILD_QUEUE.queue)],
    }
    message = json.encode(status)
    status_pub.send(message)
