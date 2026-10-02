"""Tests for deploy/app/serve.py (origin check, web app, runtime config, SSM settings, log redaction).

  python3 -m unittest discover -s deploy/tests -p 'test_*.py'      (needs fastapi + httpx, e.g. the backend venv)
"""

import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from fastapi import FastAPI, Request, WebSocket  # noqa: E402
from fastapi.responses import PlainTextResponse  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

import serve  # noqa: E402

SECRET = "s" * 43
GOOD = {"X-Origin-Verify": SECRET}
VALUES = {"region": "ap-south-1", "userPoolId": "ap-south-1_POOL", "userPoolClientId": "client123",
          "websocketUrl": "wss://d111.cloudfront.net/ws", "idpAppUrl": "https://d222.cloudfront.net"}


def backend_app():
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"message": "OK\n"}

    @app.get("/echo-headers")
    async def echo(request: Request):
        return dict(request.headers)

    @app.post("/phone/plivo/answer")
    async def answer():
        return PlainTextResponse("<Response/>", media_type="application/xml")

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("hello " + websocket.query_params.get("language", ""))
        await websocket.close()

    return app


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.front = root / "frontend"
        (self.front / "assets").mkdir(parents=True)
        (self.front / "index.html").write_text("<html>voice</html>")
        (self.front / "assets" / "index-abc123.js").write_text("console.log(1)")
        (self.front / "audio-processor.js").write_text("// worklet")
        (self.front / "config.json").write_text(json.dumps({
            "cognito": {"region": "PLACEHOLDER", "userPoolId": "PLACEHOLDER", "appClientId": "PLACEHOLDER",
                        "identityPoolId": "PLACEHOLDER"},
            "wsUrl": "ws://localhost:8080/ws", "idpUrl": "", "brand": {"name": "DSA Document AI"}}))
        (root / "secret.txt").write_text("outside the web root")
        self.release = root / "RELEASE.json"
        self.release.write_text(json.dumps({"id": "20261001T000000Z-abc1234", "built_at": "2026-10-01T00:00:00Z",
                                            "git_sha": "abc1234deadbeef"}))
        self.app = serve.build_app(backend_app(), self.front, [SECRET], values=lambda: dict(VALUES),
                                   release_file=self.release)
        self.client = TestClient(self.app)                            # remote client ("testclient")
        self.local = TestClient(self.app, client=("127.0.0.1", 40000))

    def tearDown(self):
        self.tmp.cleanup()


class OriginGuardTests(Fixture):
    def test_rejects_requests_without_the_cloudfront_header(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get("/", headers={"X-Origin-Verify": "wrong"}).status_code, 403)

    def test_accepts_cloudfront(self):
        self.assertEqual(self.client.get("/health", headers=GOOD).json(), {"message": "OK\n"})

    def test_server_itself_needs_no_header(self):
        self.assertEqual(self.local.get("/health").status_code, 200)

    def test_spoofed_forwarded_for_gets_no_local_exemption(self):
        # CloudFront always adds X-Forwarded-For; a "local" request carrying one is not the server itself
        self.assertEqual(self.local.get("/health", headers={"X-Forwarded-For": "127.0.0.1"}).status_code, 403)
        self.assertEqual(self.client.get("/health", headers={"X-Forwarded-For": "127.0.0.1"}).status_code, 403)

    def test_header_is_not_passed_to_the_app(self):
        seen = self.client.get("/echo-headers", headers=GOOD).json()
        self.assertNotIn("x-origin-verify", seen)

    def test_previous_secret_still_accepted_during_rotation(self):
        app = serve.build_app(backend_app(), None, ["new" * 15, SECRET])
        self.assertEqual(TestClient(app).get("/health", headers=GOOD).status_code, 200)

    def test_no_secret_configured_fails_closed(self):
        app = serve.build_app(backend_app(), None, ["", ""])
        self.assertEqual(TestClient(app).get("/health", headers={"X-Origin-Verify": ""}).status_code, 403)

    def test_websocket_rejected_without_header(self):
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with self.client.websocket_connect("/ws") as ws:
                ws.receive_text()
        self.assertEqual(ctx.exception.code, 1008)

    def test_websocket_through_cloudfront(self):
        with self.client.websocket_connect("/ws?token=abc&language=hi-IN", headers=GOOD) as ws:
            self.assertEqual(ws.receive_text(), "hello hi-IN")

    def test_post_routes_pass_through(self):
        r = self.client.post("/phone/plivo/answer", headers=GOOD)
        self.assertEqual(r.status_code, 200)
        self.assertIn("<Response/>", r.text)


class WebAppTests(Fixture):
    def test_index_not_cached(self):
        r = self.client.get("/", headers=GOOD)
        self.assertEqual(r.text, "<html>voice</html>")
        self.assertEqual(r.headers["cache-control"], "no-cache")

    def test_hashed_assets_cached_forever(self):
        r = self.client.get("/assets/index-abc123.js", headers=GOOD)
        self.assertEqual(r.status_code, 200)
        self.assertIn("immutable", r.headers["cache-control"])
        self.assertIn("javascript", r.headers["content-type"])

    def test_other_files_short_cache(self):
        r = self.client.get("/audio-processor.js", headers=GOOD)
        self.assertEqual(r.headers["cache-control"], "public, max-age=300")

    def test_head(self):
        r = self.client.head("/assets/index-abc123.js", headers=GOOD)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"")

    def test_no_path_traversal(self):
        for path in ("/../secret.txt", "/assets/../../secret.txt", "/%2e%2e/secret.txt", "/assets/%2e%2e/%2e%2e/secret.txt"):
            r = self.client.get(path, headers=GOOD)
            self.assertNotIn("outside the web root", r.text, path)

    def test_file_for_refuses_raw_traversal_and_symlink_escape(self):
        web = serve.WebApp(backend_app(), self.front, lambda: VALUES)
        (self.front / "escape.txt").symlink_to(Path(self.tmp.name) / "secret.txt")
        for raw in ("/../secret.txt", "../secret.txt", "/assets/../../secret.txt", "/escape.txt", "/a\x00b",
                    "/..", "/assets/"):
            self.assertIsNone(web.file_for(raw), raw)
        self.assertEqual(web.file_for("/assets/../index.html"), (self.front / "index.html").resolve())

    def test_runtime_config_fills_the_frontend_template(self):
        cfg = self.client.get("/config.json", headers=GOOD).json()
        self.assertEqual(cfg["cognito"], {"region": "ap-south-1", "userPoolId": "ap-south-1_POOL",
                                          "appClientId": "client123"})
        self.assertEqual(cfg["wsUrl"], "wss://d111.cloudfront.net/ws")
        self.assertEqual(cfg["idpUrl"], "https://d222.cloudfront.net")
        self.assertEqual(cfg["brand"], {"name": "DSA Document AI"})
        for key, value in VALUES.items():
            self.assertEqual(cfg[key], value)

    def test_aws_exports_shape_for_the_original_sample(self):
        cfg = self.client.get("/aws-exports.json", headers=GOOD).json()
        self.assertEqual(cfg["amplify"]["Auth"]["Cognito"],
                         {"userPoolId": "ap-south-1_POOL", "userPoolClientId": "client123", "region": "ap-south-1"})
        self.assertEqual(cfg["websocket"]["apiUrl"], "wss://d111.cloudfront.net/ws")
        r = self.client.get("/aws-exports.json", headers=GOOD)
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_release_info_hides_git_details(self):
        self.assertEqual(self.client.get("/release.json", headers=GOOD).json(),
                         {"id": "20261001T000000Z-abc1234", "built_at": "2026-10-01T00:00:00Z"})

    def test_client_side_routes_get_index(self):
        r = self.client.get("/call", headers={**GOOD, "Accept": "text/html,application/xhtml+xml"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.text, "<html>voice</html>")

    def test_api_404_stays_404(self):
        self.assertEqual(self.client.get("/missing.js", headers=GOOD).status_code, 404)
        self.assertEqual(self.client.get("/api/nope", headers={**GOOD, "Accept": "text/html"}).status_code, 404)
        self.assertEqual(self.client.get("/call", headers={**GOOD, "Accept": "application/json"}).status_code, 404)

    def test_backend_routes_win_over_spa(self):
        r = self.client.get("/health", headers={**GOOD, "Accept": "text/html"})
        self.assertEqual(r.json(), {"message": "OK\n"})

    def test_without_frontend_everything_goes_to_backend(self):
        app = serve.build_app(backend_app(), None, [SECRET])
        self.assertEqual(TestClient(app).get("/", headers=GOOD).status_code, 404)
        cfg = TestClient(app).get("/config.json", headers=GOOD).json()
        self.assertIn("websocketUrl", cfg)


class FrontendExampleConfigTests(unittest.TestCase):
    """The real frontend ships config.example.json: it becomes the template of the served config.json."""

    EXAMPLE = Path(__file__).resolve().parents[2] / "frontend" / "public" / "config.example.json"

    def test_example_template_filled(self):
        if not self.EXAMPLE.is_file():
            self.skipTest("frontend/public/config.example.json not present")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("<html></html>")
            (root / "config.example.json").write_text(self.EXAMPLE.read_text())
            env = {"PHONE_URL_KEY": "k" * 40, "PUBLIC_HOST": "d111.cloudfront.net", "PLIVO_AUTH_ID": "MA1",
                   "PLIVO_AUTH_TOKEN": "t", "PLIVO_NUMBER": "+91" "2200000000", "DEFAULT_LANGUAGE": "mr-IN"}
            app = serve.build_app(backend_app(), root, [SECRET], values=lambda: dict(VALUES),
                                  extras=lambda: serve.runtime_extras(env))
            cfg = TestClient(app).get("/config.json", headers=GOOD).json()
        text = json.dumps(cfg)
        for bad in ("REPLACE", "XXXXXXXXX"):            # frontend/src/lib/config.js rejects placeholders
            self.assertNotIn(bad, text)
        for key in ("region", "userPoolId", "userPoolClientId", "websocketUrl", "idpAppUrl"):
            self.assertEqual(cfg[key], VALUES[key])
        self.assertEqual(cfg["pipeline"], "transcribe-polly")      # kept from the example
        self.assertEqual(cfg["defaultLanguage"], "mr-IN")          # follows the backend's DEFAULT_LANGUAGE
        self.assertIs(cfg["outboundCalls"], True)
        self.assertEqual(cfg["providers"], ["plivo"])

    def test_outbound_button_follows_the_backend_rules(self):
        self.assertEqual(serve.runtime_extras({}), {"outboundCalls": False})
        short_key = {"PHONE_URL_KEY": "k" * 10, "PUBLIC_HOST": "h", "PLIVO_AUTH_ID": "a", "PLIVO_AUTH_TOKEN": "t",
                     "PLIVO_NUMBER": "n"}
        self.assertFalse(serve.runtime_extras(short_key)["outboundCalls"])
        exo = {"PHONE_URL_KEY": "k" * 40, "PUBLIC_HOST": "h", "EXOTEL_SID": "s", "EXOTEL_API_KEY": "k",
               "EXOTEL_API_TOKEN": "t", "EXOTEL_EXOPHONE": "+91"}
        self.assertEqual(serve.runtime_extras(exo)["providers"], ["exotel"])
        self.assertFalse(serve.runtime_extras({**exo, "OUTBOUND_CALLS_UI": "false"})["outboundCalls"])
        self.assertEqual(serve.runtime_extras({"CALL_MAX_SECONDS": "300", "QA_PROJECT_ID": "p1"}),
                         {"outboundCalls": False, "maxCallSeconds": 300, "qaProjectId": "p1"})


class RuntimeValuesTests(unittest.TestCase):
    def test_from_stack_env(self):
        env = {"AWS_REGION": "ap-south-1", "USER_POOL_ID": "p", "APP_CLIENT_ID": "c",
               "PUBLIC_HOST": "dxyz.cloudfront.net", "IDP_APP_URL": "https://idp"}
        self.assertEqual(serve.runtime_values(env), {"region": "ap-south-1", "userPoolId": "p",
                                                     "userPoolClientId": "c",
                                                     "websocketUrl": "wss://dxyz.cloudfront.net/ws",
                                                     "idpAppUrl": "https://idp"})

    def test_local_default(self):
        self.assertEqual(serve.runtime_values({})["websocketUrl"], "ws://localhost:8080/ws")


class FakeSSM:
    def __init__(self, pages_by_call):
        self.calls = 0
        self.pages_by_call = pages_by_call

    def get_paginator(self, name):
        assert name == "get_parameters_by_path"
        fake = self

        class P:
            def paginate(self, **kwargs):
                assert kwargs == {"Path": "/docai-voice", "Recursive": True, "WithDecryption": True}
                fake.calls += 1
                result = fake.pages_by_call[min(fake.calls, len(fake.pages_by_call)) - 1]
                if isinstance(result, Exception):
                    raise result
                return result

        return P()


class SettingsTests(unittest.TestCase):
    P = "/docai-voice"

    def params(self):
        return {
            f"{self.P}/stack-env": json.dumps({"USER_POOL_ID": "pool", "LOG_LEVEL": "INFO", "ENABLE_WHATSAPP": False}),
            f"{self.P}/stack-extra-env": json.dumps({"LOG_LEVEL": "DEBUG", "DSA_NAME": "Varunika"}),
            f"{self.P}/secrets/PLIVO_AUTH_TOKEN": "tok",
            f"{self.P}/secrets/LOG_LEVEL": "WARNING",
            f"{self.P}/secrets/bad-name": "x",
        }

    def test_layers_and_precedence(self):
        merged = serve.env_from_parameters(self.params(), self.P)
        self.assertEqual(merged, {"USER_POOL_ID": "pool", "LOG_LEVEL": "WARNING", "ENABLE_WHATSAPP": "false",
                                  "DSA_NAME": "Varunika", "PLIVO_AUTH_TOKEN": "tok"})

    def test_process_environment_wins(self):
        environ = {"LOG_LEVEL": "ERROR"}
        applied = serve.apply_env({"LOG_LEVEL": "INFO", "X": "1"}, environ)
        self.assertEqual(environ, {"LOG_LEVEL": "ERROR", "X": "1"})
        self.assertEqual(applied, ["X"])

    def test_waits_for_cloudformation_then_reads(self):
        stack = {"Parameters": [{"Name": f"{self.P}/stack-env", "Value": "{}"}]}
        fake = FakeSSM([RuntimeError("network down"), [{"Parameters": []}], [stack]])
        slept = []
        params = serve.load_parameters(self.P, lambda: fake, wait_s=60, sleep=slept.append)
        self.assertEqual(params, {f"{self.P}/stack-env": "{}"})
        self.assertEqual(len(slept), 2)

    def test_gives_up_after_the_wait(self):
        fake = FakeSSM([[{"Parameters": []}]])
        with self.assertRaises(SystemExit):
            serve.load_parameters(self.P, lambda: fake, wait_s=0, sleep=lambda s: None)

    def test_paginates(self):
        pages = [{"Parameters": [{"Name": f"{self.P}/stack-env", "Value": "{}"}]},
                 {"Parameters": [{"Name": f"{self.P}/secrets/A", "Value": "1"}]}]
        self.assertEqual(serve.fetch_parameters(self.P, FakeSSM([pages])),
                         {f"{self.P}/stack-env": "{}", f"{self.P}/secrets/A": "1"})

    def test_bad_json_stops_start_up(self):
        with self.assertRaises(SystemExit):
            serve.env_from_parameters({f"{self.P}/stack-env": "{not json"}, self.P)
        with self.assertRaises(SystemExit):
            serve.env_from_parameters({f"{self.P}/stack-env": "[1, 2]"}, self.P)


class StartupTests(unittest.TestCase):
    """serve.py starts the backend through its own main() and wraps the app handed to uvicorn.Config."""

    def setUp(self):
        import uvicorn

        self.original = uvicorn.Config.__init__

    def tearDown(self):
        import uvicorn

        uvicorn.Config.__init__ = self.original

    def test_overrides(self):
        out = serve.uvicorn_overrides({"host": "0.0.0.0", "port": 9000, "proxy_headers": True,
                                       "forwarded_allow_ips": "*", "access_log": True, "timeout_keep_alive": 5,
                                       "ws_max_size": 1 << 20})
        self.assertFalse(out["proxy_headers"])
        self.assertNotIn("forwarded_allow_ips", out)
        self.assertFalse(out["access_log"])
        self.assertEqual(out["timeout_keep_alive"], 75)
        self.assertEqual(out["timeout_graceful_shutdown"], 10)
        self.assertEqual(out["ws_max_size"], 1 << 20)       # the backend's own settings are kept

    def test_backend_main_runs_with_the_wrapped_app(self):
        import types

        import uvicorn

        seen = {}
        inner = backend_app()

        def fake_main():
            config = uvicorn.Config(inner, host="0.0.0.0", port=8080, proxy_headers=True, forwarded_allow_ips="*",
                                    log_config=None)
            seen["config"] = config
            return 0

        module = types.SimpleNamespace(__name__="main", main=fake_main, create_app=lambda: inner)
        wrapped = []
        rc = serve.run_backend(module, lambda app: wrapped.append(app) or serve.build_app(app, None, [SECRET]))
        self.assertEqual(rc, 0)
        self.assertEqual(wrapped, [inner])
        config = seen["config"]
        self.assertFalse(config.proxy_headers)
        self.assertIsInstance(config.app, serve.AccessLog)
        self.assertEqual(TestClient(config.app).get("/health").status_code, 403)      # guard is in front
        self.assertEqual(TestClient(config.app).get("/health", headers=GOOD).status_code, 200)

    def test_import_string_is_refused(self):
        import types

        import uvicorn

        module = types.SimpleNamespace(__name__="main", main=lambda: uvicorn.Config("main:app", port=1))
        with self.assertRaises(SystemExit):
            serve.run_backend(module, lambda app: app)

    def test_async_main_of_the_original_sample(self):
        import types

        import uvicorn

        seen = {}

        async def sample_main():
            seen["config"] = uvicorn.Config(backend_app(), host="0.0.0.0", port=8080)

        module = types.SimpleNamespace(__name__="main", main=sample_main)
        self.assertEqual(serve.run_backend(module, lambda app: serve.build_app(app, None, [SECRET])), 0)
        self.assertIsInstance(seen["config"].app, serve.AccessLog)


class LogTests(unittest.TestCase):
    def test_phone_paths_lose_their_tokens(self):
        self.assertEqual(serve.redact_path("/phone/plivo/ws/eyJhbGciOi.123.abc"), "/phone/plivo/ws/…")
        self.assertEqual(serve.redact_path("/phone/exotel/ws/SECRETKEY"), "/phone/exotel/ws/…")
        self.assertEqual(serve.redact_path("/phone/plivo/answer"), "/phone/plivo/answer")
        self.assertEqual(serve.redact_path("/ws"), "/ws")

    def test_query_tokens_redacted(self):
        record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1,
                                   '%s - "WebSocket %s" 403', ("1.2.3.4", "/ws?token=eyJraWQ.abc&language=hi-IN"), None)
        serve.RedactTokens().filter(record)
        self.assertEqual(record.getMessage(), '1.2.3.4 - "WebSocket /ws?token=…&language=hi-IN" 403')

    def test_uvicorn_websocket_lines_lose_phone_keys(self):
        # uvicorn logs every WebSocket handshake with its full path and query on "uvicorn.error"
        for path, want in (("/phone/exotel/ws/KEY123?sample-rate=16000", "/phone/exotel/ws/…"),
                           ("/phone/exotel/ws/KEY123/CTX456", "/phone/exotel/ws/…"),
                           ("/phone/plivo/ws/gAAAAABsealed", "/phone/plivo/ws/…"),
                           ("/phone/plivo/answer", "/phone/plivo/answer"),
                           ("/ws?language=hindi", "/ws?language=hindi"),
                           ("/whatsapp?hub.mode=subscribe&hub.verify_token=VT1&hub.challenge=7",
                            "/whatsapp?hub.mode=subscribe&hub.verify_token=…&hub.challenge=7")):
            record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1,
                                       '%s - "WebSocket %s" [accepted]', ("1.2.3.4:5", path), None)
            serve.RedactTokens().filter(record)
            self.assertEqual(record.getMessage(), f'1.2.3.4:5 - "WebSocket {want}" [accepted]')

    def test_redaction_survives_the_backend_replacing_log_handlers(self):
        seen = []

        class Capture(logging.Handler):
            def emit(self, record):
                seen.append(record.getMessage())

        root = logging.getLogger()
        uv = logging.getLogger("uvicorn.error")
        saved = (root.handlers[:], root.level, uv.handlers[:], uv.propagate, uv.level, uv.filters[:])
        try:
            serve.install_log_redaction()
            # what backend/logging_setup.py does: new root handlers (force=True) and its own uvicorn handlers
            logging.basicConfig(handlers=[Capture()], level=logging.INFO, force=True)
            uv.handlers, uv.propagate = [Capture()], False
            uv.setLevel(logging.INFO)
            uv.info('%s - "WebSocket %s" [accepted]', "1.2.3.4:5", "/phone/exotel/ws/KEY123?sample-rate=16000")
        finally:
            root.handlers, uv.handlers, uv.propagate, uv.filters = saved[0], saved[2], saved[3], saved[5]
            root.setLevel(saved[1])
            uv.setLevel(saved[4])
        self.assertEqual(seen, ['1.2.3.4:5 - "WebSocket /phone/exotel/ws/…" [accepted]'])

    def test_access_log_line(self):
        app = serve.build_app(backend_app(), None, [SECRET])
        with self.assertLogs("docai.serve", level="INFO") as logs:
            TestClient(app).get("/health?token=secret", headers=GOOD)
            TestClient(app).get("/phone/exotel/ws/KEY123", headers=GOOD)
        joined = "\n".join(logs.output)
        self.assertIn("GET /health 200", joined)
        self.assertNotIn("secret", joined)
        self.assertNotIn("KEY123", joined)


if __name__ == "__main__":
    unittest.main()
