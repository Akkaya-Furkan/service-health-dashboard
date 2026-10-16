import argparse
import os

import uvicorn

from .app import create_app
from .config import load_settings


def main():
    p = argparse.ArgumentParser(prog="shd")
    p.add_argument("--demo", action="store_true", help="synthetic data, no docker, no network")
    p.add_argument("--host", default=os.environ.get("SHD_BIND", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("SHD_PORT", "8000")))
    a = p.parse_args()
    if a.demo:
        os.environ["SHD_DEMO"] = "1"
    uvicorn.run(create_app(load_settings()), host=a.host, port=a.port)


if __name__ == "__main__":
    main()
