"""Tests for direct-serve HTTP quality features such as gzip compression."""

from fastapi.testclient import TestClient

from app.main import app


def test_openapi_json_is_gzipped_when_client_accepts_gzip():
    with TestClient(app) as client:
        response = client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"


def test_custom_swagger_docs_page_is_served():
    with TestClient(app) as client:
        response = client.get("/docs")

    assert response.status_code == 200
    assert "Meshloom API" in response.text
    assert "SwaggerUIBundle" in response.text
    assert 'url: "openapi.json"' in response.text
    assert 'href="api/health"' in response.text
    assert "repeating-linear-gradient" not in response.text
    assert "background-size: 32px 32px" not in response.text


def test_openapi_includes_docs_metadata():
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    data = response.json()
    assert data["info"]["description"].startswith("Meshloom exposes")
    assert "/docs" not in data["paths"]
    tags = {tag["name"]: tag["description"] for tag in data["tags"]}
    assert tags["messages"].startswith("Message history")
    assert tags["radio"].startswith("Radio configuration")


def test_openapi_declares_the_bearer_token_and_the_session_cookie():
    with TestClient(app) as client:
        data = client.get("/openapi.json").json()

    schemes = data["components"]["securitySchemes"]
    assert schemes["bearerToken"] == {**schemes["bearerToken"], "type": "http", "scheme": "bearer"}
    assert schemes["sessionCookie"]["in"] == "cookie"
    assert data["security"] == [{"bearerToken": []}, {"sessionCookie": []}]
    assert data["paths"]["/api/auth/login"]["post"]["security"] == []
    assert "security" not in data["paths"]["/api/auth/tokens"]["get"]
    created = data["paths"]["/api/auth/tokens"]["post"]["responses"]["201"]
    assert created["content"]["application/json"]["schema"]["$ref"].endswith("TokenCreatedDoc")


def _assert_hardening_headers(response) -> None:
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"


def test_hardening_headers_on_docs_openapi_and_errors():
    with TestClient(app) as client:
        docs = client.get("/docs")
        openapi = client.get("/openapi.json")
        missing = client.get("/api/definitely-not-an-endpoint")

    assert docs.status_code == 200
    assert openapi.status_code == 200
    assert missing.status_code == 404
    _assert_hardening_headers(docs)
    _assert_hardening_headers(openapi)
    _assert_hardening_headers(missing)


def test_openapi_documents_common_error_responses():
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    data = response.json()
    responses = data["paths"]["/api/messages/channel"]["post"]["responses"]
    assert responses["400"]["description"] == "Bad request"
    assert responses["423"]["description"] == "Radio unavailable or locked"
    assert responses["500"]["description"] == "Server error"
