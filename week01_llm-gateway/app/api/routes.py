from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> dict[str, str]:
    config_loaded = getattr(request.app.state, "config", None) is not None
    return {"status": "ok" if config_loaded else "starting"}
