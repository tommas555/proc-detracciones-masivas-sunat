# proc_detracciones/models.py
from __future__ import annotations

from datetime import datetime, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from .extensions import db


# ---------- helpers de tiempo (UTC aware) ----------
def utcnow():
    """Fecha/hora actual en UTC (timezone-aware)."""
    return datetime.now(timezone.utc)


# ---------- modelos ----------
class User(db.Model, UserMixin):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)

    # Identidad: permitimos email/username opcional para soportar magic link por username
    email = db.Column(db.String(255), unique=True, nullable=True, index=True)
    username = db.Column(db.String(64), unique=True, nullable=True, index=True)

    # Password puede ser NULL si el usuario se crea por invitación (magic link)
    password_hash = db.Column(db.String(255), nullable=True)

    # Estado/rol
    role = db.Column(db.String(20), default="user", nullable=False)
    is_active_flag = db.Column(db.Boolean, default=True, nullable=False)

    # Trial (timezone-aware)
    trial_ends_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Cuotas de trial
    xml_quota = db.Column(db.Integer, nullable=False, default=40)
    xml_used = db.Column(db.Integer, nullable=False, default=0)
    runs_quota = db.Column(db.Integer, nullable=False, default=2)
    runs_used = db.Column(db.Integer, nullable=False, default=0)

    # === NUEVOS CAMPOS: Verificación de email, nombres y gestión de cuenta ===
    # Verificación de email
    is_email_verified = db.Column(db.Boolean, default=False, nullable=False)
    email_verified_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Nombres
    first_name = db.Column(db.String(120), nullable=True)
    last_name = db.Column(db.String(120), nullable=True)

    # Gestión de cuenta
    account_status = db.Column(db.String(24), default="TRIAL_ACTIVO", nullable=False)
    payment_proof_submitted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    payment_proof_url = db.Column(db.Text, nullable=True)
    payment_reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    payment_reviewed_by_admin_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    # Relación para el admin que revisó (opcional, pero útil)
    reviewed_by_admin = db.relationship("User", remote_side=[id], backref="reviewed_users", uselist=False)

    # Auditoría
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    # ---- helpers ----
    def set_password(self, pw: str) -> None:
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
        # Si no tiene password (cuenta creada por invitación/magic link), no valida por password
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, pw)

    @property
    def is_active(self) -> bool:
        return bool(self.is_active_flag)

    def __repr__(self) -> str:
        ident = self.username or self.email or f"id:{self.id}"
        return f"<User {ident}>"


class AuthToken(db.Model):
    __tablename__ = "auth_token"

    id = db.Column(db.Integer, primary_key=True)

    # Si es invitación previa a crear usuario real, podría ser NULL (pero en tu flujo lo asociamos al crear)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    user = db.relationship("User", backref="auth_tokens")

    # "magic_login" o "invite_trial"
    purpose = db.Column(db.String(20), nullable=False)

    # Guardamos solo el hash del token
    token_hash = db.Column(db.String(128), unique=True, nullable=False, index=True)

    # Fechas timezone-aware
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    first_used_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Control de uso
    max_uses = db.Column(db.Integer, default=1, nullable=False)
    used_count = db.Column(db.Integer, default=0, nullable=False)
    revoked = db.Column(db.Boolean, default=False, nullable=False)

    # Auditoría
    created_by_admin_id = db.Column(db.Integer, nullable=True)
    first_used_ip = db.Column(db.String(45), nullable=True)

    def __repr__(self) -> str:
        return f"<AuthToken user_id={self.user_id} purpose={self.purpose} used={self.used_count}/{self.max_uses}>"
