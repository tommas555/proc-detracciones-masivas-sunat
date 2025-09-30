# En alembic/env.py
import os
from alembic import context
from sqlalchemy import engine_from_config, pool
from proc_detracciones import create_app
from proc_detracciones.extensions import db

app = create_app()
app.app_context().push()

target_metadata = db.Model.metadata
config = context.config

db_url = os.getenv("DATABASE_URL")
if not db_url:
    db_url = config.get_main_option("sqlalchemy.url")
if not db_url:
    db_url = "sqlite:///instance/app.db"  # ← Fallback explícito

if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

config.set_main_option("sqlalchemy.url", db_url)