from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.api import routes_health, routes_organizations, routes_users, routes_databases
from app.database.connection import engine
from app.models.base import Base
# Import all models so Base metadata is aware of them
from app.models import *
import os

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Context Memory Framework API", version="1.0.0")

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(routes_health.router, prefix="/api")
app.include_router(routes_organizations.router, prefix="/api")
app.include_router(routes_users.router, prefix="/api")
app.include_router(routes_databases.router, prefix="/api")

# Serve frontend static files
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
