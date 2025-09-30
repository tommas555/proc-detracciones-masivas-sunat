# proc_detracciones/routes/auth.py
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    current_app,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
    has_request_context,
)
from flask_login import login_required, login_user, logout_user, current_user
from flask_wtf import FlaskForm
from sqlalchemy import func
from wtforms import StringField
from wtforms.validators import Length

from proc_detracciones.extensions import db
from proc_detracciones.models import User, AuthToken

# ------------------------------------------------------------------
#  BLUEPRINT ÚNICO
# ------------------------------------------------------------------
auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# ------------------------------------------------------------------
# Helpers fecha/UTC & TZ
# ------------------------------------------------------------------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _coerce_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

def _get_tz():
    name = current_app.config.get("TRIAL_TZ", "America/Lima")
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc

def _fmt_local(dt, with_label=False):
    """dd/mm/AAAA HH:MM:SS en hora local (Perú)."""
    if dt is None:
        return "-"
    tz = _get_tz()
    s = dt.astimezone(tz).strftime('%d/%m/%Y - %H:%M:%S')
    return f"{s} (hora de Lima)" if with_label else s

def _token_ttl() -> timedelta:
    secs = int(current_app.config.get("MAGIC_TOKEN_TTL_SECONDS", 0) or 0)
    hrs  = int(current_app.config.get("MAGIC_TOKEN_TTL_HOURS", 24) or 0)
    return timedelta(seconds=secs) if secs > 0 else timedelta(hours=hrs)

# ------------------------------------------------------------------
# Login / logout
# ------------------------------------------------------------------
@auth_bp.get("/login")
def login():
    return render_template("login.html")

@auth_bp.post("/login")
def login_post():
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        flash("Credenciales inválidas.", "danger")
        return redirect(url_for("auth.login"))

    login_user(user, remember=True)
    return redirect(url_for("web.form"))

@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))

# ------------------------------------------------------------------
# Magic link (trial sin contraseña)
# ------------------------------------------------------------------
def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _find_user_by_ident(ident: str) -> User | None:
    ident = (ident or "").strip().lower()
    if "@" in ident:
        return User.query.filter(func.lower(User.email) == ident).first()
    return User.query.filter(func.lower(User.username) == ident).first()

def _apply_trial_quotas_only(user: User) -> None:
    if getattr(user, "xml_quota", None) is None:
        user.xml_quota = int(current_app.config.get("TRIAL_XML_QUOTA", 40))
    if getattr(user, "runs_quota", None) is None:
        user.runs_quota = int(current_app.config.get("TRIAL_RUNS_QUOTA", 2))
    if getattr(user, "xml_used", None) is None:
        user.xml_used = 0
    if getattr(user, "runs_used", None) is None:
        user.runs_used = 0

def _ensure_trial_defaults_on_first_use(user: User) -> None:
    if getattr(user, "trial_ends_at", None) is not None:
        return
    s = int(current_app.config.get("TRIAL_SECONDS", 0) or 0)
    m = int(current_app.config.get("TRIAL_MINUTES", 0) or 0)
    h = int(current_app.config.get("TRIAL_HOURS", 0) or 0)
    d = int(current_app.config.get("TRIAL_DAYS", 14) or 0)
    delta = timedelta(seconds=s, minutes=m, hours=h, days=d)
    if delta.total_seconds() <= 0:
        delta = timedelta(days=int(current_app.config.get("TRIAL_DAYS", 14)))
    user.trial_ends_at = _utcnow() + delta

def create_magic_link(ident: str, purpose: str = "invite_trial", created_by_admin_id=None) -> str:
    user = _find_user_by_ident(ident)
    if not user and purpose == "invite_trial":
        if "@" in ident:
            user = User(email=ident)
        else:
            user = User(username=ident)
        _apply_trial_quotas_only(user)
        db.session.add(user)
        db.session.commit()
    if not user:
        raise ValueError("Usuario no encontrado y no es invitación.")

    raw = secrets.token_urlsafe(32)
    token = AuthToken(
        user_id=user.id,
        purpose=purpose,
        token_hash=_hash_token(raw),
        expires_at=_utcnow() + _token_ttl(),
        max_uses=1,
        revoked=False,
        created_by_admin_id=created_by_admin_id,
    )
    db.session.add(token)
    db.session.commit()

    path = f"/auth/magic?token={raw}"
    if has_request_context():
        return url_for("auth.magic_get", token=raw, _external=True)
    server = current_app.config.get("SERVER_NAME") or "127.0.0.1:5000"
    scheme = current_app.config.get("PREFERRED_URL_SCHEME", "http")
    return f"{scheme}://{server}{path}"

@auth_bp.get("/magic")
def magic_get():
    raw = (request.args.get("token") or "").strip()
    if not raw:
        flash("Token ausente.", "danger")
        return redirect(url_for("auth.login"))
    t = AuthToken.query.filter_by(token_hash=_hash_token(raw)).first()
    if not t or t.revoked or t.used_count >= t.max_uses:
        flash("Token inválido o expirado.", "danger")
        return redirect(url_for("auth.login"))
    exp = _coerce_utc(t.expires_at)
    if exp is None or (_utcnow() > exp):
        flash("Token inválido o expirado.", "danger")
        return redirect(url_for("auth.login"))
    token_expires_local = _fmt_local(exp, with_label=True)
    trial_end = _coerce_utc(getattr(t.user, "trial_ends_at", None))
    trial_expires_local = _fmt_local(trial_end, with_label=True) if trial_end else ""
    return render_template(
        "auth_magic_confirm.html",
        token=raw,
        token_expires_local=token_expires_local,
        trial_expires_local=trial_expires_local,
    )

@auth_bp.post("/magic")
def magic_post():
    raw = (request.form.get("token") or "").strip()
    if not raw:
        flash("Token ausente.", "danger")
        return redirect(url_for("auth.login"))
    t = AuthToken.query.filter_by(token_hash=_hash_token(raw)).first()
    if not t or t.revoked or t.used_count >= t.max_uses:
        flash("Token inválido o expirado.", "danger")
        return redirect(url_for("auth.login"))
    exp = _coerce_utc(t.expires_at)
    if exp is None or (_utcnow() > exp):
        flash("Token inválido o expirado.", "danger")
        return redirect(url_for("auth.login"))
    user = t.user
    _apply_trial_quotas_only(user)
    _ensure_trial_defaults_on_first_use(user)
    login_user(user, remember=True)
    t.used_count += 1
    if t.first_used_at is None:
        t.first_used_at = _utcnow()
        t.first_used_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    db.session.commit()
    return redirect(url_for("web.form"))

# ------------------------------------------------------------------
# Registro normal (con contraseña)
# ------------------------------------------------------------------
@auth_bp.route("/register", methods=["GET"])
def register_get():
    return render_template("register.html")

@auth_bp.route("/register", methods=["POST"], endpoint="register_post")
def register_post():
    username   = request.form.get("username", "").strip()
    first_name = request.form.get("first_name", "").strip()
    last_name  = request.form.get("last_name", "").strip()
    email      = (request.form.get("email") or "").strip().lower()
    password   = request.form.get("password", "")

    if not username or not first_name or not last_name or not email or not password:
        flash("Completa todos los campos.", "danger")
        return redirect(url_for("auth.register_get"))
    if User.query.filter(func.lower(User.username) == username.lower()).first():
        flash("Ese usuario ya existe.", "warning")
        return redirect(url_for("auth.register_get"))
    if User.query.filter(func.lower(User.email) == email.lower()).first():
        flash("Ese email ya está registrado.", "warning")
        return redirect(url_for("auth.register_get"))

    u = User(
        username=username,
        first_name=first_name,
        last_name=last_name,
        email=email,
        role="user",
        account_status="TRIAL_ACTIVO",
        is_email_verified=False,
    )
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    flash("Registro exitoso. Revisa tu correo para verificar la cuenta.", "success")
    return redirect(url_for("auth.login"))

# ------------------------------------------------------------------
# Wizard «Activar plan Pro»  (upgrade.html)
# ------------------------------------------------------------------
@auth_bp.get("/upgrade")
@login_required
def upgrade():
    # Si el usuario entró por magic-link → no tiene email → forzar completar registro
    if not current_user.email:
        return redirect(url_for("auth.register_get"))
    # Si ya tiene email → mostrar wizard normal (verificar → subir → estado)
    return render_template("upgrade.html")

# ---------- PASO 1: EDITAR PERFIL ----------
@auth_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    class ProfileForm(FlaskForm):
        first_name = StringField('Nombre', validators=[Length(max=120)])
        last_name  = StringField('Apellido', validators=[Length(max=120)])
    form = ProfileForm()
    if form.validate_on_submit():
        current_user.first_name = form.first_name.data.strip() or None
        current_user.last_name  = form.last_name.data.strip()  or None
        db.session.commit()
        flash('Perfil actualizado ✅', 'success')
        return redirect(url_for('auth.upgrade'))
    form.first_name.data = current_user.first_name or ''
    form.last_name.data  = current_user.last_name  or ''
    return render_template('profile.html', form=form)

# ---------- PASO 2: RE-ENVIAR VERIFICACIÓN ----------
@auth_bp.route('/resend-verification', methods=['POST'])
@login_required
def resend_verification():
    if current_user.is_email_verified:
        flash('Tu correo ya está verificado', 'info')
        return redirect(url_for('auth.upgrade'))
    # usa tu función ya existente
    send_verification_email(current_user)
    flash('Correo de verificación re-enviado ✅', 'success')
    return redirect(url_for('auth.upgrade'))

# ---------- PASO 3: SUBIR COMPROBANTE ----------
@auth_bp.route('/upload-proof', methods=['POST'])
@login_required
def upload_proof():
    file = request.files.get('proof')
    if not file or file.filename == '':
        flash('No se seleccionó archivo', 'warning')
        return redirect(url_for('auth.upgrade'))
    allowed = {'pdf', 'png', 'jpg', 'jpeg'}
    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in allowed:
        flash('Solo se permiten PDF, PNG o JPG', 'warning')
        return redirect(url_for('auth.upgrade'))

    folder = Path(current_app.root_path) / 'static' / 'proofs'
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    path = folder / name
    file.save(path)

    current_user.payment_proof_url = f"/static/proofs/{name}"
    current_user.payment_proof_submitted_at = _utcnow()
    current_user.account_status = 'PENDIENTE_REVISION'
    db.session.commit()
    flash('Comprobante subido – en revisión ✅', 'success')
    return redirect(url_for('auth.upgrade'))


# ----------  ENVÍO DE VERIFICACIÓN  ----------
def send_verification_email(user: User) -> None:
    # 1.  código aleatorio 6 dígitos
    code = f"{secrets.randbelow(10_000):04d}"

    # 2.  token con código
    token = AuthToken(
        user_id=user.id,
        purpose="verify_email",
        code=code,
        #expires_at=datetime.utcnow() + timedelta(minutes=10),
        expires_at = _utcnow() + timedelta(minutes=10),
        max_uses=1,
        revoked=False,
    )
    db.session.add(token)
    db.session.commit()

    # 3.  mail simple
    subject = "Verifica tu correo – Procesador de Detracciones"
    body = f"Hola {user.first_name or user.email},\n\n" \
           f"Tu código de verificación es:  {code}\n\n" \
           f"Válido por 10 minutos."

    # 4.  envío (por ahora consola)
    print("-----------  CÓDIGO DE VERIFICACIÓN  -----------")
    print(f"Para: {user.email}")
    print(body)
    print("------------------------------------------------")

# ----------  RECIBE EL LINK  ----------
@auth_bp.route('/verify-email/<token>')
def verify_email(token):
    """
    Marca el correo como verificado si el token es válido.
    """
    t = AuthToken.query.filter_by(
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        purpose="verify_email",
        revoked=False
    ).first()

    if not t or t.used_count >= t.max_uses or _utcnow() > t.expires_at:
        flash("El enlace es inválido o ha expirado.", "warning")
        return redirect(url_for("auth.upgrade"))

    user = t.user
    user.is_email_verified = True
    user.email_verified_at = _utcnow()
    t.used_count += 1
    db.session.commit()

    flash("Correo verificado ✅  Puedes continuar con el paso 3.", "success")
    return redirect(url_for("auth.upgrade"))


#--------------endpoint que valida el código
@auth_bp.route('/verify-code', methods=['POST'])
@login_required
def verify_code():
    code = (request.json or {}).get('code', '').strip()
    if not code or len(code) != 4 or not code.isdigit():
        return {"ok": False, "msg": "Código inválido"}, 400

    token = AuthToken.query.filter_by(
        user_id=current_user.id,
        purpose="verify_email",
        code=code,
        revoked=False
    ).first()

    if not token or token.used_count >= token.max_uses or _utcnow() > token.expires_at:
        return {"ok": False, "msg": "Código incorrecto o expirado"}, 400

    # marcar verificado
    current_user.is_email_verified = True
    # current_user.email_verified_at = datetime.utcnow()
    current_user.email_verified_at = _utcnow()
    token.used_count += 1
    db.session.commit()
    return {"ok": True, "msg": "Correo verificado ✅"}