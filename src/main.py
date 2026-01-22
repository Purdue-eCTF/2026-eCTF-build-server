from builder import init_build_queue
from connection import serve
# from distribution import init_distribution_queue

if __name__ == "__main__":
    init_build_queue()
    # init_distribution_queue()
    serve()
