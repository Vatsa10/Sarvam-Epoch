"""FastAPI app: the real-time meet is the only surface.

Room lifecycle, language list, and the live WebSocket relay live under
app/meet_interface/ (mounted at /api/meet/*); the Next.js meet UI is served
as a static export at /meet.
"""
from __future__ import annotations

import pathlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from .meet_interface import db as meet_db
from .meet_interface.app import router as meet_router

ROOT = pathlib.Path(__file__).resolve().parent.parent
MEET_STATIC = ROOT / "frontend" / "out"

app = FastAPI(title="NyayBandhan")
app.include_router(meet_router, prefix="/api")

# The Next.js meet UI runs on :3000 in dev (npm run dev) against this API on
# :8000 - the only cross-origin case in the app, since the built static
# export is normally served by this same process at /meet. Regex (not a
# fixed list) because "localhost" and "127.0.0.1" are different origins to
# the browser, and dev falls back to :3001+ if :3000 is already taken.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _meet_db_startup() -> None:
    if meet_db.DATABASE_URL:
        await meet_db.get_pool()


@app.on_event("shutdown")
async def _meet_db_shutdown() -> None:
    await meet_db.close_pool()


@app.get("/", include_in_schema=False)
async def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/meet/")


if MEET_STATIC.exists():
    @app.get("/meet", include_in_schema=False)
    async def meet_redirect() -> RedirectResponse:
        # StaticFiles only mounts on the "/meet/..." prefix - the bare path
        # (no trailing slash) otherwise 404s instead of serving the meet UI.
        return RedirectResponse(url="/meet/")

    app.mount("/meet", StaticFiles(directory=str(MEET_STATIC), html=True), name="meet")
else:
    print(f"[meet_interface] {MEET_STATIC} not built yet - run `npm run build` in frontend/ "
          "to serve the meet UI at /meet")
