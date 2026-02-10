import traceback
from dataclasses import dataclass
from socket import socket
from enum import Enum
import subprocess

from colors import red, blue


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
    socket_colors: bool

    def to_json(self):
        return {}

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


class ActionResult(Job):
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

    def log(self, msg: str):
        from publish import publish_logs

        print(msg)
        publish_logs(self.commit.run_id, msg)


@dataclass
class BuildStatusUpdateReq:
    active: list[ActionResult]
    queue: list[ActionResult]
