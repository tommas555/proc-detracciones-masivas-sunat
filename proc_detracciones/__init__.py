# proc_detracciones/__init__.py
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Flask, flash, redirect, request, url_for, current_app
from .config import Config
from .extensions import db, login_manager
from .commands import register_cli

from proc_detracciones.extensions import db, migrate

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config())

    # ─────────────────────────────────────────────────────────────────────────────
    # [A] AJUSTES DEL TRIAL (PERÍODO DE PRUEBA)
    # - El trial comienza cuando el usuario pulsa "Continuar" en el magic link
    #   (se fija en auth.magic_post()).
    # - Puedes definir la duración con segundos/minutos/horas/días (se suman).
    # - TRIAL_ENDS_BY controla si se bloquea por fecha ("date"), por cuotas
    #   ("quota") o por ambos ("both"). (Se evalúa en routes/web.process()).
    # - TRIAL_TZ solo afecta el FORMATO mostrado (hora local que verás en UI).
    # ─────────────────────────────────────────────────────────────────────────────
    app.config.setdefault("TRIAL_DAYS", 2)
    app.config.setdefault("TRIAL_HOURS", 0)
    app.config.setdefault("TRIAL_MINUTES", 0)
    app.config.setdefault("TRIAL_SECONDS", 0)
    app.config.setdefault("TRIAL_ENDS_BY", "both")       # "date" | "quota" | "both"
    app.config.setdefault("TRIAL_TZ", "America/Lima")    # Zona horaria para mostrar

    # ─────────────────────────────────────────────────────────────────────────────
    # [B] CUOTAS DE USO DURANTE EL TRIAL
    # - TRIAL_XML_QUOTA: máximo de XML totales que puede procesar el usuario
    #   (se descuenta lo que intente subir en cada ejecución).
    # - TRIAL_RUNS_QUOTA: máximo de ejecuciones del proceso (runs) permitidas.
    #   Ambas se aplican/validan en routes/web.process().
    # ─────────────────────────────────────────────────────────────────────────────
    app.config.setdefault("TRIAL_XML_QUOTA", 12)   # p.ej. 10 XML totales
    app.config.setdefault("TRIAL_RUNS_QUOTA", 2)   # p.ej. 2 ejecuciones

    # ─────────────────────────────────────────────────────────────────────────────
    # [C] MAGIC LINK / TOKEN DE ACCESO
    # - TTL del enlace que envías (por seguridad: usa corto, p.ej. 120 s).
    # - Se usa al crear AuthToken en routes/auth.create_magic_link().
    # - Solo afecta a la PANTALLA del link; no cambia la duración del trial.
    # ─────────────────────────────────────────────────────────────────────────────
    app.config.setdefault("MAGIC_TOKEN_TTL_SECONDS", 0)  # para pruebas
    app.config.setdefault("MAGIC_TOKEN_TTL_HOURS", 6)      # si usas horas, pon >0 y deja SECONDS=0

    # ─────────────────────────────────────────────────────────────────────────────
    # [D] LÍMITES DE SUBIDA / UI
    # - MAX_CONTENT_LENGTH: tope del request en Flask (tamaño total, bytes).
    # - WEB_MAX_XML_PER_UPLOAD: si quieres limitar cuántos XML puede traer
    #   una sola subida (útil para UX; aplícalo en routes/web.process()).
    # ─────────────────────────────────────────────────────────────────────────────
    app.config.setdefault("MAX_CONTENT_LENGTH", 7 * 1024 * 1024)  # 7 MB server-side
    app.config.setdefault("WEB_MAX_XML_PER_UPLOAD", 500)           # límite suave por envío

    # ─────────────────────────────────────────────────────────────────────────────
    # [E] URLS Y ESQUEMA
    # - PREFERRED_URL_SCHEME se usa para construir links absolutos desde CLI.
    # - En producción define SERVER_NAME=tu-dominio (env var en Railway).
    # ─────────────────────────────────────────────────────────────────────────────
    app.config.setdefault("PREFERRED_URL_SCHEME", "https")
    # app.config.setdefault("SERVER_NAME", "127.0.0.1:5000")  # úsal0 en DEV si quieres links absolutos

    # ─────────────────────────────────────────────────────────────────────────────
    # Extensiones
    # ─────────────────────────────────────────────────────────────────────────────
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    # Blueprints
    from .routes.auth import auth_bp
    from .routes.web import web_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(web_bp)

    # CLI
    register_cli(app)

    # DB + user_loader
    with app.app_context():
        from .models import User, AuthToken
        db.create_all()

        @login_manager.user_loader
        def load_user(user_id: str):
            return User.query.get(int(user_id))

    # ─────────────────────────────────────────────────────────────────────────────
    # Guard global: si el TRIAL venció, cierra sesión y redirige a /auth/login
    # (No bloquea rutas de auth ni static).
    # ─────────────────────────────────────────────────────────────────────────────
    @app.before_request
    def _enforce_trial_expiry():
        from flask_login import current_user, logout_user
        if not getattr(current_user, "is_authenticated", False):
            return
        ep = (request.endpoint or "")
        if ep.startswith("auth.") or ep == "static":
            return

        ends = getattr(current_user, "trial_ends_at", None)
        if ends is None:
            return  # usuarios “activos” (sin trial) no se bloquean aquí

        if ends.tzinfo is None:
            ends = ends.replace(tzinfo=timezone.utc)

        if datetime.now(timezone.utc) > ends:
            tz = ZoneInfo(current_app.config.get("TRIAL_TZ", "America/Lima"))
            msg_time = ends.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
            logout_user()
            flash(f"Tu período de prueba venció el {msg_time}.", "warning")
            return redirect(url_for("auth.login"))

    return app
