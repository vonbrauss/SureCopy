"""Disposable synthetic fixtures. Tests never touch real drives or app data."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from surecopy.models import Destination, JobSpec, VerifyLevel
from surecopy.store import Store


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="surecopy-test-")
        self.src = os.path.join(self.tmp, "src")
        self.store = Store(os.path.join(self.tmp, "jobs.db"))

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dest(self, name: str) -> str:
        path = os.path.join(self.tmp, name)
        os.makedirs(path, exist_ok=True)
        return path

    def make(self, files: dict[str, bytes | int], root: str | None = None) -> str:
        root = root or os.path.join(self.src, "Show")
        os.makedirs(root, exist_ok=True)
        for rel, data in files.items():
            path = os.path.join(root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if isinstance(data, int):
                data = os.urandom(data)
            with open(path, "wb") as fh:
                fh.write(data)
        return root

    def spec(self, sources, dests, verify=VerifyLevel.FAST, **kw) -> JobSpec:
        if isinstance(verify, VerifyLevel):
            verify = [verify] * len(dests)
        return JobSpec("test", list(sources), [Destination(d, v) for d, v in zip(dests, verify)], **kw)


def read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def tree(root: str) -> dict[str, bytes]:
    out = {}
    for dirpath, _, files in os.walk(root):
        for name in files:
            if name.endswith(".surecopy-partial"):
                continue
            full = os.path.join(dirpath, name)
            out[os.path.relpath(full, root)] = read(full)
    return out
