"""The HTTP contract: well-formed, served, typed for TypeScript, and guarded against drift from the server's source."""
import json
import re
import threading
import unittest
from pathlib import Path

from stickerloom.console import openapi

SERVER = Path(__file__).resolve().parent.parent / "stickerloom" / "console" / "server.py"


def refs(node, out):
    if isinstance(node, dict):
        if "$ref" in node:
            out.add(node["$ref"])
        for v in node.values():
            refs(v, out)
    elif isinstance(node, list):
        for v in node:
            refs(v, out)


class SpecShape(unittest.TestCase):
    def setUp(self):
        self.spec = openapi.build()

    def test_it_is_openapi_31_and_every_ref_resolves(self):
        self.assertEqual(self.spec["openapi"], "3.1.0")
        found = set()
        refs(self.spec, found)
        names = set(self.spec["components"]["schemas"])
        self.assertTrue(found)
        for r in found:
            self.assertIn(r.rsplit("/", 1)[1], names, r)

    def test_operation_ids_are_unique_and_path_params_are_declared(self):
        ids = []
        for path, ops in self.spec["paths"].items():
            declared_in_path = set(re.findall(r"\{(\w+)\}", path))
            for method, op in ops.items():
                ids.append(op["operationId"])
                declared = {p["name"] for p in op.get("parameters", []) if p["in"] == "path"}
                self.assertEqual(declared, declared_in_path, f"{method} {path}")
        self.assertEqual(len(ids), len(set(ids)))

    def test_explicit_import_consents_validate_and_are_documented_as_booleans(self):
        from pydantic import ValidationError
        from stickerloom.console.app_models import ImportOptions
        options = ImportOptions.model_validate({"name": "again.mp4", "again": "1", "as_new": "1", "grid_ok": "true"})
        self.assertTrue(options.again and options.as_new and options.grid_ok)
        self.assertFalse(ImportOptions().again or ImportOptions().grid_ok)
        with self.assertRaises(ValidationError):
            ImportOptions.model_validate({"unknown_option": "1"})
        params = {p["name"]: p["schema"] for p in self.spec["paths"]["/api/import"]["post"]["parameters"]}
        self.assertEqual(params["again"]["type"], "boolean")
        self.assertEqual(params["grid_ok"]["type"], "boolean")

    def test_the_chat_and_events_and_assets_are_typed(self):
        p = self.spec["paths"]
        send = p["/api/chat/sessions/{id}/messages"]["post"]
        self.assertEqual(send["requestBody"]["content"]["application/json"]["schema"]["$ref"], "#/components/schemas/ChatSend")
        self.assertIn("Idempotency-Key", [x["name"] for x in send["parameters"]])
        ev = p["/api/generations/{id}/events"]["get"]
        self.assertIn("text/event-stream", ev["responses"]["200"]["content"])
        self.assertIn("Last-Event-ID", [x["name"] for x in ev["parameters"]])
        self.assertEqual(p["/api/assets/sign"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"], "#/components/schemas/AssetLink")


class DriftGuard(unittest.TestCase):
    """A route that exists in the server but not in the spec fails here, so the contract cannot silently rot."""

    def setUp(self):
        self.src = SERVER.read_text(encoding="utf-8")
        self.paths = set(openapi.build()["paths"])

    def test_every_static_api_route_of_the_server_is_described(self):
        static = {m for m in re.findall(r'path == "(/api/[^"]+)"', self.src)}
        missing = sorted(p for p in static if p not in self.paths)
        self.assertEqual(missing, [], "routes in server.py missing from console/openapi.py")

    def test_every_generation_verb_of_the_server_is_described(self):
        verbs = set(re.findall(r'if parts\[3\] == "([a-z_]+)"', self.src))
        wanted = {f"/api/generations/{{id}}/{v}" for v in verbs}
        missing = sorted(w for w in wanted if w not in self.paths)
        self.assertEqual(missing, [])

    def test_every_route_family_the_server_serves_has_a_description(self):
        for prefix in re.findall(r'path\.startswith\("(/api/[a-z]+)', self.src):
            self.assertTrue(any(p.startswith(prefix) for p in self.paths), prefix)


class EveryDocumentedRouteIsServed(unittest.TestCase):
    """The other direction (five documented routes used to answer 404). Every operation of the spec is called on a scratch server with dummy ids;
    the one thing it may not answer is the route-miss message. A missing batch / pack / job answers in its own words, so only an unserved URL fails here."""

    SKIP = {("GET", "/api/generations/{id}/events"),          # a stream
            ("POST", "/api/generations/{id}/reveal")}         # opens a window on this machine

    def test_no_documented_operation_is_a_route_miss(self):
        import http.client
        import shutil
        import tempfile
        from unittest import mock
        from stickerloom.generation import higgsfield
        from stickerloom.runtime import cache as cachemod
        from stickerloom.console.server import NO_ROUTE, serve
        tmp = Path(tempfile.mkdtemp())
        (tmp / "in").mkdir()
        mem = cachemod.Cache(force_memory=True)
        patches = [mock.patch.object(cachemod, "default", lambda: mem), mock.patch.object(higgsfield, "available", lambda: False)]
        for p in patches:
            p.start()
        srv, c = serve(tmp / "out", tmp / "in", 0, block=False)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        dummy = {"id": "1", "aid": "A1", "sid": "x", "token": "x.y", "path": "G001/slices/none.png"}
        misses, probed = [], 0
        try:
            for path, ops in openapi.build()["paths"].items():
                for method, op in ops.items():
                    if (method.upper(), path) in self.SKIP:
                        continue
                    url = re.sub(r"\{(\w+)\}", lambda m: dummy.get(m.group(1), "1"), path)
                    if path.startswith("/api/jobs/") and "{id}" in path:
                        url = url.replace("/1", "/J001")
                    if path.startswith("/api/chat/sessions/") and "{id}" in path:
                        url = url.replace("/1", "/S001")
                    if path.startswith("/api/tasks/") and "{id}" in path:
                        url = url.replace("/1", "/T001")
                    h = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
                    body = b"{}" if method.upper() == "POST" else None
                    h.request(method.upper(), url, body, {"Content-Type": "application/json"})
                    r = h.getresponse()
                    raw = r.read()
                    h.close()
                    probed += 1
                    try:
                        err = json.loads(raw).get("error")
                    except (ValueError, AttributeError):
                        err = None
                    if r.status == 404 and err == NO_ROUTE:
                        misses.append(f"{method.upper()} {path}")
            self.assertGreater(probed, 80)
            self.assertEqual(misses, [], "documented in console/openapi.py but not served by console/server.py")
        finally:
            srv.shutdown()
            c.release_writer()
            srv.server_close()
            for p in patches:
                p.stop()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_an_unserved_url_answers_the_route_miss_message(self):
        import http.client
        import shutil
        import tempfile
        from stickerloom.console.server import NO_ROUTE, serve
        tmp = Path(tempfile.mkdtemp())
        (tmp / "in").mkdir()
        srv, c = serve(tmp / "out", tmp / "in", 0, block=False)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            for method, url in (("POST", "/api/generations/1/delete"), ("GET", "/api/packs/abc"), ("POST", "/api/generations/1/telegram")):
                h = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
                h.request(method, url, b"{}" if method == "POST" else None, {"Content-Type": "application/json"})
                r = h.getresponse()
                self.assertEqual((r.status, json.loads(r.read())["error"]), (404, NO_ROUTE), f"{method} {url}")
                h.close()
        finally:
            srv.shutdown()
            c.release_writer()
            srv.server_close()
            shutil.rmtree(tmp, ignore_errors=True)


class TypeScript(unittest.TestCase):
    def test_types_are_generated_from_the_schemas(self):
        ts = openapi.typescript()
        for name in ("Session", "Message", "Step", "Sticker", "ChatSend", "Event", "AssetLink", "Health"):
            self.assertIn(f"export interface {name}", ts)
        self.assertEqual(ts.count("{"), ts.count("}"))
        self.assertIn('kind: "task" | "step" | "note" | "final";', ts)
        self.assertIn("status?:", ts)                                    # optional where not required


class Served(unittest.TestCase):
    def test_the_server_serves_the_same_document(self):
        import http.client
        import shutil
        import tempfile
        from stickerloom.console.server import serve
        tmp = Path(tempfile.mkdtemp())
        (tmp / "in").mkdir()
        srv, c = serve(tmp / "out", tmp / "in", 0, block=False)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            h = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
            h.request("GET", "/api/openapi.json")
            r = h.getresponse()
            body = json.loads(r.read())
            self.assertEqual(r.status, 200)
            self.assertEqual(set(body["paths"]), set(openapi.build()["paths"]))
        finally:
            srv.shutdown()
            c.release_writer()
            srv.server_close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
