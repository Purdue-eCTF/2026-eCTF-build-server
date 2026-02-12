import subprocess
import traceback
from dataclasses import dataclass
from enum import Enum
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


class ActionStatus(Enum):
    SUCCESS = "SUCCESS"
    TESTING = "TESTING"
    BUILDING = "BUILDING"
    BUILD_PENDING = "BUILD_PENDING"
    TEST_PENDING = "TEST_PENDING"
    BUILD_FAILED = "BUILD_FAILED"
    TEST_FAILED = "TEST_FAILED"


@dataclass
class Job:
    conn: socket
    status: ActionStatus
    start_time: float
    commit: Commit

    def to_json(self):
        return {
            "status": self.status,
            "start": round(self.start_time),
            "commit": self.commit.to_json(),
        }

    def on_success(self):
        self.update_status(ActionStatus.SUCCESS)
        self.conn.sendall(b"0\n")
        self.conn.close()

    def on_failure(self, status: ActionStatus):
        self.update_status(status)
        self.conn.sendall(b"1\n")
        self.conn.close()

    def on_error(self, e: Exception, msg: str, status: ActionStatus):
        self.log(red(msg))
        if isinstance(e, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
            self.log(e.stdout or b"")
            self.log(e.stderr or b"")
        self.log(red(traceback.format_exc()))
        self.on_failure(status)

    def update_status(self, status: ActionStatus):
        from publish import publish_status

        self.status = status
        publish_status(self)

    def log(self, msg: str | bytes):
        from publish import publish_logs

        if isinstance(msg, bytes):
            print(msg.decode())
        else:
            print(msg)

        publish_logs(self.commit.run_id, msg)


@dataclass
class BuildStatusUpdateReq:
    active: list[Job]
    queue: list[Job]
