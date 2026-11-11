"""httpx request logs keep scheme, host and port only."""

import logging

import httpx

import app.net.egress_guard  # noqa: F401  (installs the filter)


def test_httpx_request_log_drops_path_query_and_userinfo(caplog):
    url = httpx.URL("https://user:pw@hc.example.org:8443/ping/0b8e-secret-uuid?key=abc#frag")
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info('HTTP Request: %s %s "%s"', "GET", url, "HTTP/1.1 200 OK")
    text = caplog.text
    assert "hc.example.org:8443" in text
    for secret in ("secret-uuid", "key=abc", "user", "pw@", "frag"):
        assert secret not in text
