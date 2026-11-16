import re
import secrets
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from shd.app import create_app
from shd.config import Settings, load_settings


TOKEN = "test-secret-token"
PROTECTED_PATHS = ("/api/services", "/api/uptime?limit=3", "/metrics")


@pytest.fixture
def authenticated_app():
    return create_app(Settings(demo=True, api_token=TOKEN), start_checker=False)


def test_token_config():
    assert load_settings({}).api_token is None
    assert load_settings({"SHD_API_TOKEN": ""}).api_token is None
    settings = load_settings({"SHD_API_TOKEN": TOKEN})
    assert settings.api_token == TOKEN
    assert TOKEN not in repr(settings)


@pytest.mark.parametrize("token", [None, ""])
def test_unconfigured_auth_stays_open(token):
    app = create_app(Settings(demo=True, api_token=token), start_checker=False)
    with TestClient(app) as client:
        for path in PROTECTED_PATHS:
            for headers in ({}, {"Authorization": "Basic ignored"}):
                response = client.get(path, headers=headers)
                assert response.status_code == 200
                assert "www-authenticate" not in response.headers


@pytest.mark.parametrize("authorization", [
    None, "", "Basic " + TOKEN, "Bearer", "Bearer ",
    "Bearer wrong", "Bearer test-secret-tokem", "Bearer " + TOKEN + "extra",
    "Bearer  " + TOKEN, "Bearer " + TOKEN + " ", "Bearer\t" + TOKEN,
])
def test_invalid_credentials(authenticated_app, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    with TestClient(authenticated_app) as client:
        for path in PROTECTED_PATHS:
            response = client.get(path, headers=headers)
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
            assert TOKEN not in response.text


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "bEaReR"])
def test_valid_bearer_credentials(authenticated_app, scheme):
    with TestClient(authenticated_app) as client:
        headers = {"Authorization": f"{scheme} {TOKEN}"}
        services = client.get("/api/services", headers=headers)
        assert services.status_code == 200
        assert services.json()["count"] == 6
        uptime = client.get("/api/uptime?limit=3", headers=headers)
        assert uptime.status_code == 200
        assert uptime.json()["targets"][0]["history"]
        metrics = client.get("/metrics", headers=headers)
        assert metrics.status_code == 200
        assert "shd_target_up" in metrics.text


def test_entire_api_namespace_and_methods_are_protected(authenticated_app):
    @authenticated_app.get("/api/future")
    def future():
        return {"ok": True}

    with TestClient(authenticated_app) as client:
        for path in (*PROTECTED_PATHS, "/api", "/api/unknown", "/api/future", "/metrics/"):
            for method in ("GET", "POST", "HEAD", "OPTIONS"):
                response = client.request(method, path, follow_redirects=False)
                assert response.status_code == 401
                assert response.headers["www-authenticate"] == "Bearer"
        assert client.get("/api/future", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_credentials_use_constant_time_comparison(authenticated_app):
    with TestClient(authenticated_app) as client:
        with patch("shd.app.secrets.compare_digest", wraps=secrets.compare_digest) as compare:
            for token, status in ((TOKEN, 200), ("wrong", 401)):
                assert client.get("/metrics", headers={"Authorization": f"Bearer {token}"}).status_code == status
                compare.assert_called_with(token.encode("utf-8"), TOKEN.encode("utf-8"))


def test_public_health_and_htmx_polling_work_without_token(authenticated_app):
    with TestClient(authenticated_app) as client:
        assert client.get("/health").json()["status"] == "ok"
        page = client.get("/")
        assert page.status_code == 200
        assert client.get("/static/htmx.min.js").status_code == 200
        assert client.get("/static/style.css").status_code == 200
        polls = re.findall(r'hx-get="([^"]+)"', page.text)
        assert set(polls) == {"/ui/services", "/ui/uptime"}
        for path in polls:
            response = client.get(path, headers={"HX-Request": "true"})
            assert response.status_code == 200
            assert "nextcloud" in response.text
            assert TOKEN not in response.text
            assert "set-cookie" not in response.headers
        assert TOKEN not in page.text
        # Public UI access never authenticates subsequent API requests.
        assert client.get("/api/services").status_code == 401
