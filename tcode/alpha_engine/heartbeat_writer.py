#!/usr/bin/env python3
"""
Minimal CLI wrapper so Go can write a heartbeat row via subprocess.

Usage:
  python3 heartbeat_writer.py --component engine_subscriber --status ok
  python3 heartbeat_writer.py --component engine_ibkr_status --status degraded --detail "roundtrip:62s"

Exit code 0 on success, 1 on error.
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from heartbeat import emit_heartbeat

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--component", required=True)
    parser.add_argument("--status", default="ok")
    parser.add_argument("--detail", default=None)
    args = parser.parse_args()
    emit_heartbeat(args.component, args.status, args.detail)
