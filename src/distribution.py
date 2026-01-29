import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from socket import socket
from typing import Literal
from urllib.parse import urlparse

import requests

from colors import blue, red
from config import GITHUB_TOKEN, GITHUB_USERNAME, IPS
from jobs import Commit, Job
from webhook import push_webhook

distribution_queue: Queue["DistributionJob"] = Queue()
upload_status: dict[str, "UploadServerStatus"] = {}
server_queues: dict[str, Queue[str]] = {"TEST": Queue(), "ATTACK": Queue()}

OUT_PATH = "~/ectf2025/build_out/"
TEST_OUT_PATH = "~/ectf2025/test_out/"
CI_PATH = "~/ectf2025/CI/"
VENV = ". ~/ectf2025/.venv/bin/activate"


@dataclass
class DistributionJob(Job):
    name: str
    in_path: str
    queue_type: Literal["ATTACK", "TEST"]
    attack_board: bool
    commit: Commit | None = None

    def to_json(self):
        return {
            "result": self.status,
            "actionStart": round(self.start_time),
            "commit": self.commit and self.commit.to_json(),
        }

    def distribute(self, ip: str):
        self.status = "UPLOADING"
        self.start_time = time.time()
        push_webhook(self.queue_type, self)

        firmware_file = Path(self.in_path).name
        try:
            # upload to server
            self.log(blue(f"[DIST] Uploading {self.name} to {ip}"))
            try:
                self.upload(ip, [self.in_path], OUT_PATH)
            except subprocess.SubprocessError as e:
                if (
                    isinstance(e, subprocess.CalledProcessError)
                    and b"Connection closed by UNKNOWN port 65535" in e.stderr
                ):
                    self.log(f"[DIST] {ip} is disconnected, changing servers")

                    self.status = "PENDING"
                    push_webhook(self.queue_type, self)

                    upload_status[ip].connected = False
                    add_to_dist_queue(self)
                else:
                    self.on_error(e, f"[DIST] Failed to upload to {ip}")

                    self.status = "FAILED"
                    push_webhook(self.queue_type, self)
                return

            # flash binary
            self.log(blue("[DIST] Flashing binary"))
            try:
                output = subprocess.run(
                    [
                        "ssh",
                        "-F",
                        "ssh_config",
                        "-i",
                        "id_ed25519",
                        "-o",
                        "StrictHostKeyChecking=accept-new",
                        ip,
                        f"{VENV} || exit 1; {CI_PATH}/update {OUT_PATH}/{firmware_file} {
                            '1' if self.attack_board else ''
                        };",
                    ],
                    timeout=60 * 4,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.conn.sendall(output.stdout)
                self.conn.sendall(output.stderr)
            except subprocess.SubprocessError as e:
                self.on_error(e, f"[DIST] Failed to flash on {ip}")

                self.status = "FAILED"
                push_webhook(self.queue_type, self)
                return

            self.log(blue("[DIST] Flashed!"))
            self.post_upload(ip)
        except (BrokenPipeError, TimeoutError):
            print(red("[DIST] Client disconnected"))
        finally:
            if upload_status[ip].connected:
                # if not changing servers
                server_queues[self.queue_type].put(ip)
                self.cleanup()
            distribution_queue.task_done()

    def upload(self, ip: str, files: list[Path | str], out_path: str):
        # auto-retry to work around PAL
        max_retries = 3
        for i in range(max_retries):
            try:
                subprocess.run(
                    [
                        "rsync",
                        (
                            "--rsh=ssh -F ssh_config -i id_ed25519 -o StrictHostKeyChecking=accept-new"
                            " -o ServerAliveInterval=5 -o ServerAliveCountMax=1"
                        ),
                        "-av",
                        "--partial",
                        "--progress",
                        "--delete",
                        *files,
                        f"{ip}:{out_path}",
                    ],
                    timeout=30,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except subprocess.CalledProcessError as e:
                if i == max_retries - 1:
                    raise
                if b"write error: Broken pipe" not in e.stderr:
                    raise

    def cleanup(self):
        pass

    def post_upload(self, ip: str):
        pass


class TestingJob(DistributionJob):
    def __init__(
        self,
        conn: socket,
        status: str,
        start_time: float,
        build_folder: str,
        commit: Commit,
    ):
        self.build_folder = build_folder
        super().__init__(
            conn=conn,
            status=status,
            start_time=start_time,
            name=commit.hash,
            in_path=build_folder + "/build_out/max78000.bin",
            queue_type="TEST",
            commit=commit,
            socket_colors=True,
            attack_board=False,
        )

    def post_upload(self, ip: str):
        self.status = "TESTING"
        push_webhook("TEST", self)

        # run tests
        self.log(blue(f"[TEST] Running tests for {self.name}"))

        # upload test data to server
        self.log(blue(f"[TEST] Uploading test data to {ip}"))
        try:
            self.upload(
                ip,
                [
                    f"{self.build_folder}/design",
                    f"{self.build_folder}/secrets/global.secrets",
                ],
                TEST_OUT_PATH,
            )
        except subprocess.SubprocessError as e:
            self.on_error(e, f"[TEST] Failed to upload to {ip}")

            self.status = "FAILED"
            push_webhook("TEST", self)
            return

        self.log(blue(f"[TEST] Running tests on {ip}"))

        try:
            output = subprocess.run(
                [
                    "ssh",
                    "-F",
                    "ssh_config",
                    "-i",
                    "id_ed25519",
                    "-o",
                    "StrictHostKeyChecking=accept-new",
                    ip,
                    f"{VENV} || exit 1; {CI_PATH}/run_build_tests.sh;",
                ],
                timeout=60 * 10,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.conn.sendall(output.stdout)
            self.conn.sendall(output.stderr)

        except subprocess.SubprocessError as e:
            self.on_error(e, f"[TEST] Tests failed for {self.name}")

            self.status = "FAILED"
            push_webhook("TEST", self)
            return

        self.log(blue(f"[TEST] Tests OK for {self.name}"))
        self.conn.sendall(b"%*&0\n")
        self.conn.close()
        self.status = "SUCCESS"
        push_webhook("TEST", self)

    def cleanup(self):
        shutil.rmtree(self.build_folder)


class AttackingJob(DistributionJob):
    def __init__(
        self,
        conn: socket,
        status: str,
        start_time: float,
        team: str,
    ):
        self.team = team
        self.target_folder = Path("~/mounts/targets/").expanduser() / team
        super().__init__(
            conn=conn,
            status=status,
            start_time=start_time,
            name=team,
            in_path=str(self.target_folder / "attacker.prot"),
            queue_type="ATTACK",
            commit=Commit("", team, "Automated attack tests", ""),
            socket_colors=False,  # scrape-bot specific
            attack_board=True,
        )

    def post_upload(self, ip: str):
        self.status = "ATTACKING"
        push_webhook("ATTACK", self)

        # upload attack data to server
        self.log(blue(f"[ATTACK] Uploading attack data to {ip}"))

        try:
            target_files = [
                p
                for p in self.target_folder.iterdir()
                if p.is_file() and p.suffix != ".prot"
            ]
            self.upload(
                ip,
                [
                    *target_files,
                    self.target_folder / "design/design",
                ],
                TEST_OUT_PATH,
            )
        except subprocess.SubprocessError as e:
            self.on_error(e, f"[ATTACK] Failed to upload to {ip}")

            self.status = "FAILED"
            push_webhook("ATTACK", self)
            return

        # run attacks
        self.log(blue(f"[ATTACK] Running attacks for {self.name} on {ip}"))

        try:
            output = subprocess.run(
                [
                    "ssh",
                    "-F",
                    "ssh_config",
                    "-i",
                    "id_ed25519",
                    "-o",
                    "StrictHostKeyChecking=accept-new",
                    ip,
                    f"{VENV} || exit 1; {CI_PATH}/run_attack_tests.sh 1;",
                ],
                timeout=60 * 10,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.conn.sendall(output.stdout)
            self.conn.sendall(output.stderr)
        except subprocess.SubprocessError as e:
            self.on_error(e, f"[ATTACK] Attacks failed for {self.name}")

            self.status = "FAILED"
            push_webhook("ATTACK", self)
            return

        self.log(blue(f"[ATTACK] ATTACK OK for {self.name}"))
        self.conn.sendall(b"%*&0\n")
        self.conn.close()
        self.status = "SUCCESS"
        push_webhook("ATTACK", self)


class AttackScriptJob(DistributionJob):
    def __init__(
        self,
        conn: socket,
        status: str,
        start_time: float,
        team: str,
        script_url: str,
    ):
        self.team = team
        self.target_folder = Path("~/mounts/targets/").expanduser() / team
        self.script_url = script_url
        super().__init__(
            conn=conn,
            status=status,
            start_time=start_time,
            name=team,
            in_path=str(self.target_folder / "attacker.prot"),
            queue_type="ATTACK",
            commit=Commit("", team, "Manual attack script", ""),
            socket_colors=False,  # scrape-bot specific
            attack_board=True,
        )

    def post_upload(self, ip: str):
        self.status = "ATTACKING"
        push_webhook("ATTACK", self)

        # download attack script
        self.log(blue("[ATTACK] Downloading attack script"))
        try:
            resp = requests.get(self.script_url, timeout=5)
            if not resp.ok:
                self.log(
                    red(
                        f"[ATTACK] Fetching {self.script_url} returned {resp.status_code}"
                    )
                )
                self.status = "FAILED"
                push_webhook("ATTACK", self)
                return

            script_filename = urlparse(self.script_url).path.split("/")[-1]

            if "/" in script_filename:
                self.log(red(f"[ATTACK] Invalid filename {script_filename}"))
                self.status = "FAILED"
                push_webhook("ATTACK", self)
                return
        except requests.Timeout as e:
            self.on_error(e, f"[ATTACK] Failed to upload to {ip}")

            self.status = "FAILED"
            push_webhook("ATTACK", self)
            return

        # upload attack data to server
        self.log(blue(f"[ATTACK] Uploading attack data to {ip}"))

        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                script_path = Path(temp_dir) / script_filename
                with script_path.open("w") as f:
                    f.write(resp.text)
                target_files = [
                    p
                    for p in self.target_folder.iterdir()
                    if p.is_file() and p.suffix != ".prot"
                ]
                self.upload(
                    ip,
                    [
                        *target_files,
                        self.target_folder / "design/design",
                        script_path,
                    ],
                    TEST_OUT_PATH,
                )
        except subprocess.SubprocessError as e:
            self.on_error(e, f"[ATTACK] Failed to upload to {ip}")

            self.status = "FAILED"
            push_webhook("ATTACK", self)
            return

        # run attack
        self.log(blue(f"[ATTACK] Running attack script for {self.name} on {ip}"))

        try:
            remote_script_path = Path(TEST_OUT_PATH) / script_filename
            # ensure that ~ in TEST_OUT_PATH is still expanded
            quoted_script_path = f"{TEST_OUT_PATH}/{shlex.quote(script_filename)}"
            command = (
                f"python3 {quoted_script_path}"
                if remote_script_path.suffix == ".py"
                else f"chmod +x {quoted_script_path}; {quoted_script_path}"
            )
            output = subprocess.run(
                [
                    "ssh",
                    "-F",
                    "ssh_config",
                    "-i",
                    "id_ed25519",
                    "-o",
                    "StrictHostKeyChecking=accept-new",
                    ip,
                    (
                        f"{VENV} || exit 1;"
                        f"cd {TEST_OUT_PATH}; . {CI_PATH}/setup_attacks.sh;"
                        f"echo Running attack; {command} 2>&1"
                    ),
                ],
                timeout=60 * 10,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.conn.sendall(output.stdout)
            self.conn.sendall(output.stderr)
        except subprocess.SubprocessError as e:
            self.on_error(e, f"[ATTACK] Attacks failed for {self.name}")

            self.status = "FAILED"
            push_webhook("ATTACK", self)
            return

        self.log(blue(f"[ATTACK] ATTACK OK for {self.name}"))
        self.conn.sendall(b"%*&0\n")
        self.conn.close()
        self.status = "SUCCESS"
        push_webhook("ATTACK", self)


class UpdateCIJob(Job):
    def __init__(self, conn, status, start_time):
        super().__init__(conn, status, start_time, socket_colors=True)

    def update_ci(self):
        from builder import BUILD_QUEUE  # noqa: PLC0415

        BUILD_QUEUE.join()
        distribution_queue.join()

        for ip, status in upload_status.items():
            if status.connected:
                self.log(blue(f"[UPDATE] Updating CI on {ip}"))
                try:
                    output = subprocess.run(
                        [
                            "ssh",
                            "-F",
                            "ssh_config",
                            "-i",
                            "id_ed25519",
                            "-o",
                            "StrictHostKeyChecking=accept-new",
                            ip,
                            f"cd {CI_PATH} && "
                            f"GITHUB_USERNAME={GITHUB_USERNAME} GITHUB_TOKEN={
                                GITHUB_TOKEN
                            } "
                            f"GIT_ASKPASS={CI_PATH}/git-askpass.sh "
                            "git pull --recurse-submodules --ff-only origin main",
                        ],
                        timeout=60 * 2,
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    self.conn.sendall(output.stdout)
                    self.conn.sendall(output.stderr)
                except subprocess.SubprocessError as e:
                    self.on_error(e, f"[UPDATE] Failed to update CI on {ip}")

                    self.status = "FAILED"
                    return
            else:
                self.log(
                    red(f"[UPDATE] Skipping CI update on {ip} because it is disconnected")
                )

        self.log(blue("[UPDATE] CI updates complete"))
        self.conn.sendall(b"%*&0\n")
        self.conn.close()
        self.status = "SUCCESS"


@dataclass
class UploadServerStatus:
    job: DistributionJob | None = None
    connected: bool = True

    def is_avail(self):
        return self.connected and (not self.job or self.job.status != "TESTING")


def distribution_loop():
    while True:
        req = distribution_queue.get()
        avail_ip = server_queues[req.queue_type].get()
        req.status = "TESTING"
        req.start_time = time.time()
        upload_status[avail_ip].job = req
        push_webhook()
        threading.Thread(target=req.distribute, args=(avail_ip,), daemon=True).start()


def add_to_dist_queue(job: DistributionJob):
    distribution_queue.put(job)


def init_distribution_queue():
    # setup ssh
    with open("ssh_config", "w", encoding="utf-8") as f:
        for ip, queue_type in IPS:
            f.write(
                f"Host {
                    ip.split('@')[1]
                }\nProxyCommand cloudflared access ssh --hostname %h\n"
            )
            upload_status[ip] = UploadServerStatus()
            server_queues[queue_type].put(ip)
    push_webhook()
    print(blue(f"[DIST] Loaded {len(IPS)} ips"))

    print(blue("[DIST] Dist queue ready..."))
    threading.Thread(target=distribution_loop, daemon=True).start()
