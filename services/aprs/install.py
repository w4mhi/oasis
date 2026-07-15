#!/usr/bin/env python3
"""Service entry point for the APRS History API installer."""

from services.aprs.common.aprs import run


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Enable the APRS History API")
    parser.add_argument("--no-enable", action="store_true", help="Write the service file without enabling it")
    parser.add_argument("--port", type=int, default=8085, help="Port to expose")
    args = parser.parse_args()
    run(no_enable=args.no_enable, port=args.port)
