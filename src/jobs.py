import re
import subprocess
import traceback
from dataclasses import dataclass
from socket import socket

from colors import red


@dataclass
class Commit:
    hash: str
    author: str
    message: str
    run_id: str

    def to_json(self):
        return {
            "hash": self.hash,
            "name": self.message,
            "author": self.author,
            "runId": self.run_id,
        }


@dataclass
class Job:
    conn: socket
    status: str
    start_time: float
    socket_colors: bool

    def to_json(self):
        return {}

    def log(self, msg: str):
        print(msg)
        if not self.socket_colors:
            msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)
        self.conn.sendall(msg.encode() + b"\n")

    def on_error(self, e: Exception, msg: str):
        self.log(red(msg))
        if isinstance(e, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
            self.conn.sendall(e.stdout or b"")
            self.conn.sendall(e.stderr or b"")
        self.log(red(traceback.format_exc()))
        self.conn.sendall(b"%*&1\n")
        self.conn.close()
        self.status = "FAILED"


class ActionUpdate(Job):
    commit: Commit

    def __init__(self, conn, status, start_time, commit):
        self.commit = commit
        super().__init__(
            conn=conn, status=status, start_time=start_time, socket_colors=True
        )

    def to_json(self):
        return {
            "status": self.status,
            "start": round(self.start_time),
            "commit": self.commit.to_json(),
        }
