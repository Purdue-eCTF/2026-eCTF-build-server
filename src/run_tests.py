import asyncio
import os
from pathlib import Path

from boardtools import ProvisionClient, ProvisionConfig
from provision_common import BoardType

from jobs import Job


async def run_tests(job: Job):
    job.log("[Build] Extracting board image from Docker volume...")
    with (Path.home() / "mounts/build_out/hsm.bin").open("rb") as f:
        board_image = f.read()

    job.log(f"[Build] Board image extracted ({len(board_image)} bytes).")

    job.log("[Client] Provisioning board...")
    config = ProvisionConfig.load_from_file("boardtools/config.json")
    client = ProvisionClient(config, "test-client-" + os.urandom(4).hex())
    board = await client.provision_board(BoardType.DEV)
    job.log("[Client] Provisioned board:", board.name)
    job.log("[Client] Flashing board image...")
    resp = await board.flash_image(board_image)
    job.log("[Client] Flashed board image.")
    resp = await board.power_cycle()
    job.log("[Client] Power cycle response:", resp)
    job.log("[Client] Running tests...")
    # Disabled for now until we actually have tests to run
    test_payload = b"test input data"
    # result = await board.run_tests(TestType.DEV, test_payload)
    result = "Tests not configured; Pretend this is test output."

    job.log("[Client] Dev test output:", result)
    job.on_success()


if __name__ == "__main__":
    board_image, test_output = asyncio.run(run_tests())
    print("board_image bytes:", len(board_image))
    print("test_output bytes:", len(test_output))
