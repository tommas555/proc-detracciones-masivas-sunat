# proc_detracciones/extensions.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate

db = SQLAlchemy()
migrate = Migrate()

login_manager = LoginManager()
login_manager.login_view = "auth.login"          # adónde redirigir si no hay sesión
login_manager.login_message = "Inicia sesión para continuar."
login_manager.login_message_category = "warning"  # categoría de flash message (Bootstrap)
