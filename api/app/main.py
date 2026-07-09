from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import auth, tenants, users

app = FastAPI(title="Baton API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(tenants.router)
app.include_router(users.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
