import json
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

PROMPT = "  <b>Escribe un resumen</b>  "
MODEL = "claude-sonnet"
FORM = {"prompt": PROMPT, "model": MODEL}
RESULT = {
    "model": MODEL,
    "estimated_tokens": 23,
    "estimated_cost_usd": 0.000069,
    "score": 87,
    "suggestions": ["Agrega el público objetivo."],
    "alerts": ["<script>alert(1)</script>"],
    "env": "API-DEV",
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("API_URL", raising=False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def post_api() -> Iterator[AsyncMock]:
    with patch.object(httpx.AsyncClient, "post", autospec=True) as post:
        yield post.mock


@pytest.mark.parametrize(
    ("configured", "expected", "badge"),
    [(None, "DEV", "badge-dev"), ("PROD", "PROD", "badge-prod")],
)
def test_health_and_form(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    configured: str | None,
    expected: str,
    badge: str,
) -> None:
    if configured is not None:
        monkeypatch.setenv("APP_ENV", configured)

    assert client.get("/health").json() == {
        "status": "ok", "service": "promptlint-web", "env": expected
    }
    page = client.get("/")
    assert page.status_code == 200
    assert f'class="badge {badge}">{expected}</span>' in page.text
    assert 'action="http://testserver/analyze" method="post"' in page.text
    assert '<textarea id="prompt" name="prompt"' in page.text
    for model in ("gpt-4o", "gemini-pro", "claude-sonnet"):
        assert f'value="{model}"' in page.text
    assert ">Analizar</button>" in page.text
    assert client.get("/static/style.css").status_code == 200


@pytest.mark.parametrize(
    ("api_url", "expected_url", "has_items"),
    [
        (None, "http://localhost:8000/analyze", True),
        ("http://promptlint-api:8000/", "http://promptlint-api:8000/analyze", False),
    ],
)
def test_analysis(
    client: TestClient,
    post_api: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    api_url: str | None,
    expected_url: str,
    has_items: bool,
) -> None:
    if api_url is not None:
        monkeypatch.setenv("API_URL", api_url)
    payload = RESULT if has_items else {**RESULT, "suggestions": [], "alerts": []}
    post_api.return_value = httpx.Response(
        200, json=payload, request=httpx.Request("POST", expected_url)
    )

    response = client.post("/analyze", data=FORM)

    post_api.assert_awaited_once()
    assert post_api.await_args.args[1:] == (expected_url,)
    assert post_api.await_args.kwargs == {"json": FORM}
    assert response.status_code == 200
    for field in ("model", "estimated_tokens", "estimated_cost_usd", "score", "env"):
        assert f"<dd>{payload[field]}</dd>" in response.text
    assert 'value="claude-sonnet" selected' in response.text
    assert "&lt;b&gt;Escribe un resumen&lt;/b&gt;" in response.text
    if has_items:
        assert "Agrega el público objetivo." in response.text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
        assert "<script>" not in response.text
    else:
        assert "Sin sugerencias." in response.text
        assert "Sin alertas." in response.text


@pytest.mark.parametrize(
    "form",
    [{}, {"prompt": "  ", "model": MODEL}, {"prompt": "Hola", "model": "otro"}],
)
def test_invalid_form(
    client: TestClient, post_api: AsyncMock, form: dict[str, str]
) -> None:
    response = client.post("/analyze", data=form)

    assert response.status_code == 422
    assert "selecciona un modelo válido" in response.text
    post_api.assert_not_awaited()


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout, httpx.InvalidURL])
def test_unreachable_api(
    client: TestClient, post_api: AsyncMock, failure: type[Exception]
) -> None:
    post_api.side_effect = failure("fallo simulado")

    response = client.post("/analyze", data=FORM)

    assert response.status_code == 503
    assert "No se pudo conectar con la API" in response.text
    assert 'value="claude-sonnet" selected' in response.text
    assert "&lt;b&gt;Escribe un resumen&lt;/b&gt;" in response.text


@pytest.mark.parametrize(
    ("status", "body"),
    [(500, "{}"), (200, "malformed"), (200, "{}"),
     (200, json.dumps({**RESULT, "score": "87"}))],
)
def test_unsuitable_api_response(
    client: TestClient, post_api: AsyncMock, status: int, body: str
) -> None:
    post_api.return_value = httpx.Response(
        status, content=body, request=httpx.Request("POST", "http://localhost:8000/analyze")
    )

    response = client.post("/analyze", data=FORM)

    assert response.status_code == 502
    assert "La API no devolvió un análisis válido" in response.text
