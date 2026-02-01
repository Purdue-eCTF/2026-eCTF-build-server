import subprocess
from pathlib import Path
import asyncio
import os

from boardtools import ProvisionClient, ProvisionConfig
from provision_common import BoardType, TestType

async def run_tests() -> bytes:
    HOST_DIR = Path.cwd()   
    VOLUME_NAME = "build_server_build_out"
    FILENAME = "hsm.bin"

    cmd = [
            "docker", "run", "--rm",
            "-v", f"{VOLUME_NAME}:/volume:ro",
            "alpine",
            "cat", f"/volume/{FILENAME}"
        ]

    print("[Build] Extracting board image from Docker volume...")
    result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE)
    board_image = result.stdout

    print(f"[Build] Board image extracted ({len(board_image)} bytes).")
    
    print("[Client] Provisioning board...")
    config = ProvisionConfig.load_from_file("boardtools/config.json")
    client = ProvisionClient(config, "test-client-"+ os.urandom(4).hex())
    board = await client.provision_board(BoardType.DEV)
    print("[Client] Provisioned board:", board.name)
    print("[Client] Flashing board image...")
    resp = await board.flash_image(board_image)
    print("[Client] Flashed board image.")
    resp = await board.power_cycle()
    print("[Client] Power cycle response:", resp)
    print("[Client] Running tests...")
    # Disabled for now until we actually have tests to run
    test_payload = b"test input data"
    # result = await board.run_tests(TestType.DEV, test_payload)
    result = "Tests not configured; Pretend this is test output."

    print("[Client] Dev test output:", result)
    print("[Client] Done.")
    client.close()
    return board_image

if __name__ == "__main__":
    board_image, test_output = asyncio.run(run_tests())
    print("board_image bytes:", len(board_image))
    print("test_output bytes:", len(test_output))