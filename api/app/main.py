from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import auth, clients, duties, files, notices, payments, performance, proposals, tenants, users


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler = None
    if get_settings().SCHEDULER_ENABLED:
        from .scheduler import start_scheduler

        scheduler = start_scheduler()
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Baton API", version="0.1.0", lifespan=lifespan)

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
app.include_router(duties.router)
app.include_router(payments.router)
app.include_router(notices.router)
app.include_router(clients.router)
app.include_router(performance.router)
app.include_router(files.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
