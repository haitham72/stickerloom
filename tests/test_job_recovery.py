"""Free reconciliation, ticket continuation and explicitly priced paid retry; no real provider."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from stickerloom.generation import jobs, recovery, higgsfield


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.job = jobs.create(self.out, "video", request={"prompt": "move", "model": "fake", "t2v": True})
        jobs.claim(self.out, self.job["id"], "paid-ticket")
        jobs.update(self.out, self.job["id"], status="FAILED", error="HTTP 503", model="fake", cost_estimate=4.5)

    def test_check_completed_downloads_by_ticket_once_and_records_divergence(self):
        fake = mock.Mock()
        fake.get.return_value = {"status": "completed", "result_url": "https://fake.test/result.mp4"}
        fake.download.side_effect = lambda url, p: (p.parent.mkdir(parents=True, exist_ok=True), p.write_bytes(b"video"))
        follow = mock.Mock()
        result = recovery.check(self.out, self.job["id"], fake, follow, "Noor")
        self.assertEqual(result["status"], "DONE")
        self.assertEqual(result["external_task_id"], "paid-ticket")
        self.assertEqual(result["provider_check"]["classification"], "DIVERGENCE")
        self.assertIn("Divergence, not a failure", result["provider_check"]["message"])
        self.assertTrue(all(h["actor"] == "human" and h["by"] == "Noor" for h in result["history"]))
        fake.get.assert_called_once_with("paid-ticket")
        fake.create.assert_not_called(); fake.wait.assert_not_called(); fake.cost.assert_not_called()
        self.assertEqual((self.out / result["result"]["file"]).read_bytes(), b"video")
        recovery.check(self.out, self.job["id"], fake, follow)
        self.assertEqual(fake.download.call_count, 1)
        self.assertEqual(follow.call_count, 1)
        self.assertEqual(len((self.out / "model_calls.jsonl").read_text().splitlines()), 1)

    def test_continue_is_same_ticket_and_refuses_without_one(self):
        result = recovery.continue_job(self.out, self.job["id"])
        self.assertEqual((result["status"], result["external_task_id"]), ("CLAIMED", "paid-ticket"))
        jobs.update(self.out, self.job["id"], status="FAILED", external_task_id=None)
        with self.assertRaisesRegex(jobs.JobError, "stored provider ticket"):
            recovery.continue_job(self.out, self.job["id"])

    def test_retry_is_paid_explicit_and_idempotent(self):
        jid = self.job["id"]
        with self.assertRaisesRegex(jobs.JobError, "SPENDS"):
            recovery.retry(self.out, jid)
        jobs.update(self.out, jid, retry_quote={"credits": 4.5, "at": jobs._now()})
        new = recovery.retry(self.out, jid, True, 4.5)
        self.assertNotEqual(new["id"], jid)
        self.assertIsNone(new["external_task_id"])
        self.assertEqual(new["request"]["approved_cost"], 4.5)
        self.assertEqual(recovery.retry(self.out, jid, True, 4.5)["id"], new["id"])
        self.assertEqual(len(jobs.list(self.out)), 2)

    def test_read_only_cli_command(self):
        with mock.patch.object(higgsfield, "_json", return_value={"status": "working"}) as cli:
            self.assertEqual(higgsfield.get("ticket")["status"], "working")
        cli.assert_called_once_with(["generate", "get", "ticket"])

    def test_check_working_never_downloads_or_spends(self):
        fake = mock.Mock()
        fake.get.return_value = {"status": "working"}
        result = recovery.check(self.out, self.job["id"], fake)
        self.assertEqual(result["status"], "FAILED")
        fake.download.assert_not_called(); fake.create.assert_not_called(); fake.wait.assert_not_called()

    def test_http_check_reconciles_and_paid_retry_requires_confirmation(self):
        import http.client
        import json
        import threading
        from stickerloom.console.server import serve
        inp = self.out / "inputs"
        inp.mkdir()
        srv, console = serve(self.out, inp, 0, block=False)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(console.release_writer)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        def post(verb, body=None):
            conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1])
            conn.request("POST", f"/api/jobs/{self.job['id']}/{verb}", json.dumps(body or {}), {"Content-Type": "application/json"})
            response = conn.getresponse()
            result = response.status, json.loads(response.read())
            conn.close()
            return result
        with mock.patch.object(higgsfield, "get", return_value={"status": "completed", "result_url": "https://fake.test/result.mp4"}) as lookup, \
             mock.patch.object(higgsfield, "download", side_effect=lambda url, p: (p.parent.mkdir(parents=True, exist_ok=True), p.write_bytes(b"video"))), \
             mock.patch.object(console, "job_followup", return_value=None):
            self.assertEqual(post("retry")[0], 409)
            status, job = post("check")
        self.assertEqual((status, job["status"], job["provider_check"]["classification"]), (200, "DONE", "DIVERGENCE"))
        lookup.assert_called_once_with("paid-ticket")
