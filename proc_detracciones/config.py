# proc_detracciones/config.py
import os

class Config:
    # SECRET_KEY - obligatoria en producción, con fallback en desarrollo
    SECRET_KEY = os.environ.get("SECRET_KEY")
    if not SECRET_KEY:
        if os.environ.get("FLASK_ENV") == "production":
            raise ValueError("SECRET_KEY no configurada en producción")
        # Fallback solo para desarrollo
        SECRET_KEY = "dev_secret_key_CAMBIAR_EN_PRODUCCION"
    
    # Database
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///app.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Limits
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50 MB
    
    # Email (SMTP) - Configuración desde variables de entorno
    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USERNAME = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
    MAIL_USE_TLS = True
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_USERNAME")


    ADMIN_EMAILS = os.getenv("ADMIN_EMAILS")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")