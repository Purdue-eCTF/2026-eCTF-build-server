import os
import subprocess
import sys
import time
import traceback
from queue import Queue
from threading import Thread

from colors import blue, red
from config import DESIGN_REPO, GITHUB_TOKEN

# from distribution import TestingJob, add_to_dist_queue
from jobs import ActionResult, ActionStatus
from publish import publish_status

BUILD_QUEUE: Queue[ActionResult] = Queue()
active_build: ActionResult | None = None


def add_to_build_queue(job: ActionResult):
    """
    Add a job to the build queue
    :param job: The job to add
    """
    BUILD_QUEUE.put(job)


def build(job: ActionResult):
    global active_build  # noqa: PLW0603
    active_build = job
    job.start_time = time.time()
    job.update_status(ActionStatus.BUILDING)

    build_folder = f"./builds/{job.commit.run_id}"

    try:
        job.log("[BUILD] Pulling from repo...")
        # pull from repo
        try:
            output = subprocess.run(
                "cd ectf-design-repo &&"
                # "git checkout main &&"
                # "git fetch &&"
                # "git reset --hard origin/main &&"
                f"git checkout {job.commit.hash}",
                shell=True,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as e:
            job.on_error(
                e, f"[BUILD] Failed to build commit {job.commit.hash}! No commit found."
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
                stderr=subprocess.PIPE,
            )
        except subprocess.CalledProcessError as e:
            job.on_error(
                e,
                f"[BUILD] Failed to build commit {
                    job.commit.hash
                }! Failed to build secrets!\nError: {e.output}",
            )

            return

        job.log("[BUILD] Building firmware...")
        # build firmware
        try:
            if os.getenv("DOCKER"):
                # docker-in-docker jank
                # build_server_build_out is volume mounted to ~/mounts/build_out which is symlinked to ~/src/ectf-design-repo/build_out
                # build_server_secrets is volume mounted to ~/mounts/secrets which is symlinked to ~/src/ectf-design-repo/secrets
                # build_server_firmware is volume mounted to ~/mounts/firmware which is symlinked to ~/src/ectf-design-repo/firmware
                output = subprocess.run(
                    "cd ectf-design-repo &&"
                    "rm -rf build_out/* ~/mounts/firmware/* &&"
                    "(docker build -t build-hsm ./firmware &&"
                    "cp -r ./firmware/* ~/mounts/firmware &&"
                    "docker run --rm -v build_server_firmware:/hsm "
                    "-v build_server_secrets:/secrets "
                    "-v build_server_build_out:/out -e HSM_PIN='1a2b3c' "
                    "-e PERMISSIONS='1234=R--:4321=RWC' build-hsm) && "
                    '[ -n "$(ls -A build_out 2>/dev/null)" ]',
                    shell=True,
                    check=True,
                    timeout=60 * 10,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            else:
                output = subprocess.run(
                    "cd ectf-design-repo && ./build.sh && "
                    '[ -n "$(ls -A build_out 2>/dev/null)" ]',
                    shell=True,
                    check=True,
                    timeout=60 * 10,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            job.log(output.stdout)
            job.log(output.stderr)
        except subprocess.SubprocessError as e:
            job.on_error(
                e,
                f"[BUILD] Failed to build commit {
                    job.commit.hash
                }! Build failed!\nError: {e.output}",
            )

            return

        # output in build_out
        try:
            subprocess.run(
                f"cp -Lr ectf-design-repo/ {build_folder}",
                shell=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            job.on_error(
                e, f"[BUILD] Failed to build commit {job.commit.hash}! Build failed!"
            )

            return

        job.log(f"[BUILD] Built {job.commit.hash}!")

        active_build = None
        publish_status()
    finally:
        active_build = None
        BUILD_QUEUE.task_done()


def build_loop():
    while True:
        job = BUILD_QUEUE.get()
        try:
            build(job)
        except (BrokenPipeError, TimeoutError):
            print(red("[BUILD] Client disconnected"))
        except Exception:  # noqa: BLE001
            # error handling :tm:
            publish_status(job)
            traceback.print_exc()


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
                f"echo {GITHUB_TOKEN} | gh auth login --with-token",
                shell=True,
                check=True,
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
            return

    # pull repo
    if (
        subprocess.run(
            "cd ectf-design-repo && git status",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
            stderr=subprocess.PIPE,
        )
    if os.getenv("DOCKER"):  # setup for docker-in-docker jank
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
