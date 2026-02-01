import os
import socket
import sys

print("IP =", os.environ.get("IP"))
print("PORT =", os.environ.get("PORT"))
print("TOKEN =", os.environ.get("TOKEN"))


if __name__ == "__main__":
    if len(sys.argv) < 5:
        # ${{ github.sha }} ${{ github.actor }} ${{ github.event.head_commit.message }} ${{ github.run_id }}
        print("Usage: python3 client_test hash author name run_id")
        sys.exit(1)

    conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    conn.connect((os.environ["IP"], int(os.environ["PORT"])))
    conn.send(os.environ["TOKEN"].encode())
    ack = conn.recv(1024).decode()

    if not ack or "Invalid" in ack:
        print("Connection error")
        sys.exit(1)
    sep = chr(0x1B)

    conn.send(
        f"{sys.argv[1]}{sep}{sys.argv[2]}{sep}{sys.argv[3]}{sep}{sys.argv[4]}".encode()
    )

    while True:
        data = conn.recv(1024)
        line = data.decode(errors="ignore")
        sys.exit(int(line.split("\n")[0]))
        print(line, end="")