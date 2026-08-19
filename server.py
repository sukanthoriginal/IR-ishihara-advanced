#!/usr/bin/env python3
"""Entry point for the local Advanced IR-Ishihara server."""

from pathlib import Path

from shared.experiment_server import main


if __name__ == "__main__":
    main(Path(__file__).resolve().parent)
