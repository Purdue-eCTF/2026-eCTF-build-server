import asyncio
import os
from pathlib import Path

from boardtools import ProvisionClient, ProvisionConfig
from provision_common import BoardType, TestType

from config import AUTH_TOKEN
from jobs import Job


async def run_tests(job: Job):
    try:
        job.log("[TEST] Extracting board image from Docker volume...")
        with (Path.home() / "mounts/build_out/hsm.bin").open("rb") as f:
            board_image = f.read()

        job.log(f"[TEST] Board image extracted ({len(board_image)} bytes).")

        job.log("[TEST] Provisioning board...")
        config = ProvisionConfig.load_from_file("boardtools_config.json")
        config.auth_token = AUTH_TOKEN
        client = ProvisionClient(config, "test-client-" + job.commit.run_id)
        board = await client.provision_board(BoardType.DEV)
        job.log("[TEST] Provisioned board:", board.name)
        job.log("[TEST] Flashing board image...")
        resp = await board.flash_image(board_image)
        job.log("[TEST] Flashed board image.")
        job.log("[TEST] Running tests...")
        # Disabled for now until we actually have tests to run
        test_payload = b"test input data"
        result = await board.run_tests(TestType.DEV, test_payload)
        if result == "0":
            job.on_success()
        else:
            job.log("[TEST] Tests failed")
            job.conn.sendall(b"1\n")
            job.conn.close()

    except Exception as e:
        job.on_error(e, "[TEST] Error while testing")


if __name__ == "__main__":
    board_image, test_output = asyncio.run(run_tests())
    print("board_image bytes:", len(board_image))
    print("test_output bytes:", len(test_output))
