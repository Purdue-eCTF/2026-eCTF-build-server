from builder import init_build_queue
from connection import serve

if __name__ == "__main__":
    init_build_queue()
    serve()
