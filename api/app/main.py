from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import auth, files, proposals, tenants, users

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
app.include_router(proposals.workload_router)  # before users.router — /users/{user_id} would shadow /users/workload
app.include_router(users.router)
app.include_router(proposals.router)
app.include_router(files.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
