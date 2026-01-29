import traceback
import zmq
import json

import requests

from colors import red
from config import DEBUG, STATUS_PORT, LOG_PORT
from jobs import Job, BuildStatusUpdateReq

context = zmq.Context.instance()
status_pub = context.socket(zmq.PUB)
status_pub.bind(f"tcp://*:{STATUS_PORT}")
active_status: Job | None = None


def push_webhook(update_type: str = "QUEUE", update_state: Job | None = None):
    from builder import BUILD_QUEUE, active_build  # noqa: PLC0415

    if DEBUG:  # disable webhook while debugging
        return

    if update_state is not None:
        global active_status
        active_status = update_state
    try:
        status = {
            "active": [active_status.to_json()] if active_status is not None else [],
            "queue": [t.to_json() for t in list(BUILD_QUEUE.queue)],
        }
        print(json.dumps(status))
    except requests.RequestException as e:
        traceback.print_exc()
