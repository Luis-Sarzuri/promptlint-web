import os
from pathlib import Path
from typing import Annotated, Literal, get_args

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, ValidationError

Model = Literal["gpt-4o", "gemini-pro", "claude-sonnet"]
MODELS: tuple[Model, ...] = get_args(Model)
APP_DIR = Path(__file__).resolve().parent

app = FastAPI(title="PromptLint Web")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


class Analysis(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)

    model: str
    estimated_tokens: int
    estimated_cost_usd: float
    score: int
    suggestions: list[str]
    alerts: list[str]
    env: str


def app_env() -> str:
    return os.getenv("APP_ENV", "DEV")


def render_page(
    request: Request,
    *,
    prompt: str = "",
    model: str = MODELS[0],
    result: Analysis | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_env": app_env(),
            "models": MODELS,
            "prompt": prompt,
            "selected_model": model,
            "result": result,
            "error": error,
        },
        status_code=status_code,
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "promptlint-web", "env": app_env()}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return render_page(request)


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(
    request: Request,
    prompt: Annotated[str, Form()] = "",
    model: Annotated[str, Form()] = "",
) -> HTMLResponse:
    if not prompt.strip() or model not in MODELS:
        return render_page(
            request,
            prompt=prompt,
            model=model,
            error="Escribe un prompt y selecciona un modelo válido.",
            status_code=422,
        )

    api_url = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{api_url}/analyze", json={"prompt": prompt, "model": model}
            )
            response.raise_for_status()
            result = Analysis.model_validate_json(response.content)
    except (httpx.RequestError, httpx.InvalidURL):
        return render_page(
            request,
            prompt=prompt,
            model=model,
            error="No se pudo conectar con la API. Inténtalo de nuevo.",
            status_code=503,
        )
    except (httpx.HTTPStatusError, ValidationError):
        return render_page(
            request,
            prompt=prompt,
            model=model,
            error="La API no devolvió un análisis válido. Inténtalo de nuevo.",
            status_code=502,
        )

    return render_page(request, prompt=prompt, model=model, result=result)
