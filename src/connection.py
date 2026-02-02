import re
import socket
import sys
import time
import traceback
from urllib.parse import urlparse

from builder import add_to_build_queue
from colors import blue
from config import AUTH_TOKEN, PORT

# from distribution import AttackingJob, AttackScriptJob, UpdateCIJob, add_to_dist_queue
from jobs import ActionResult, Commit, ActionStatus
from publish import publish_status


# https://stackoverflow.com/a/52455972
def is_url(url):
    try:
        result = urlparse(url)
        return result.scheme.startswith("http") and result.netloc
    except ValueError:
        return False


def serve():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("0.0.0.0", PORT))  # noqa: S104
    server.listen()

    print(blue(f"[CONN] Listening on port {PORT}..."))
    sys.stdout.flush()

    while True:
        try:
            conn, addr = server.accept()
            conn.settimeout(10)
            print(f"[CONN] New connection from {addr}")

            try:
                token = conn.recv(1024).decode()
                if token != AUTH_TOKEN:
                    print("[CONN] Invalid connection, wrong token")
                    conn.close()
                    continue

                conn.sendall(b"[CONN] Building design\n")
                hash, author, message, run_id = (
                    conn.recv(1024).decode("utf-8").split(chr(0x1B))
                )
                print(f"[CONN] New build request for commit {hash}...")

                if len(hash) > 40 or len(hash) < 7 or re.search(r"[^0-9a-f]", hash):
                    print(f"[CONN] Invalid hash {hash}")
                    conn.sendall(f"[CONN] Invalid hash {hash}\n".encode())
                    conn.close()
                    continue

                print(f"[CONN] Queuing build for commit {hash}...")

                req = ActionResult(
                    conn,
                    ActionStatus.BUILD_PENDING,
                    time.time(),
                    Commit(hash, author, message, run_id),
                )
                add_to_build_queue(req)
                publish_status()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                conn.close()
                continue
        except KeyboardInterrupt:
            server.shutdown(socket.SHUT_RDWR)
            server.close()
            break
