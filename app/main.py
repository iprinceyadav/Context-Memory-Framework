import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.api import (
    access,
    health,
    organizations,
    users,
    databases,
    chat,
    memories,
)
from app.database.connection import engine, get_db
from app.database.migrations import run_migrations
from app.models import *
from app.services.indexer import ensure_index
from app.web import pages
from app.web.templating import STATIC_DIR

logger = logging.getLogger(__name__)

run_migrations(engine)


def sync_vector_index(application: FastAPI) -> None:
    sessions = application.dependency_overrides.get(get_db, get_db)()
    db = next(sessions)
    try:
        rebuilt = ensure_index(db)
        if rebuilt:
            logger.info("Vector index rebuilt: %s", rebuilt)
    except Exception:
        logger.warning("Vector index check failed; retrieval falls back to database scans.", exc_info=True)
    finally:
        sessions.close()


@asynccontextmanager
async def lifespan(application: FastAPI):
    sync_vector_index(application)
    yield


app = FastAPI(title="Context Memory Framework API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(organizations.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(databases.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(memories.router, prefix="/api")
app.include_router(access.router, prefix="/api")

app.include_router(pages.router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
