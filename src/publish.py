import zmq
from msgspec import json

from config import DEBUG, STATUS_PORT, LOG_PORT
from jobs import Job

context = zmq.Context.instance()
status_pub = context.socket(zmq.PUB)
status_pub.bind(f"tcp://*:{STATUS_PORT}")
active_status: Job | None = None

log_pub = context.socket(zmq.PUB)
log_pub.bind(f"tcp://*:{LOG_PORT}")


def publish_logs(run_id: str, msg: str):
    log_pub.send(f"{run_id}-build {msg}".encode())


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
