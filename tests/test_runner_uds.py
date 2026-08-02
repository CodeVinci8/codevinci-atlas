"""Тесты UDS Runner: безопасность, стриминг, redaction, interrupt, timeout.

Master Spec §13, §30. Асинхронные тесты через IsolatedAsyncioTestCase.
"""

import asyncio
import os
import stat
import unittest

from atlas_core.redaction import SECRET_MARKER
from atlas_runner.client import RunnerClient
from atlas_runner.protocol import generate_token
from atlas_runner.server import RunnerConfig, RunnerServer
from atlas_test_base import AtlasTestCase  # noqa: F401  (гарантирует sys.path)


class RunnerHarness:
    def __init__(self, tmp):
        self.tmp = tmp
        self.token = generate_token()
        self.sock = os.path.join(tmp, "runner.sock")
        self.jrn = os.path.join(tmp, "journal.jsonl")

    async def __aenter__(self):
        cfg = RunnerConfig(socket_path=self.sock, token=self.token, journal_path=self.jrn,
                           allowed_dirs=[self.tmp], allow_root=True, heartbeat_s=0.2, grace_s=1.0)
        self.srv = RunnerServer(cfg)
        await self.srv.start()
        self.task = asyncio.create_task(self.srv.serve_forever())
        self.client = RunnerClient(self.sock, self.token)
        return self

    async def __aexit__(self, *exc):
        await self.srv.stop()
        self.task.cancel()


class TestRunnerUDS(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory(prefix="atlas-runner-")
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    async def test_socket_permissions_0660(self):
        async with RunnerHarness(self.tmp):
            mode = stat.S_IMODE(os.stat(os.path.join(self.tmp, "runner.sock")).st_mode)
            self.assertEqual(mode, 0o660)

    async def test_valid_run_streams_and_hashes(self):
        async with RunnerHarness(self.tmp) as h:
            ev = await h.client.run({"argv": ["printf", "%s\\n", "hello"], "cwd": self.tmp, "timeout_s": 10})
            types = [e["type"] for e in ev]
            self.assertIn("run.started", types)
            self.assertIn("run.output", types)
            fin = ev[-1]
            self.assertEqual(fin["type"], "run.finished")
            self.assertEqual(fin["state"], "SUCCEEDED")
            self.assertTrue(fin["output_hash"].startswith("sha256:"))

    async def test_wrong_token_rejected(self):
        async with RunnerHarness(self.tmp) as h:
            bad = RunnerClient(h.sock, "WRONG")
            ev = await bad.run({"argv": ["true"], "cwd": self.tmp})
            self.assertEqual(ev[0]["type"], "error")

    async def test_disallowed_executable(self):
        async with RunnerHarness(self.tmp) as h:
            ev = await h.client.run({"argv": ["/bin/ls"], "cwd": self.tmp})
            self.assertEqual(ev[0]["type"], "error")

    async def test_cwd_traversal_blocked(self):
        async with RunnerHarness(self.tmp) as h:
            ev = await h.client.run({"argv": ["true"], "cwd": "/etc"})
            self.assertEqual(ev[0]["type"], "error")

    async def test_secret_in_argv_refused(self):
        async with RunnerHarness(self.tmp) as h:
            ev = await h.client.run({"argv": ["printf", SECRET_MARKER], "cwd": self.tmp})
            self.assertEqual(ev[0]["type"], "error")  # §13.2 raw secret в запросе запрещён

    async def test_output_redaction(self):
        # секрет генерируется процессом (в файле), а не передаётся в argv
        secret_file = os.path.join(self.tmp, "secret.txt")
        with open(secret_file, "w", encoding="utf-8") as fh:
            fh.write("access_token=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345\n")
        async with RunnerHarness(self.tmp) as h:
            ev = await h.client.run({"argv": ["cat", "secret.txt"], "cwd": self.tmp, "timeout_s": 10})
            lines = [e["line"] for e in ev if e["type"] == "run.output"]
            self.assertTrue(lines)
            self.assertTrue(any("[REDACTED]" in l for l in lines))
            self.assertFalse(any("ghp_ABCDEF" in l for l in lines))

    async def test_timeout(self):
        async with RunnerHarness(self.tmp) as h:
            ev = await h.client.run({"argv": ["sleep", "30"], "cwd": self.tmp, "timeout_s": 1}, timeout_s=10)
            self.assertEqual(ev[-1]["state"], "TIMEOUT")

    async def test_interrupt(self):
        async with RunnerHarness(self.tmp) as h:
            rid = "req_it_1"

            async def do_run():
                return await h.client.run({"argv": ["sleep", "30"], "cwd": self.tmp,
                                           "timeout_s": 30, "request_id": rid}, timeout_s=35)
            task = asyncio.create_task(do_run())
            await asyncio.sleep(0.5)
            ack = await h.client.interrupt(rid)
            self.assertTrue(ack["found"])
            ev = await task
            self.assertEqual(ev[-1]["state"], "INTERRUPTED")
