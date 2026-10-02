#!/usr/bin/env python3
"""Production entry point of the voice bot on the EC2 server (systemd runs this file).

It runs backend/main.py the way the backend starts itself (its main(): settings, logging, uvicorn) and puts
three ASGI layers around the app through a small hook on uvicorn.Config, without changing the backend:

1. Settings. Before main.py is imported (it reads os.environ at import time), the environment is filled from
   SSM Parameter Store under $DOCAI_SSM_PREFIX (default /docai-voice):
     <prefix>/stack-env          String, JSON object written by CloudFormation (pool, client, IDP URL, host...)
     <prefix>/stack-extra-env    String, JSON object (template parameter ExtraEnvJson)
     <prefix>/secrets/<NAME>     SecureString, one variable each (deploy/secrets.sh): provider keys, ORIGIN_VERIFY
   Precedence: variables already set for the process (systemd unit) > secrets > extra-env > stack-env.
   Secrets stay in process memory; nothing is written to disk. Values are never logged, only names.
2. Origin check. CloudFront adds "X-Origin-Verify: <secret>" to every request it forwards; anything else is
   refused (HTTP 403 / WebSocket close 1008), except requests from 127.0.0.1 without X-Forwarded-For (health
   checks on the server). The header is removed before the request reaches the app. uvicorn's proxy_headers
   is forced off, so the client address is always the TCP peer and cannot be spoofed with X-Forwarded-For.
3. Web app. Serves the built frontend (FRONTEND_DIR) and generates /config.json and /aws-exports.json at run
   time (region, IDP user pool, voice app client, wss://<distribution>/ws, IDP link), so one build works for
   every stack. /release.json tells which release is running.
4. Logs. One line per request without query strings, tokens or phone URL keys; uvicorn's own access log is off
   (it would print the Cognito token from /ws?token=...). uvicorn still logs each WebSocket handshake with its
   path on "uvicorn.error": a filter on that logger keeps the first three segments of /phone/* paths (the Exotel
   URL key and sealed call tokens come after them) and hides token-like query values.
"""

from __future__ import annotations

import asyncio
import copy
import hmac
import importlib
import inspect
import json
import logging
import os
import posixpath
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

from starlette.responses import FileResponse, JSONResponse

LOG = logging.getLogger("docai.serve")

HERE = Path(__file__).resolve().parent
RELEASE_ROOT = HERE.parent.parent          # <release>/deploy/app/serve.py -> <release>
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
LOCAL_CLIENTS = {"127.0.0.1", "::1"}
ORIGIN_HEADER = b"x-origin-verify"
CONFIG_PATHS = ("/config.json", "/aws-exports.json", "/runtime-config.json")
NO_SPA_PREFIXES = ("/ws", "/phone/", "/whatsapp", "/calls/", "/api/", "/health")


# ---------------------------------------------------------------------------------------------- settings (SSM)
def fetch_parameters(prefix: str, client: Any) -> dict[str, str]:
    """All parameters under prefix (recursive, decrypted): {full name: value}."""
    out: dict[str, str] = {}
    paginator = client.get_paginator("get_parameters_by_path")
    for page in paginator.paginate(Path=prefix, Recursive=True, WithDecryption=True):
        for p in page.get("Parameters", []):
            out[p["Name"]] = p["Value"]
    return out


def load_parameters(prefix: str, client_factory: Callable[[], Any], wait_s: float = 180.0,
                    sleep: Callable[[float], None] = time.sleep) -> dict[str, str]:
    """Read the parameters; on a fresh server retry until <prefix>/stack-env exists (CloudFormation writes it)."""
    deadline = time.monotonic() + wait_s
    delay = 2.0
    client = None
    while True:
        try:
            client = client or client_factory()
            params = fetch_parameters(prefix, client)
            if f"{prefix}/stack-env" in params:
                return params
            reason = f"{prefix}/stack-env does not exist yet"
        except Exception as error:  # network not up yet at boot, throttling, missing permission
            reason = f"{type(error).__name__}: {error}"
            client = None
        if time.monotonic() >= deadline:
            raise SystemExit(f"cannot read settings from SSM ({reason})")
        LOG.warning("waiting for settings in SSM: %s", reason)
        sleep(delay)
        delay = min(delay * 2, 15.0)


def _json_object(text: str, name: str) -> dict[str, str]:
    try:
        value = json.loads(text or "{}")
    except ValueError as error:
        raise SystemExit(f"{name} is not valid JSON: {error}") from None
    if not isinstance(value, dict):
        raise SystemExit(f"{name} must be a JSON object")
    return {str(k): "" if v is None else str(v).lower() if isinstance(v, bool) else str(v) for k, v in value.items()}


def env_from_parameters(params: dict[str, str], prefix: str) -> dict[str, str]:
    """Merge the three layers: stack-env < stack-extra-env < secrets/*. Invalid variable names are skipped."""
    layers = [
        _json_object(params.get(f"{prefix}/stack-env", "{}"), f"{prefix}/stack-env"),
        _json_object(params.get(f"{prefix}/stack-extra-env", "{}"), f"{prefix}/stack-extra-env"),
        {name.rsplit("/", 1)[1]: value for name, value in params.items() if name.startswith(f"{prefix}/secrets/")},
    ]
    merged: dict[str, str] = {}
    for layer in layers:
        for key, value in layer.items():
            if ENV_NAME.match(key):
                merged[key] = value
            else:
                LOG.warning("ignoring setting with an invalid variable name: %r", key[:40])
    return merged


def apply_env(merged: dict[str, str], environ: Any = os.environ) -> list[str]:
    """Set variables that the process does not have yet; returns the names that were set."""
    applied = []
    for key, value in merged.items():
        if key not in environ:
            environ[key] = value
            applied.append(key)
    return applied


# ---------------------------------------------------------------------------------------------- runtime config
_ALIASES = {  # normalised key (lowercase, letters and digits only) -> value name
    **dict.fromkeys(["region", "awsregion", "cognitoregion", "authregion", "awsprojectregion", "awscognitoregion"],
                    "region"),
    **dict.fromkeys(["userpoolid", "cognitouserpoolid", "awsuserpoolsid"], "userPoolId"),
    **dict.fromkeys(["userpoolclientid", "appclientid", "clientid", "userpoolwebclientid", "cognitoappclientid",
                     "cognitoclientid", "awsuserpoolswebclientid", "webclientid"], "userPoolClientId"),
    **dict.fromkeys(["websocketurl", "wsurl", "apiurl", "backendwsurl", "backendwebsocketurl", "socketurl",
                     "voicewsurl", "voicewebsocketurl", "wsendpoint", "websocketendpoint"],
                    "websocketUrl"),
    **dict.fromkeys(["idpappurl", "idpurl", "idpweburl", "idpwebappurl", "documentaiurl", "idplink",
                     "backtoidpurl", "idpfrontendurl", "idphomeurl", "documentaiappurl"], "idpAppUrl"),
}
_DROP = {"identitypoolid", "awscognitoidentitypoolid"}   # the voice app has no identity pool (no AWS creds in browsers)


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def runtime_values(environ: Any = os.environ) -> dict[str, str]:
    host = environ.get("PUBLIC_HOST", "")
    ws = environ.get("PUBLIC_WS_URL") or (f"wss://{host}/ws" if host else "ws://localhost:8080/ws")
    return {
        "region": environ.get("AWS_REGION") or environ.get("AWS_DEFAULT_REGION") or "ap-south-1",
        "userPoolId": environ.get("USER_POOL_ID") or environ.get("COGNITO_USER_POOL_ID", ""),
        "userPoolClientId": (environ.get("APP_CLIENT_ID") or environ.get("COGNITO_APP_CLIENT_ID")
                             or environ.get("COGNITO_APP_CLIENT_IDS", "").split(",")[0].strip()),
        "websocketUrl": ws,
        "idpAppUrl": environ.get("IDP_APP_URL", ""),
    }


def runtime_extras(environ: Any = os.environ) -> dict:
    """Frontend options that follow the backend's settings: "Ring a phone" only when a provider is configured
    (same rules as backend/config.py), QA project link, start language, call length."""
    extras: dict[str, Any] = {}
    phone = len(environ.get("PHONE_URL_KEY", "")) >= 32 and bool(environ.get("PUBLIC_HOST"))
    providers = [name for name, keys in (("plivo", ("PLIVO_AUTH_ID", "PLIVO_AUTH_TOKEN", "PLIVO_NUMBER")),
                                         ("exotel", ("EXOTEL_SID", "EXOTEL_API_KEY", "EXOTEL_API_TOKEN",
                                                     "EXOTEL_EXOPHONE")))
                 if phone and all(environ.get(k) for k in keys)]
    mode = environ.get("OUTBOUND_CALLS_UI", "auto").strip().lower()
    extras["outboundCalls"] = bool(providers) if mode == "auto" else mode == "true"
    if providers:
        extras["providers"] = providers
    if environ.get("QA_PROJECT_ID"):
        extras["qaProjectId"] = environ["QA_PROJECT_ID"]
    if environ.get("DEFAULT_LANGUAGE") in ("en-IN", "hi-IN", "mr-IN"):
        extras["defaultLanguage"] = environ["DEFAULT_LANGUAGE"]
    if environ.get("CALL_MAX_SECONDS", "").isdigit():
        extras["maxCallSeconds"] = int(environ["CALL_MAX_SECONDS"])
    return extras


def _fill(node: Any, values: dict[str, str]) -> Any:
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            n = _norm(str(key))
            if n in _DROP:
                continue
            if n in _ALIASES and not isinstance(value, (dict, list)):
                out[key] = values[_ALIASES[n]]
            else:
                out[key] = _fill(value, values)
        return out
    if isinstance(node, list):
        return [_fill(v, values) for v in node]
    return node


def build_runtime_config(template: Any, values: dict[str, str], extras: dict | None = None) -> dict:
    """The frontend's own config.json / config.example.json with every known key filled in, the canonical keys,
    and the options that follow the backend's settings."""
    cfg = _fill(copy.deepcopy(template), values) if isinstance(template, dict) else {}
    for key, value in values.items():
        cfg.setdefault(key, value)
    cfg.update(extras or {})
    cognito = cfg.setdefault("amplify", {}).setdefault("Auth", {}).setdefault("Cognito", {})
    if isinstance(cognito, dict):
        cognito.pop("identityPoolId", None)
        cognito.update(userPoolId=values["userPoolId"], userPoolClientId=values["userPoolClientId"],
                       region=values["region"])
    ws = cfg.setdefault("websocket", {})
    if isinstance(ws, dict):
        ws["apiUrl"] = values["websocketUrl"]
    return cfg


# ---------------------------------------------------------------------------------------------- ASGI layers
async def _plain(send, status: int, body: bytes) -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


class OriginGuard:
    """Only our CloudFront distribution (X-Origin-Verify) and the server itself may reach the app."""

    def __init__(self, app, secrets: list[str]):
        self.app = app
        self.secrets = [s.encode() for s in secrets if s]

    def allowed(self, scope) -> bool:
        headers = scope.get("headers", [])
        client = scope.get("client")
        if client and client[0] in LOCAL_CLIENTS and not any(k == b"x-forwarded-for" for k, _ in headers):
            return True                    # the server itself (CloudFront always adds X-Forwarded-For)
        got = next((v for k, v in headers if k == ORIGIN_HEADER), None)
        return got is not None and any(hmac.compare_digest(got, s) for s in self.secrets)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        if not self.allowed(scope):
            if scope["type"] == "http":
                return await _plain(send, 403, b"Forbidden\n")
            await receive()                                   # websocket.connect
            return await send({"type": "websocket.close", "code": 1008})
        scope = dict(scope)
        scope["headers"] = [(k, v) for k, v in scope.get("headers", []) if k != ORIGIN_HEADER]
        return await self.app(scope, receive, send)


def cache_control(path: str) -> str:
    if path.startswith("/assets/"):
        return "public, max-age=31536000, immutable"      # Vite file names change with every build
    if path in ("/", "") or path.endswith(".html"):
        return "no-cache"
    return "public, max-age=300"


class WebApp:
    """Built frontend + runtime config in front of the backend app. Unknown paths go to the backend."""

    def __init__(self, app, root: Path | None, values: Callable[[], dict[str, str]],
                 release_file: Path | None = None, extras: Callable[[], dict] = runtime_extras):
        self.app = app
        self.root = root.resolve() if root and (root / "index.html").is_file() else None
        self.values = values
        self.extras = extras
        self.release_file = release_file
        self.template = None
        for name in ("config.json", "config.example.json"):     # the build's own file is the template
            if self.root and (self.root / name).is_file():
                try:
                    self.template = json.loads((self.root / name).read_text())
                    break
                except ValueError:
                    LOG.warning("frontend %s is not valid JSON: ignoring it", name)

    def file_for(self, path: str) -> Path | None:
        if self.root is None or "\x00" in path:
            return None
        rel = posixpath.normpath(path.lstrip("/") or "index.html")
        if rel in (".", "..") or rel.startswith(("../", "/")):
            return None
        try:
            real = (self.root / rel).resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if not real.is_file() or not str(real).startswith(str(self.root) + os.sep):
            return None
        return real

    def spa_route(self, scope) -> bool:
        path = scope["path"]
        if self.root is None or path.startswith(NO_SPA_PREFIXES) or "." in path.rsplit("/", 1)[-1]:
            return False
        accept = next((v for k, v in scope.get("headers", []) if k == b"accept"), b"")
        return b"text/html" in accept

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("GET", "HEAD"):
            return await self.app(scope, receive, send)
        path = scope["path"]
        if path in CONFIG_PATHS:
            body = build_runtime_config(self.template, self.values(), self.extras())
            return await JSONResponse(body, headers={"cache-control": "no-store"})(scope, receive, send)
        if path == "/release.json":
            info = {}
            if self.release_file and self.release_file.is_file():
                raw = json.loads(self.release_file.read_text())
                info = {k: raw.get(k) for k in ("id", "built_at")}
            return await JSONResponse(info, headers={"cache-control": "no-store"})(scope, receive, send)
        found = self.file_for(path)
        if found:
            return await FileResponse(found, headers={"cache-control": cache_control(path)})(scope, receive, send)
        if not self.spa_route(scope):
            return await self.app(scope, receive, send)
        # client-side route (e.g. /call): ask the backend first, answer index.html if it has no such page
        state = {"404": False}

        async def send_unless_404(message):
            if message["type"] == "http.response.start" and message["status"] == 404:
                state["404"] = True
            if not state["404"]:
                await send(message)

        await self.app(scope, receive, send_unless_404)
        if state["404"]:
            index = self.root / "index.html"
            await FileResponse(index, headers={"cache-control": "no-cache"})(scope, receive, send)


def redact_path(path: str) -> str:
    """Phone/WhatsApp paths carry signed tokens and URL keys: keep the first three segments only."""
    parts = path.split("/")
    if len(parts) > 4 and parts[1] in ("phone", "whatsapp"):
        return "/".join(parts[:4]) + "/…"
    return path[:120]


class AccessLog:
    """One log line per request: method, path without query, status, milliseconds (WebSocket: call length)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        started = time.monotonic()
        status = {"code": 0}

        async def watch(message):
            t = message["type"]
            if t in ("http.response.start", "websocket.http.response.start"):
                status["code"] = message["status"]
            elif t == "websocket.accept":
                status["code"] = 101
            elif t == "websocket.close" and not status["code"]:
                status["code"] = 403
            await send(message)

        try:
            await self.app(scope, receive, watch)
        finally:
            path = scope.get("path", "")
            client = (scope.get("client") or ("-",))[0]
            quiet = (path == "/health" and client in LOCAL_CLIENTS) or (path.startswith("/assets/")
                                                                       and status["code"] in (200, 304))
            if not quiet:
                method = "WS" if scope["type"] == "websocket" else scope.get("method", "-")
                ms = int((time.monotonic() - started) * 1000)
                LOG.info("%s %s %s %dms", method, redact_path(path), status["code"] or "-", ms)


class RedactTokens(logging.Filter):
    """Third-party log lines: uvicorn logs every WebSocket handshake with its full path and query, and phone
    paths carry the Exotel URL key and sealed call tokens. Keeps the first three segments of /phone/* paths
    (as the access log does) and hides the values of token-like query parameters."""

    QUERY = re.compile(r"([?&][\w.-]*(?:token|key|ctx|signature|secret)[\w.-]*=)[^&\s\"']+", re.IGNORECASE)
    PHONE_PATH = re.compile(r"(/(?:phone|whatsapp)/[^/\s\"'?]+/[^/\s\"'?]+)/[^\s\"']+")

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return True
        cleaned = self.PHONE_PATH.sub(r"\1/…", self.QUERY.sub(r"\1…", message))
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        return True


REDACTOR = RedactTokens()
# Logger-level filters stay when the backend replaces the logging handlers (backend/logging_setup.py does,
# with basicConfig(force=True)); uvicorn's handshake lines are logged on "uvicorn.error" itself.
REDACTED_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "websockets", "websockets.server")


def install_log_redaction() -> None:
    for handler in logging.getLogger().handlers:     # until the backend sets up its logging: every logger
        handler.addFilter(REDACTOR)
    for name in REDACTED_LOGGERS:
        logging.getLogger(name).addFilter(REDACTOR)  # addFilter ignores a filter that is already there


def build_app(inner, frontend_dir: Path | None, origin_secrets: list[str],
              values: Callable[[], dict[str, str]] = runtime_values, release_file: Path | None = None,
              extras: Callable[[], dict] = runtime_extras):
    return AccessLog(OriginGuard(WebApp(inner, frontend_dir, values, release_file, extras), origin_secrets))


def default_frontend_dir() -> Path | None:
    if os.environ.get("FRONTEND_DIR"):
        return Path(os.environ["FRONTEND_DIR"])
    for candidate in (RELEASE_ROOT / "frontend", RELEASE_ROOT / "frontend" / "dist", RELEASE_ROOT / "frontend" / "build"):
        if (candidate / "index.html").is_file() and (candidate.name != "frontend" or (candidate / "assets").is_dir()):
            return candidate
    return None


# ---------------------------------------------------------------------------------------------- main
def main() -> None:
    logging.basicConfig(level=os.environ.get("SERVE_LOG_LEVEL", "INFO"), stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    install_log_redaction()

    prefix = os.environ.get("DOCAI_SSM_PREFIX", "/docai-voice").rstrip("/")
    if prefix and os.environ.get("DOCAI_SKIP_SSM") != "1":
        import boto3  # noqa: PLC0415  (boto3 comes with the backend requirements)

        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"
        params = load_parameters(prefix, lambda: boto3.client("ssm", region_name=region),
                                 wait_s=float(os.environ.get("DOCAI_SSM_WAIT", "180")))
        applied = apply_env(env_from_parameters(params, prefix))
        LOG.info("settings from SSM %s: %s", prefix, ", ".join(sorted(applied)) or "none new")

    origin_secrets = [os.environ.pop("ORIGIN_VERIFY", ""), os.environ.pop("ORIGIN_VERIFY_PREVIOUS", "")]
    if not origin_secrets[0]:
        LOG.error("ORIGIN_VERIFY is not set: only requests from 127.0.0.1 will be answered")

    backend_dir = Path(os.environ.get("BACKEND_DIR", RELEASE_ROOT / "backend")).resolve()
    sys.path.insert(0, str(backend_dir))
    os.chdir(backend_dir)
    frontend = default_frontend_dir()
    release_file = Path(os.environ.get("RELEASE_FILE", RELEASE_ROOT / "RELEASE.json"))

    def wrap(inner):
        LOG.info("serving the backend with frontend %s", frontend or "(none)")
        return build_app(inner, frontend, origin_secrets, release_file=release_file)

    module_name, _, attr = os.environ.get("APP_IMPORT", "main").partition(":")
    sys.exit(run_backend(importlib.import_module(module_name), wrap, attr))


def uvicorn_overrides(kwargs: dict) -> dict:
    """Settings we insist on, whatever the backend passes to uvicorn."""
    out = dict(kwargs)
    out["host"] = os.environ.get("BIND_HOST") or out.get("host") or "0.0.0.0"
    out["port"] = int(os.environ.get("PORT") or out.get("port") or 8080)
    out["proxy_headers"] = False            # client = TCP peer (CloudFront); X-Forwarded-For is never trusted
    out.pop("forwarded_allow_ips", None)
    out["access_log"] = False               # it prints query strings (the browser's Cognito token)
    out["server_header"] = False
    # longer than CloudFront's 5 s origin keep-alive (no 502 races); a restart does not hang on open calls
    out["timeout_keep_alive"] = max(int(out.get("timeout_keep_alive") or 0), 75)
    out["timeout_graceful_shutdown"] = out.get("timeout_graceful_shutdown") or 10
    return out


def install_uvicorn_hook(wrap: Callable) -> None:
    """Wrap whatever ASGI app the backend hands to uvicorn.Config (uvicorn.run uses Config too)."""
    import uvicorn  # noqa: PLC0415

    original = uvicorn.Config.__init__
    if getattr(original, "_docai", False):
        return

    def patched(self, app, *args, **kwargs):
        if isinstance(app, str) or args:
            raise SystemExit("serve.py needs uvicorn.Config(app_object, **keywords) to add the origin check")
        original(self, wrap(app), **uvicorn_overrides(kwargs))

    patched._docai = True
    uvicorn.Config.__init__ = patched


def run_backend(module, wrap: Callable, attr: str = "") -> int:
    """Start the backend: its main() if it has one (preferred), else its app / create_app() under uvicorn."""
    import uvicorn  # noqa: PLC0415

    if not attr and callable(getattr(module, "main", None)):
        install_uvicorn_hook(wrap)
        result = module.main()
        if inspect.iscoroutine(result):
            result = asyncio.run(result)
        return result if isinstance(result, int) else 0
    app = getattr(module, attr or "app", None)
    if app is None and callable(getattr(module, "create_app", None)):
        app = module.create_app()
    if app is None:
        raise SystemExit(f"{module.__name__} has no main(), app or create_app()")
    uvicorn.run(wrap(app), **uvicorn_overrides({"log_config": None}))
    return 0


if __name__ == "__main__":
    main()
