"""Offline end-to-end self test (implemented after the mock server exists)."""

from __future__ import annotations


def run_selftest() -> bool:
    from tests.selftest import run

    return run()
