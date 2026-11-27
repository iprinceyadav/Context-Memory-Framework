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
from app.database.connection import engine
from app.database.migrations import run_migrations
from app.models import *
import os

run_migrations(engine)

app = FastAPI(title="Context Memory Framework API", version="2.0.0")

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

frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
