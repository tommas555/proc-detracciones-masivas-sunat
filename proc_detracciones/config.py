import os

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "clave_secreta_para_flask_2025")
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50 MB
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///app.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False



# import os

# BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))  # raíz del repo
# DEFAULT_SQLITE = f"sqlite:///{os.path.join(BASE_DIR, 'app.db')}"
# class Config:
#     SECRET_KEY = os.getenv("SECRET_KEY", "clave_secreta_para_flask_2025")
#     SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", DEFAULT_SQLITE)
#     SQLALCHEMY_TRACK_MODIFICATIONS = False
