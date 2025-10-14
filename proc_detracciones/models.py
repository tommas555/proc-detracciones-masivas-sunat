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
    # xml_quota = db.Column(db.Integer, nullable=False, default=40)
    # xml_used = db.Column(db.Integer, nullable=False, default=0)
    # runs_quota = db.Column(db.Integer, nullable=False, default=2)
    # runs_used = db.Column(db.Integer, nullable=False, default=0)

    # Verificación de email
    is_email_verified = db.Column(db.Boolean, default=False, nullable=False)
    email_verified_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Nombres
    first_name = db.Column(db.String(120), nullable=True)
    last_name = db.Column(db.String(120), nullable=True)

    phone = db.Column(db.String(15), nullable=True)  # ✅ NUEVO CAMPO

    # Gestión de cuenta
    account_status = db.Column(db.String(24), default="TRIAL_ACTIVO", nullable=False)
    payment_proof_submitted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    payment_proof_url = db.Column(db.Text, nullable=True)
    payment_reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    payment_reviewed_by_admin_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    # Relación para el admin que revisó
    reviewed_by_admin = db.relationship("User", remote_side=[id], backref="reviewed_users", uselist=False)

    # Auditoría
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


    # Agregar estos campos a la clase User para la suscripción
    # subscription_start = db.Column(db.DateTime(timezone=True), nullable=True)
    # subscription_end = db.Column(db.DateTime(timezone=True), nullable=True)
    # months_paid = db.Column(db.Integer, default=0)  # Cuántos meses pagó
    # last_payment_date = db.Column(db.DateTime(timezone=True), nullable=True)
    payment_proof_reviewed_at = db.Column(db.DateTime(timezone=True), nullable=True)


    # proc_detracciones/models.py (dentro de la clase User)

    def get_active_subscription(self):
        """Devuelve la suscripción activa actual del usuario, o None si no tiene."""
        from datetime import datetime, timezone
        
        return UserPlanSubscription.query.filter(
            UserPlanSubscription.user_id == self.id,
            UserPlanSubscription.is_active == True,
            UserPlanSubscription.ends_at > datetime.now(timezone.utc)
        ).order_by(UserPlanSubscription.ends_at.desc()).first()


    # ---- helpers ----
    def set_password(self, pw: str) -> None:
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, pw)

    @property
    def is_active(self) -> bool:
        return bool(self.is_active_flag)

    def __repr__(self) -> str:
        ident = self.username or self.email or f"id:{self.id}"
        return f"<User {ident}>"


    def get_current_quota(self, service_id: int):
        """Calcula la cuota total y usada de un usuario para un servicio específico."""
        from .models import UserPlanSubscription, PlanServiceQuota, ServiceUsageLog
        from sqlalchemy import func
        from datetime import datetime, timezone

        # 1. Buscar LA ÚLTIMA suscripción activa (la más reciente)
        active_sub = db.session.query(UserPlanSubscription).filter(
            UserPlanSubscription.user_id == self.id,
            UserPlanSubscription.is_active == True,
            UserPlanSubscription.ends_at > datetime.now(timezone.utc)
        ).order_by(UserPlanSubscription.created_at.desc()).first()

        if not active_sub:
            return {
                "quota": 0,
                "used": 0,
                "remaining": 0,
                "is_unlimited": False
            }

        # 2. Obtener la cuota del plan
        plan_quota = db.session.query(PlanServiceQuota).filter_by(
            plan_id=active_sub.plan_id, service_id=service_id
        ).first()

        if not plan_quota:
            return {
                "quota": 0,
                "used": 0,
                "remaining": 0,
                "is_unlimited": False
            }

        is_unlimited = (plan_quota.xml_quota == -1)
        total_quota = plan_quota.xml_quota if not is_unlimited else 0

        # 3. Buscar el uso SOLO desde que inició la suscripción actual
        usage = db.session.query(func.sum(ServiceUsageLog.xml_processed)).filter(
            ServiceUsageLog.user_id == self.id,
            ServiceUsageLog.service_id == service_id,
            ServiceUsageLog.completed_at >= active_sub.starts_at  # ✅ FILTRO NUEVO
        ).scalar() or 0

        return {
            "quota": total_quota,
            "used": usage,
            "remaining": (total_quota - usage) if not is_unlimited else float('inf'),
            "is_unlimited": is_unlimited
        }    # <-- FIN DE LA FUNCIÓN 


class AuthToken(db.Model):
    """
    Token unificado para:
    - Magic links (usa token_hash)
    - Códigos de verificación de email (usa code de 4 dígitos)
    """
    __tablename__ = "auth_token"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    user = db.relationship("User", backref="auth_tokens")

    # "magic_login", "invite_trial", "verify_email"
    purpose = db.Column(db.String(20), nullable=False)

    # Para magic links: guardamos el hash del token
    token_hash = db.Column(db.String(128), unique=True, nullable=False, index=True)

    # NUEVO: Para verificación por código de 4 dígitos
    code = db.Column(db.String(4), nullable=True, index=True)

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
    reference_note = db.Column(db.String(255), nullable=True)
    first_used_ip = db.Column(db.String(45), nullable=True)

    magic_link_url = db.Column(db.Text, nullable=True)  # ✅ NUEVO CAMPO

    def __repr__(self) -> str:
        return f"<AuthToken user_id={self.user_id} purpose={self.purpose} used={self.used_count}/{self.max_uses}>"



    

class Plan(db.Model):
    """Planes disponibles (Trial, Básico, Premium, etc.)"""
    __tablename__ = 'plan'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)  # "Plan Trial", "Plan Básico"
    slug = db.Column(db.String(100), unique=True, nullable=False)  # "trial", "basico"
    description = db.Column(db.Text)
    price_monthly = db.Column(db.Integer, default=0)  # En centavos: 5000 = S/ 50.00
    is_active = db.Column(db.Boolean, default=True)
    is_unlimited = db.Column(db.Boolean, default=False)  # Si es True, ignora quotas
    trial_days = db.Column(db.Integer, nullable=True) 
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    
    # Relaciones
    service_quotas = db.relationship('PlanServiceQuota', backref='plan', cascade='all, delete-orphan')


class Service(db.Model):
    """Servicios disponibles (Detracciones, Retenciones, etc.)"""
    __tablename__ = 'service'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)  # "Detracciones"
    slug = db.Column(db.String(100), unique=True, nullable=False)  # "detracciones"
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    
    # Relaciones
    quotas = db.relationship('PlanServiceQuota', backref='service', cascade='all, delete-orphan')


class PlanServiceQuota(db.Model):
    """Límites de cada servicio dentro de un plan"""
    __tablename__ = 'plan_service_quota'
    
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('plan.id'), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey('service.id'), nullable=False)
    
    # Cuotas (-1 = ilimitado)
    xml_quota = db.Column(db.Integer, default=-1)  # -1 = sin límite
    runs_quota = db.Column(db.Integer, default=-1)
    
    __table_args__ = (db.UniqueConstraint('plan_id', 'service_id', name='unique_plan_service'),)


class UserPlanSubscription(db.Model):
    """Suscripción activa de un usuario a un plan"""
    __tablename__ = 'user_plan_subscription'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey('plan.id'), nullable=False)
    
    # Vigencia
    starts_at = db.Column(db.DateTime(timezone=True), nullable=False)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=False)
    
    # Estado
    is_active = db.Column(db.Boolean, default=True)
    auto_renew = db.Column(db.Boolean, default=False)
    
    # Auditoría
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    created_by_admin_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    # Relaciones
    user = db.relationship('User', foreign_keys=[user_id], backref='plan_subscriptions')
    plan = db.relationship('Plan', backref='subscriptions')
    created_by = db.relationship('User', foreign_keys=[created_by_admin_id])


class ServiceUsageLog(db.Model):
    """Registro de cada procesamiento (auditoría completa)"""
    __tablename__ = 'service_usage_log'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey('service.id'), nullable=False)
    
    # Detalles del procesamiento
    xml_processed = db.Column(db.Integer, default=0)  # Cantidad de XML procesados
    files_uploaded = db.Column(db.Integer, default=0)  # Archivos subidos
    success = db.Column(db.Boolean, default=True)  # Si terminó exitosamente
    
    # Metadata
    lote_number = db.Column(db.String(10))  # Número de lote procesado
    processing_time_seconds = db.Column(db.Float)  # Tiempo de ejecución
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.Text)
    
    # Timestamps
    started_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    completed_at = db.Column(db.DateTime(timezone=True))
    
    # Relaciones
    user = db.relationship('User', backref='usage_logs')
    service = db.relationship('Service', backref='usage_logs')
    error_log = db.relationship('ServiceErrorLog', uselist=False, backref='usage_log')


class ServiceErrorLog(db.Model):
    """Registro de errores en procesamientos"""
    __tablename__ = 'service_error_log'
    
    id = db.Column(db.Integer, primary_key=True)
    usage_log_id = db.Column(db.Integer, db.ForeignKey('service_usage_log.id'), nullable=False)
    
    # Error
    error_type = db.Column(db.String(100))  # "ValidationError", "ZipError", etc.
    error_message = db.Column(db.Text)
    stack_trace = db.Column(db.Text)
    
    # Timestamp
    occurred_at = db.Column(db.DateTime(timezone=True), default=utcnow)


