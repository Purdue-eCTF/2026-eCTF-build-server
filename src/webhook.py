import traceback
import zmq

import requests

from colors import red
from config import DEBUG, ZMQ_PORT
from jobs import Job

context = zmq.Context()
socket = context.socket(zmq.PUB)
socket.bind(f"tcp://*:{ZMQ_PORT}")
socket.send_string("GOOOOOOOOO")
active_status: Job | None = None


def push_webhook(update_type: str = "QUEUE", update_state: Job | None = None):
    print("[ZMQ] Publishing Message")
    from builder import BUILD_QUEUE, active_build  # noqa: PLC0415

    if DEBUG:  # disable webhook while debugging
        return

    if update_state is not None:
        global active_status
        active_status = update_state
    try:
        # An abomination :prayer_hands:
        socket.send_string(
            '"update": {'
            f'"type": {update_type},'
            f'"state": {update_state.to_json() if update_state else None},'
            "},"
            f'"status": {active_status.status if active_status else None},'
            '"build": {'
            f'"active": {active_build.to_json() if active_build else None},'
            '"queue": [' + action.to_json_string()
            for action in list(BUILD_QUEUE.queue) + "]," + "}," + "}"
        )
    except requests.RequestException as e:
        print(red("[ZMQ] Could not publish to zmq"))
        traceback.print_exc()
