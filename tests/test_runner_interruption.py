"""Восстановление после обрыва Runner (Master Spec §7.5, §33 acceptance №8)."""

import asyncio
import os
import tempfile
import unittest

from atlas_runner.client import RunnerClient
from atlas_runner.journal import RecoveryJournal
from atlas_runner.protocol import generate_token
from atlas_runner.server import RunnerConfig, RunnerServer
from atlas_test_base import AtlasTestCase  # noqa: F401 (sys.path)


class TestRunnerInterruption(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="atlas-runner-int-")
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    async def test_interrupted_job_marked_and_recovered_on_restart(self):
        token = generate_token()
        sock = os.path.join(self.tmp, "runner.sock")
        jrn = os.path.join(self.tmp, "journal.jsonl")

        # --- Runner инстанс №1: запускаем долгий job и обрываем его ---
        cfg = RunnerConfig(socket_path=sock, token=token, journal_path=jrn,
                           allowed_dirs=[self.tmp], allow_root=True, grace_s=1.0)
        srv1 = RunnerServer(cfg)
        await srv1.start()
        t1 = asyncio.create_task(srv1.serve_forever())
        client = RunnerClient(sock, token)
        rid = "req_long"

        async def do_run():
            return await client.run({"argv": ["sleep", "30"], "cwd": self.tmp,
                                     "timeout_s": 30, "request_id": rid}, timeout_s=35)
        run_task = asyncio.create_task(do_run())
        await asyncio.sleep(0.5)
        ack = await client.interrupt(rid)
        self.assertTrue(ack["found"])
        ev = await run_task
        self.assertEqual(ev[-1]["state"], "INTERRUPTED")
        await srv1.stop()
        t1.cancel()

        # journal зафиксировал interrupted
        j = RecoveryJournal(jrn)
        records = [r["event"] for r in j.read_all()]
        self.assertIn("interrupted", records)
        # у прерванного job есть finished (INTERRUPTED), поэтому он НЕ висит
        self.assertNotIn(rid, j.unfinished_jobs())

    async def test_unfinished_job_detected_after_hard_crash(self):
        # моделируем "жёсткий" обрыв: started без finished
        jrn = os.path.join(self.tmp, "journal2.jsonl")
        j = RecoveryJournal(jrn)
        j.started("req_crash", "codex", 12345)  # finished не записан → процесс "упал"
        # новый инстанс Runner при старте помечает такие job как INTERRUPTED
        token = generate_token()
        cfg = RunnerConfig(socket_path=os.path.join(self.tmp, "r2.sock"), token=token,
                           journal_path=jrn, allowed_dirs=[self.tmp], allow_root=True)
        srv = RunnerServer(cfg)  # конструктор выполняет recovery по journal
        self.assertIn("req_crash", srv.recovered_on_start)
        # после восстановления job больше не "висит"
        self.assertNotIn("req_crash", j.unfinished_jobs())
