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

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

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
    if dt is None:
        return "-"
    tz = _get_tz()
    s = dt.astimezone(tz).strftime('%d/%m/%Y a las %H:%M')
    return f"{s} (hora de Lima)" if with_label else s

def _token_ttl() -> timedelta:
    secs = int(current_app.config.get("MAGIC_TOKEN_TTL_SECONDS", 0) or 0)
    hrs  = int(current_app.config.get("MAGIC_TOKEN_TTL_HOURS", 24) or 0)
    return timedelta(seconds=secs) if secs > 0 else timedelta(hours=hrs)

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
    
    if not user.is_email_verified:
        return redirect(url_for("auth.upgrade"))
    
    return redirect(url_for("home.index"))

@auth_bp.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))

def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _find_user_by_ident(ident: str) -> User | None:
    ident = (ident or "").strip().lower()
    if "@" in ident:
        return User.query.filter(func.lower(User.email) == ident).first()
    return User.query.filter(func.lower(User.username) == ident).first()

def _apply_trial_quotas_only(user: User) -> None:
    if getattr(user, "xml_quota", None) is None:
        user.xml_quota = 10
    if getattr(user, "runs_quota", None) is None:
        user.runs_quota = 2
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
    
    # Obtener la fecha actual en la zona horaria de Lima
    tz = _get_tz()
    now = datetime.now(tz)
    
    # Establecer la fecha de vencimiento a medianoche
    trial_end = now + delta
    trial_end = trial_end.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Convertir a UTC para almacenamiento
    user.trial_ends_at = trial_end.astimezone(timezone.utc)



def create_magic_link(ident: str, purpose: str = "invite_trial", created_by_admin_id=None) -> str:
    from proc_detracciones.models import Plan, UserPlanSubscription
    from datetime import timedelta
    
    user = _find_user_by_ident(ident)
    if not user and purpose == "invite_trial":
        if "@" in ident:
            # Generar username único basado en email
            base_username = ident.split('@')[0]
            username = base_username
            counter = 1
            while User.query.filter_by(username=username).first():
                username = f"{base_username}{counter}"
                counter += 1
            user = User(email=ident, username=username)
        else:
            user = User(username=ident)
        
        # Configurar como trial activo desde el inicio
        user.account_status = 'TRIAL_ACTIVO'
        user.is_email_verified = True if "@" in ident else False
        user.email_verified_at = _utcnow() if "@" in ident else None
        
        ###_apply_trial_quotas_only(user)
        db.session.add(user)
        db.session.flush()  # Obtener el user.id
        
        # ✅ ASIGNAR PLAN TRIAL AUTOMÁTICAMENTE
        trial_plan = Plan.query.filter_by(slug='trial-detracciones').first()
        if trial_plan:

            from zoneinfo import ZoneInfo

            # Obtener zona horaria de Lima
            lima_tz = ZoneInfo("America/Lima")

            # Obtener medianoche de HOY en hora de Lima
            now_lima = datetime.now(lima_tz)
            start_at_midnight_lima = now_lima.replace(hour=0, minute=0, second=0, microsecond=0)

            # Convertir a UTC para guardar en la base de datos
            start_at_midnight = start_at_midnight_lima.astimezone(timezone.utc)

            # Desactivar suscripciones anteriores
            UserPlanSubscription.query.filter_by(user_id=user.id, is_active=True).update({'is_active': False})
            db.session.flush()

            # Desactivar suscripciones anteriores
            UserPlanSubscription.query.filter_by(user_id=user.id, is_active=True).update({'is_active': False})
            db.session.flush()

            trial_sub = UserPlanSubscription(
                    user_id=user.id,
                    plan_id=trial_plan.id,
                    starts_at=start_at_midnight,  # ✅ USAR LA VARIABLE CALCULADA
                    ends_at=start_at_midnight + timedelta(days=trial_plan.trial_days) if trial_plan.trial_days else None,  # ✅ USAR LA VARIABLE
                    is_active=True,
                    auto_renew=False
                )
            
            db.session.add(trial_sub)
        
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
    from proc_detracciones.models import Plan, UserPlanSubscription
    from datetime import timedelta
    
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
    ##_apply_trial_quotas_only(user)
    _ensure_trial_defaults_on_first_use(user)
    
    user.account_status = 'TRIAL_ACTIVO'
    user.is_email_verified = False  # Magic link no requiere verificación
    user.email_verified_at = _utcnow()
    
    # ✅ Verificar que tenga plan trial asignado
    if not user.get_active_subscription():
        trial_plan = Plan.query.filter_by(slug='trial-detracciones').first()
        if trial_plan:

            from zoneinfo import ZoneInfo

            # Obtener zona horaria de Lima
            lima_tz = ZoneInfo("America/Lima")

            # Obtener medianoche de HOY en hora de Lima
            now_lima = datetime.now(lima_tz)
            start_at_midnight_lima = now_lima.replace(hour=0, minute=0, second=0, microsecond=0)

            # Convertir a UTC para guardar en la base de datos
            start_at_midnight = start_at_midnight_lima.astimezone(timezone.utc)

            trial_sub = UserPlanSubscription(
                user_id=user.id,
                plan_id=trial_plan.id,
                starts_at=start_at_midnight,  # ✅ USAR LA VARIABLE CALCULADA
                ends_at=start_at_midnight + timedelta(days=trial_plan.trial_days) if trial_plan.trial_days else None,  # ✅ USAR LA VARIABLE
                is_active=True,
                auto_renew=False
            )
            db.session.add(trial_sub)
    
    login_user(user, remember=True)
    t.used_count += 1
    if t.first_used_at is None:
        t.first_used_at = _utcnow()
        t.first_used_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    db.session.commit()
    
    return redirect(url_for("home.index"))

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
    phone      = request.form.get("phone", "").strip()  # ✅ NUEVO

    # Validar que el teléfono tenga exactamente 9 dígitos si se proporciona
    if phone and (not phone.isdigit() or len(phone) != 9):
        flash("El celular debe tener exactamente 9 dígitos.", "danger")
        return render_template("register.html")



    if not username or not first_name or not last_name or not email or not password:
        flash("Completa todos los campos.", "danger")
        return render_template("register.html")
    
    # ✅ SI YA HAY UN USUARIO TRIAL LOGUEADO, ACTUALIZAR EN VEZ DE CREAR
    if current_user.is_authenticated and current_user.account_status == 'TRIAL_ACTIVO':
        # Verificar que el nuevo email/username no estén en uso por OTRO usuario
        existing_user = User.query.filter(func.lower(User.username) == username.lower()).first()
        if existing_user and existing_user.id != current_user.id:
            flash("Ese usuario ya existe.", "warning")
            return render_template("register.html")
        
        existing_email = User.query.filter(func.lower(User.email) == email.lower()).first()
        if existing_email and existing_email.id != current_user.id:
            flash("Ese email ya está registrado.", "warning")
            return render_template("register.html")
        
        # Actualizar el usuario trial existente
        current_user.username = username
        current_user.first_name = first_name
        current_user.last_name = last_name
        current_user.email = email
        current_user.phone = phone if phone else None  # ✅ NUEVO
        current_user.set_password(password)
        current_user.account_status = "PENDIENTE_VERIFICACION"
        current_user.is_email_verified = False
        
        # Verificar si es admin
        if current_user.email and current_user.email.lower() in [e.strip().lower() for e in current_app.config.get('ADMIN_EMAILS', [])]:
            current_user.role = 'admin'
        
        db.session.commit()
        
        try:
            send_verification_email(current_user)
            from flask import session
            session['code_sent_at'] = _utcnow().isoformat()
            flash("Datos actualizados. Revisa tu correo para el código de verificación.", "success")
        except Exception as e:
            flash("Datos actualizados, pero hubo un error al enviar el email. Usa el botón 'Enviar código'.", "warning")
        
        return redirect(url_for("auth.upgrade"))
    
    # ✅ SI NO HAY USUARIO LOGUEADO, CREAR UNO NUEVO (flujo normal)
    if User.query.filter(func.lower(User.username) == username.lower()).first():
        flash("Ese usuario ya existe.", "warning")
        return render_template("register.html")
    
    if User.query.filter(func.lower(User.email) == email.lower()).first():
        flash("Ese email ya está registrado.", "warning")
        return render_template("register.html")

    u = User(
        username=username,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone if phone else None,  # ✅ NUEVO
        role="user",
        account_status="PENDIENTE_VERIFICACION",
        is_email_verified=False,
    )
    
    u.set_password(password)
    _apply_trial_quotas_only(u)
    
    # Verificar si el email es admin
    if u.email and u.email.lower() in [e.strip().lower() for e in current_app.config.get('ADMIN_EMAILS', [])]:
        u.role = 'admin'
    
    db.session.add(u)
    db.session.commit()
    
    login_user(u, remember=True)
    
    try:
        send_verification_email(u)
        from flask import session
        session['code_sent_at'] = _utcnow().isoformat()
        flash("Registro exitoso. Revisa tu correo para el código de verificación.", "success")
    except Exception as e:
        flash("Registro exitoso, pero hubo un error al enviar el email. Usa el botón 'Enviar código'.", "warning")
    
    return redirect(url_for("auth.upgrade"))


@auth_bp.get("/upgrade")
@login_required
def upgrade():
    if not current_user.email:
        return redirect(url_for("auth.register_get"))
    
    # Si el usuario ya tiene una suscripción activa, mostrar su plan
    if current_user.account_status == 'ACTIVO':
        active_sub = current_user.get_active_subscription()
        if active_sub:
            return render_template("plan_status.html", subscription=active_sub)
    
    # Si no está activo, mostrar el proceso de upgrade
    return render_template("upgrade.html")


@auth_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        phone = request.form.get("phone", "").strip()
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        
        # Validar nombres obligatorios
        if not first_name or not last_name:
            flash("Nombres y apellidos son obligatorios.", "danger")
            return render_template("profile.html")
        
        # Validar celular si se proporciona
        if phone and (not phone.isdigit() or len(phone) != 9):
            flash("El celular debe tener exactamente 9 dígitos.", "danger")
            return render_template("profile.html")
        
        # Actualizar datos básicos
        current_user.first_name = first_name
        current_user.last_name = last_name
        current_user.phone = phone if phone else None
        
        # Si quiere cambiar la contraseña
        if current_password or new_password or confirm_password:
            # Verificar que se llenaron todos los campos de contraseña
            if not current_password or not new_password or not confirm_password:
                flash("Para cambiar la contraseña, completa todos los campos de contraseña.", "warning")
                return render_template("profile.html")
            
            # Verificar contraseña actual
            if not current_user.check_password(current_password):
                flash("La contraseña actual es incorrecta.", "danger")
                return render_template("profile.html")
            
            # Verificar que las contraseñas nuevas coincidan
            if new_password != confirm_password:
                flash("Las contraseñas nuevas no coinciden.", "danger")
                return render_template("profile.html")
            
            # Verificar longitud mínima
            if len(new_password) < 6:
                flash("La nueva contraseña debe tener al menos 6 caracteres.", "danger")
                return render_template("profile.html")
            
            # Cambiar la contraseña
            current_user.set_password(new_password)
            flash("Perfil y contraseña actualizados correctamente.", "success")
        else:
            flash("Perfil actualizado correctamente.", "success")
        
        db.session.commit()
        return redirect(url_for("auth.profile"))
    
    # GET request
    return render_template("profile.html")

@auth_bp.route('/resend-verification', methods=['POST'])
@login_required
def resend_verification():
    if current_user.is_email_verified:
        return {"ok": False, "msg": "Tu correo ya está verificado"}, 400
    
    try:
        send_verification_email(current_user)
        return {"ok": True, "msg": "Código enviado a tu email"}, 200
    except Exception as e:
        print(f"Error enviando email: {e}")
        return {"ok": False, "msg": "Error al enviar el código"}, 500

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

import smtplib
from email.message import EmailMessage



def send_verification_email(user: User) -> None:
    code = f"{secrets.randbelow(10000):04d}"
    raw_token = secrets.token_urlsafe(32)
    
    token = AuthToken(
        user_id=user.id,
        purpose="verify_email",
        code=code,
        token_hash=_hash_token(raw_token),
        expires_at=_utcnow() + timedelta(minutes=10),
        max_uses=1,
        revoked=False,
    )
    db.session.add(token)
    db.session.commit()
    
    subject = "Verifica tu cuenta - ContaPro"
    
    # HTML del correo
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Verificación de cuenta</title>
    </head>
    <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f6f8fb;">
        <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff;">
            <!-- Cabecera -->
            <div style="background-color: #0d6efd; padding: 30px 20px; text-align: center;">
                <h1 style="color: #ffffff; margin: 0; font-size: 28px; font-weight: 600;">ContaPro</h1>
                <p style="color: #e7f0ff; margin: 10px 0 0 0; font-size: 16px;">Soluciones Contables Inteligentes</p>
            </div>
            
            <!-- Contenido principal -->
            <div style="padding: 40px 30px;">
                <h2 style="color: #0d6efd; margin: 0 0 20px 0; font-size: 24px;">¡Hola {user.first_name or user.email}!</h2>
                <p style="color: #4b5563; margin: 0 0 25px 0; font-size: 16px; line-height: 1.6;">
                    Gracias por registrarte en nuestro sistema. Para completar tu registro y activar tu cuenta, por favor verifica tu correo electrónico usando el siguiente código:
                </p>
                
                <!-- Código de verificación destacado -->
                <div style="background-color: #f6f8fb; border: 2px dashed #0d6efd; border-radius: 12px; padding: 25px; text-align: center; margin: 30px 0;">
                    <p style="color: #6b7280; margin: 0 0 10px 0; font-size: 14px; text-transform: uppercase; letter-spacing: 1px;">Tu código de verificación es:</p>
                    <div style="font-size: 36px; font-weight: bold; color: #0d6efd; letter-spacing: 8px; margin: 10px 0;">{code}</div>
                    <p style="color: #6b7280; margin: 10px 0 0 0; font-size: 14px;">Válido por 10 minutos</p>
                </div>
                
                <p style="color: #4b5563; margin: 25px 0; font-size: 16px; line-height: 1.6;">
                    Si no solicitaste este código, puedes ignorar este mensaje de forma segura.
                </p>
                
                <!-- Botón de acción -->
                <div style="text-align: center; margin: 30px 0;">
                    <a href="#" style="display: inline-block; background-color: #0d6efd; color: #ffffff; padding: 12px 30px; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 16px;">Ir a la plataforma</a>
                </div>
            </div>
            
            <!-- Pie de página -->
            <div style="background-color: #f6f8fb; padding: 30px; text-align: center; border-top: 1px solid #e5e7eb;">
                <p style="color: #6b7280; margin: 0 0 10px 0; font-size: 14px;">¿Necesitas ayuda? Contacta a nuestro soporte</p>
                <p style="color: #9ca3af; margin: 0; font-size: 12px;">
                    © 2024 Soluciones Informáticas. Todos los derechos reservados.
                </p>
            </div>
        </div>
    </body>
    </html>
    """
    
    # Versión en texto plano para clientes que no soportan HTML
    text_body = f"""Hola {user.first_name or user.email},

Gracias por registrarte en nuestro sistema. Tu código de verificación es: {code}

Este código es válido por 10 minutos.

Si no solicitaste este código, ignora este mensaje.

---
Soluciones Informáticas
    """
    
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = current_app.config.get("MAIL_USERNAME")
        msg["To"] = user.email
        msg.set_content(text_body)  # Texto plano
        msg.add_alternative(html_body, subtype="html")  # HTML
        
        with smtplib.SMTP(
            current_app.config.get("MAIL_SERVER"),
            current_app.config.get("MAIL_PORT")
        ) as smtp:
            smtp.starttls()
            smtp.login(
                current_app.config.get("MAIL_USERNAME"),
                current_app.config.get("MAIL_PASSWORD")
            )
            smtp.send_message(msg)
            
        print(f"[INFO] Email enviado exitosamente a {user.email}")
            
    except Exception as e:
        current_app.logger.error(f"Error enviando email a {user.email}: {e}", exc_info=True)
        
        if current_app.config.get("FLASK_ENV") == "development":
            print("\n" + "="*50)
            print(f"[DEV] Email falló, código para {user.email}: {code}")
            print("="*50 + "\n")
        
        raise ValueError(f"No se pudo enviar el email. Verifica tu configuración SMTP.")


@auth_bp.route('/verify-email/<token>')
def verify_email(token):
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
    user.account_status = "PENDIENTE_PAGO"
    t.used_count += 1
    db.session.commit()

    flash("Correo verificado ✅  Puedes continuar con el paso 3.", "success")
    return redirect(url_for("auth.upgrade"))




@auth_bp.route('/verify-code', methods=['POST'])
@login_required
def verify_code():
    code = (request.json or {}).get('code', '').strip()
    
    print(f"\n{'='*60}")
    print(f"[VERIFY] Usuario: {current_user.id} ({current_user.email})")
    print(f"[VERIFY] Código recibido: '{code}' (len={len(code)})")
    
    if not code or len(code) != 4 or not code.isdigit():
        print(f"[VERIFY] ❌ Validación falló")
        print("="*60 + "\n")
        return {"ok": False, "msg": "Código inválido"}, 400

    token = AuthToken.query.filter_by(
        user_id=current_user.id,
        purpose="verify_email",
        code=code,
        revoked=False
    ).first()
    
    if not token:
        print(f"[VERIFY] ❌ Código incorrecto")
        print("="*60 + "\n")
        return {"ok": False, "msg": "Código incorrecto"}, 400
    
    expires_at = _coerce_utc(token.expires_at)
    now = _utcnow()
    
    print(f"[VERIFY] Token.id: {token.id}")
    print(f"[VERIFY] Expira: {expires_at}")
    print(f"[VERIFY] Ahora: {now}")
    print(f"[VERIFY] Expirado: {now > expires_at}")
    
    if token.used_count >= token.max_uses:
        print(f"[VERIFY] ❌ Código ya utilizado")
        print("="*60 + "\n")
        return {"ok": False, "msg": "Código ya utilizado"}, 400
    
    if now > expires_at:
        print(f"[VERIFY] ❌ Código expirado")
        print("="*60 + "\n")
        return {"ok": False, "msg": "Código expirado (solicita uno nuevo)"}, 400

    current_user.is_email_verified = True
    current_user.email_verified_at = now
    current_user.account_status = "PENDIENTE_PAGO"
    token.used_count += 1
    db.session.commit()
    
    print(f"[VERIFY] ✅ Verificación exitosa")
    print("="*60 + "\n")
    return {"ok": True, "msg": "Correo verificado ✅"}



################ PARA RECUPERAR CONTRASEÑA

@auth_bp.route('/forgot-password', methods=['GET'])
def forgot_password():
    return render_template('forgot_password.html')

@auth_bp.route('/send-reset-code', methods=['POST'])
def send_reset_code():
    data = request.get_json()
    email = (data.get('email') or '').strip().lower()
    
    user = User.query.filter_by(email=email).first()
    if not user:
        return {"ok": False, "msg": "No existe una cuenta con ese correo"}, 404
    
    # Generar código
    code = f"{secrets.randbelow(10000):04d}"
    raw_token = secrets.token_urlsafe(32)
    
    token = AuthToken(
        user_id=user.id,
        purpose="reset_password",
        code=code,
        token_hash=_hash_token(raw_token),
        expires_at=_utcnow() + timedelta(minutes=10),
        max_uses=1,
        revoked=False,
    )
    db.session.add(token)
    db.session.commit()
    
    # Enviar email
    try:
        subject = "Recuperación de contraseña - ContaPro"
        
        # HTML del correo
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Recuperación de contraseña</title>
        </head>
        <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f6f8fb;">
            <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff;">
                <!-- Cabecera -->
                <div style="background-color: #0d6efd; padding: 30px 20px; text-align: center;">
                    <h1 style="color: #ffffff; margin: 0; font-size: 28px; font-weight: 600;">ContaPro</h1>
                    <p style="color: #e7f0ff; margin: 10px 0 0 0; font-size: 16px;">Soluciones Contables Inteligentes</p>
                </div>
                
                <!-- Contenido principal -->
                <div style="padding: 40px 30px;">
                    <h2 style="color: #0d6efd; margin: 0 0 20px 0; font-size: 24px;">¡Hola {user.first_name or user.username}!</h2>
                    <p style="color: #4b5563; margin: 0 0 25px 0; font-size: 16px; line-height: 1.6;">
                        Hemos recibido una solicitud para restablecer tu contraseña. Para continuar, usa el siguiente código de recuperación:
                    </p>
                    
                    <!-- Código de recuperación destacado -->
                    <div style="background-color: #f6f8fb; border: 2px dashed #0d6efd; border-radius: 12px; padding: 25px; text-align: center; margin: 30px 0;">
                        <p style="color: #6b7280; margin: 0 0 10px 0; font-size: 14px; text-transform: uppercase; letter-spacing: 1px;">Tu código de recuperación es:</p>
                        <div style="font-size: 36px; font-weight: bold; color: #0d6efd; letter-spacing: 8px; margin: 10px 0;">{code}</div>
                        <p style="color: #6b7280; margin: 10px 0 0 0; font-size: 14px;">Válido por 10 minutos</p>
                    </div>
                    
                    <div style="background-color: #fee2e2; border-left: 4px solid #dc2626; padding: 15px; margin: 25px 0; border-radius: 4px;">
                        <p style="color: #991b1b; margin: 0; font-size: 14px; font-weight: 500;">
                            <strong>Importante:</strong> Si no solicitaste restablecer tu contraseña, ignora este correo. Tu cuenta seguirá siendo segura.
                        </p>
                    </div>
                    
                    <!-- Botón de acción -->
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="#" style="display: inline-block; background-color: #0d6efd; color: #ffffff; padding: 12px 30px; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 16px;">Restablecer contraseña</a>
                    </div>
                </div>
                
                <!-- Pie de página -->
                <div style="background-color: #f6f8fb; padding: 30px; text-align: center; border-top: 1px solid #e5e7eb;">
                    <p style="color: #6b7280; margin: 0 0 10px 0; font-size: 14px;">¿Necesitas ayuda? Contacta a nuestro soporte</p>
                    <p style="color: #9ca3af; margin: 0; font-size: 12px;">
                        © 2024 Soluciones Informáticas. Todos los derechos reservados.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Versión en texto plano
        text_body = f"""Hola {user.first_name or user.username},

Hemos recibido una solicitud para restablecer tu contraseña. Tu código de recuperación es: {code}

Este código es válido por 10 minutos.

Si no solicitaste este código, ignora este mensaje.

---
Soluciones Informáticas
        """
        
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = current_app.config.get("MAIL_USERNAME")
        msg["To"] = user.email
        msg.set_content(text_body)  # Texto plano
        msg.add_alternative(html_body, subtype="html")  # HTML
        
        with smtplib.SMTP(
            current_app.config.get("MAIL_SERVER"),
            current_app.config.get("MAIL_PORT")
        ) as smtp:
            smtp.starttls()
            smtp.login(
                current_app.config.get("MAIL_USERNAME"),
                current_app.config.get("MAIL_PASSWORD")
            )
            smtp.send_message(msg)
        
        return {"ok": True, "msg": "Código enviado"}, 200
    except Exception as e:
        print(f"Error: {e}")
        return {"ok": False, "msg": "Error al enviar email"}, 500    



@auth_bp.route('/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json()
    email = (data.get('email') or '').strip().lower()
    code = data.get('code', '').strip()
    new_password = data.get('new_password', '').strip()
    
    user = User.query.filter_by(email=email).first()
    if not user:
        return {"ok": False, "msg": "Usuario no encontrado"}, 404
    
    token = AuthToken.query.filter_by(
        user_id=user.id,
        purpose="reset_password",
        code=code,
        revoked=False
    ).first()
    
    if not token:
        return {"ok": False, "msg": "Código incorrecto"}, 400
    
    if token.used_count >= token.max_uses:
        return {"ok": False, "msg": "Código ya utilizado"}, 400
    
    expires_at = _coerce_utc(token.expires_at)
    if _utcnow() > expires_at:
        return {"ok": False, "msg": "Código expirado"}, 400
    
    # Actualizar contraseña
    user.set_password(new_password)
    token.used_count += 1
    db.session.commit()
    
    return {"ok": True, "msg": "Contraseña actualizada"}, 200



