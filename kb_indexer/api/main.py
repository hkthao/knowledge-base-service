from fastapi import FastAPI

from ..log import configure_logging
from .routers import health, index

configure_logging()

app = FastAPI(title="Knowledge Base Service", version="0.1.0")
app.include_router(health.router)
app.include_router(index.router)
