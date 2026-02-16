import asyncio
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from queue import Queue
from threading import Thread

from colors import blue, red
from config import DESIGN_REPO, GITHUB_TOKEN
from jobs import ActionStatus, Job
from publish import publish_status

BUILD_QUEUE: Queue[Job] = Queue()
active_build: Job | None = None
active_tests: list[Job] = []


def add_to_build_queue(job: Job):
    """
    Add a job to the build queue
    :param job: The job to add
    """
    BUILD_QUEUE.put(job)


def build(job: Job):
    global active_build  # noqa: PLW0603
    active_build = job
    job.start_time = time.time()
    job.update_status(ActionStatus.BUILDING)
    try:
        job.log("[BUILD] Pulling from repo...")
        # pull from repo
        try:
            output = subprocess.run(
                "cd ectf-design-repo &&"
                "git checkout main &&"
                "git fetch &&"
                "git reset --hard origin/main &&"
                f"git checkout {job.commit.hash}",
                shell=True,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except subprocess.CalledProcessError as e:
            job.on_error(
                e,
                f"[BUILD] Failed to build commit {job.commit.hash}! No commit found.",
                ActionStatus.BUILD_FAILED,
            )

            return

        job.log("[BUILD] Building secrets...")
        # build secrets
        try:
            output = subprocess.run(
                "cd ectf-design-repo &&"
                "rm -rf secrets/* &&"
                "mkdir -p secrets &&"
                ". ./.venv/bin/activate &&"
                "uv run secrets ./secrets/global.secrets 1 2 3 4",
                shell=True,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except subprocess.CalledProcessError as e:
            job.on_error(
                e,
                f"[BUILD] Failed to build commit {job.commit.hash}! Failed to build secrets!",
                ActionStatus.BUILD_FAILED,
            )

            return

        job.log("[BUILD] Building firmware...")
        # build firmware

        # docker-in-docker jank
        # build_server_build_out is volume mounted to ~/mounts/build_out which is symlinked to ~/src/ectf-design-repo/build_out
        # build_server_secrets is volume mounted to ~/mounts/secrets which is symlinked to ~/src/ectf-design-repo/secrets
        # build_server_firmware is volume mounted to ~/mounts/firmware which is copied from ~/src/ectf-design-repo/firmware
        with subprocess.Popen(
            "cd ectf-design-repo &&"
            "rm -rf build_out/* ~/mounts/firmware/* &&"
            "(docker build -t build-hsm ./firmware &&"
            "cp -r ./firmware/* ~/mounts/firmware &&"
            "docker run --rm -v build_server_firmware:/hsm "
            "-v build_server_secrets:/secrets "
            "-v build_server_build_out:/out -e HSM_PIN='1a2b3c' "
            "-e PERMISSIONS='1234=R--:4321=RWC:1111=RW-' build-hsm) && "
            '[ -n "$(ls -A build_out 2>/dev/null)" ]',
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            process_group=0,
        ) as proc:
            # https://github.com/python/cpython/issues/119059
            timer = threading.Timer(
                60 * 10, lambda: os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            )
            timer.start()
            assert proc.stdout is not None
            for line in proc.stdout:
                job.log(line.rstrip())
        proc.wait(timeout=15)
        timer.cancel()
        if proc.returncode != 0:
            job.log(
                f"[BUILD] Failed to build commit {job.commit.hash}! Build failed: {proc.returncode}!"
            )
            job.on_failure(ActionStatus.BUILD_FAILED)
            return

        # output in build_out
        # TODO do we really need all of the design repo or is just build_out and secrets fine?
        # if so, we can drop the symlinks and just copy from the volume mount
        try:
            subprocess.run(
                f"cp -Lr ectf-design-repo/ {job.build_folder}",
                shell=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            job.on_error(
                e,
                f"[BUILD] Failed to build commit {job.commit.hash}! Build failed!",
                ActionStatus.BUILD_FAILED,
            )

            return

        job.log(f"[BUILD] Built {job.commit.hash}!")

        active_build = None
        from run_tests import run_tests

        Thread(target=lambda job: asyncio.run(run_tests(job)), args=(job,)).start()
    except (BrokenPipeError, TimeoutError):
        print(red("[BUILD] Client disconnected"))
    except Exception as e:  # noqa: BLE001
        job.on_error(e, "[BUILD] Error occurred during build", ActionStatus.BUILD_FAILED)
        return
    finally:
        active_build = None
        BUILD_QUEUE.task_done()


def build_loop():
    while True:
        job = BUILD_QUEUE.get()
        build(job)


def init_build_queue():
    """
    Start the build queue
    """

    # login into github
    if (
        subprocess.run(
            ["gh", "auth", "status"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        ).returncode
        != 0
    ):
        print("[BUILD] Setting up git...")
        try:
            subprocess.run(
                ["gh", "auth", "login", "--with-token"],
                check=True,
                input=GITHUB_TOKEN.encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            subprocess.run(
                ["gh", "auth", "setup-git"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=True,
            )
        except subprocess.CalledProcessError:
            print(red("[BUILD] Failed to set up git!"))
            print(red(traceback.format_exc()))
            sys.exit(1)

    # install boardtools
    try:
        subprocess.run(
            [
                "pip",
                "install",
                "git+https://github.com/Purdue-eCTF/2026-eCTF-provision-server#subdirectory=provision_common",
                "git+https://github.com/Purdue-eCTF/2026-eCTF-provision-server#subdirectory=boardtools",
                "--break-system-packages",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(red("[BUILD] Failed to set up boardtools!"))
        print(red(traceback.format_exc()))
        sys.exit(1)

    # pull repo
    if (
        subprocess.run(
            "cd ectf-design-repo && git status",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        ).returncode
        == 0
    ):
        print("[BUILD] Found existing repo, reusing...")
    else:
        print("[BUILD] Cloning repo...")
        subprocess.run(
            ["git", "clone", DESIGN_REPO, "ectf-design-repo"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    # setup for docker-in-docker jank
    subprocess.run(
        "rm -rf ./ectf-design-repo/secrets ./ectf-design-repo/build_out;"
        "ln -s ~/mounts/secrets ./ectf-design-repo/secrets;"
        "ln -s ~/mounts/build_out ./ectf-design-repo/build_out;",
        shell=True,
        check=True,
    )

    # create venv
    try:
        subprocess.run(
            "cd ectf-design-repo &&"
            "python -m venv .venv --prompt ectf-example &&"
            ". ./.venv/bin/activate &&"
            "python -m pip install -e ./ectf26_design/",
            shell=True,
            timeout=60,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
    except subprocess.SubprocessError:
        print(red("[BUILD] Failed to create venv!"))
        print(traceback.format_exc())
        sys.exit(1)
        return

    subprocess.run(["rm", "-rf", "./builds"], check=True)
    subprocess.run(["mkdir", "-p", "./builds"], check=True)

    print(blue("[BUILD] Build queue ready..."))
    publish_status()
    Thread(target=build_loop, daemon=True).start()
