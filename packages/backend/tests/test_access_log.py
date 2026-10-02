"""uvicorn's access log keeps the method, path and status but never the query string (app/access_log.py).

Some query strings carry personal data (GET .../eligibility/inputs?applicant=<PAN or name>), and on
Lambda the access log goes to CloudWatch. The lines are formatted with uvicorn's own access formatter,
and one test runs a real uvicorn server (h11 and httptools) with the app. Synthetic data only.
"""

import http.client
import io
import logging
import threading
import time

import pytest
import uvicorn
from uvicorn.logging import AccessFormatter

import app.main
from app.access_log import ACCESS_LOGGER, StripQueryString, install

PAN = "ABCDE1234F"


@pytest.fixture
def access_log():
    """uvicorn's access lines, formatted like its default config (without the level and colours)."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False))
    logger = logging.getLogger(ACCESS_LOGGER)
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield stream
    logger.removeHandler(handler)
    logger.setLevel(level)


def log_request(path: str, status: int = 200) -> None:
    """The call uvicorn's h11 and httptools protocols make for each response."""
    logging.getLogger(ACCESS_LOGGER).info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:50000", "GET", path, "1.1", status)


@pytest.mark.parametrize(
    "path",
    [
        f"/projects/proj_demo/eligibility/inputs?applicant={PAN}",
        "/projects/proj_demo/eligibility/inputs?applicant=Rahul%20Vijay%20Deshmukh",
        "/projects/proj_demo/eligibility/companies?name=Konkan%20Soft",
        "/artifacts/download-url?key=alice%2Fproj_demo%2Fartifacts%2Fart_1%2FRahul.docx",
        "/projects/proj_demo/graph?search=Rahul&shared_only=true",
    ],
)
def test_the_query_string_is_dropped(access_log, path):
    log_request(path)

    assert access_log.getvalue() == f'127.0.0.1:50000 - "GET {path.split("?")[0]} HTTP/1.1" 200 OK\n'


def test_a_line_without_query_string_is_unchanged(access_log):
    log_request("/projects/proj_demo/documents", 404)

    assert access_log.getvalue() == '127.0.0.1:50000 - "GET /projects/proj_demo/documents HTTP/1.1" 404 Not Found\n'


def test_the_app_installs_the_filter_once():
    install()

    logger = logging.getLogger(ACCESS_LOGGER)
    assert len([f for f in logger.filters if isinstance(f, StripQueryString)]) == 1


@pytest.mark.parametrize("http_impl", ["h11", "httptools"])
def test_a_real_server_never_logs_the_applicant(access_log, http_impl):
    # log_config=None: uvicorn leaves logging alone, so the fixture's handler gets its lines.
    config = uvicorn.Config(
        app.main.app, host="127.0.0.1", port=0, http=http_impl, ws="none", lifespan="off", log_config=None
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        # Without x-user-id the request is refused (422) before the route reads anything.
        connection.request("GET", f"/projects/proj_demo/eligibility/inputs?applicant={PAN}")
        assert connection.getresponse().status == 422
        connection.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    lines = access_log.getvalue()
    assert PAN not in lines
    assert '"GET /projects/proj_demo/eligibility/inputs HTTP/1.1" 422' in lines
