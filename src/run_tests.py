import asyncio
import shutil
from pathlib import Path

from boardtools import ProvisionClient, ProvisionConfig
from provision_common import BoardType, TestData, TestType

from config import AUTH_TOKEN
from jobs import ActionStatus, Job

active_tests: list[Job] = []

TIMEOUT = 10 * 60


async def run_tests(job: Job):
    try:
        active_tests.append(job)
        job.update_status(ActionStatus.TEST_PENDING)

        job.log("[TEST] Extracting board image from Docker volume...")
        with (job.build_folder / "build_out/hsm.bin").open("rb") as f:
            board_image = f.read()

        job.log(f"[TEST] Board image extracted ({len(board_image)} bytes).")

        job.log("[TEST] Provisioning board...")
        config = ProvisionConfig.load_from_file("boardtools_config.json")
        config.auth_token = AUTH_TOKEN
        client = ProvisionClient(config, job.commit.run_id + "-test")
        async with await client.provision_board(BoardType.DEV) as board:
            job.log("[TEST] Provisioned board: " + board.name)

            job.update_status(ActionStatus.TESTING)

            job.log("[TEST] Flashing board image...")
            await board.flash_image(board_image)  # TODO: proper pin
            job.log("[TEST] Flashed board image.")

            job.log("[TEST] Running tests...")
            result = (
                await asyncio.wait_for(
                    board.run_tests(
                        TestType.DEV,
                        TestData(pin="1a2b3c", permissions="1234=R--:4321=RWC:1111=RW-"),
                    ),
                    timeout=TIMEOUT,
                )
            ).decode()
        if result == "0":
            job.on_success()
        else:
            job.log("[TEST] Tests failed: " + result)
            job.on_failure(ActionStatus.TEST_FAILED)
    except Exception as e:
        job.on_error(e, "[TEST] Error while testing", ActionStatus.TEST_FAILED)
    finally:
        active_tests.remove(job)
        shutil.rmtree(job.build_folder)
