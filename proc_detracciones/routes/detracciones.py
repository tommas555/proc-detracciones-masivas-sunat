# proc_detracciones/routes/detracciones.py
import os
# proc_detracciones/routes/detracciones.py
import os
import io
import zipfile
from io import BytesIO
from decimal import Decimal
from datetime import datetime, timezone
from tempfile import TemporaryDirectory

from flask import Blueprint, request, send_file, render_template, make_response, current_app, redirect, url_for, flash
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from zoneinfo import ZoneInfo

from ..extensions import db
from ..services.procesador import run_pipeline
from ..models import User  # <-- ASEGÚRATE DE QUE ESTA LÍNEA ESTÉ PRESENTE

detracciones_bp = Blueprint("detracciones", __name__, url_prefix="/services/detracciones")

def _utcnow():
    return datetime.now(timezone.utc)

def _coerce_utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

def _get_tz():
    name = current_app.config.get("TRIAL_TZ", "America/Lima")
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc

def _fmt_local(dt):
    if dt is None:
        return "-"
    return dt.astimezone(_get_tz()).strftime('%Y-%m-%d %H:%M:%S %Z')

def _error_html(message: str, status: int = 400):
    html = f"<!doctype html><html><body><div>{message}</div></body></html>"
    return make_response(html, status, {"Content-Type": "text/html; charset=utf-8"})

def _count_xml_in_filestorage(fs) -> int:
    data = fs.read()
    fs.stream = io.BytesIO(data)
    name = (fs.filename or "").lower()
    if name.endswith(".zip"):
        n = 0
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for member in z.namelist():
                if member.lower().endswith(".xml"):
                    n += 1
        return n
    return 1

@detracciones_bp.get("/")
@login_required
def form():
    # Admins ven el formulario sin restricciones
    if current_user.role == 'admin':
        return render_template("detracciones.html", 
                             trial_expires_local=None,
                             quota_info=None,
                             runs_info=None,
                             active_sub=None)
    
    # Verificar acceso
    if current_user.account_status not in ['TRIAL_ACTIVO', 'ACTIVO']:
        flash("No tienes acceso a este servicio. Activa tu plan PRO.", "warning")
        return redirect(url_for('auth.upgrade'))
    
    # Obtener suscripción activa
    active_sub = current_user.get_active_subscription()
    
    # Obtener cuota del servicio de detracciones (ID=1)
    DETRACCIONES_SERVICE_ID = 1
    quota_info = current_user.get_current_quota(DETRACCIONES_SERVICE_ID)

    # Obtener info de runs (ejecuciones)
    runs_info = None
    if active_sub:
        from proc_detracciones.models import PlanServiceQuota, ServiceUsageLog
        
        plan_quota = PlanServiceQuota.query.filter_by(
            plan_id=active_sub.plan_id,
            service_id=DETRACCIONES_SERVICE_ID
        ).first()
        
        if plan_quota:
            # Contar ejecuciones SOLO desde que inició el plan actual
            runs_used = ServiceUsageLog.query.filter(
                ServiceUsageLog.user_id == current_user.id,
                ServiceUsageLog.service_id == DETRACCIONES_SERVICE_ID,
                ServiceUsageLog.completed_at >= active_sub.starts_at
            ).count()
            
            runs_info = {
                'quota': plan_quota.runs_quota,
                'used': runs_used,
                'remaining': plan_quota.runs_quota - runs_used if plan_quota.runs_quota != -1 else 'ilimitado',
                'is_unlimited': plan_quota.runs_quota == -1
            }
    
    # Calcular fecha de vencimiento
    trial_expires_local = None
    if active_sub and active_sub.ends_at:
        tzname = current_app.config.get("TRIAL_TZ", "America/Lima")
        try:
            tz = ZoneInfo(tzname)
        except Exception:
            tz = timezone.utc
        trial_expires_local = active_sub.ends_at.astimezone(tz).strftime('%d/%m/%Y a las %H:%M')
    
    return render_template("detracciones.html", 
                         trial_expires_local=trial_expires_local,
                         quota_info=quota_info,
                         runs_info=runs_info,
                         active_sub=active_sub)


@detracciones_bp.post("/")
@login_required
def process():
    is_admin = (current_user.role == 'admin')
    
    if not is_admin and current_user.account_status not in ['TRIAL_ACTIVO', 'ACTIVO']:
        return _error_html("No tienes acceso a este servicio. Activa tu plan PRO.", 403)
    
    files = request.files.getlist("files")
    lote = (request.form.get("lote") or "").strip()
    tipo_depositante = request.form.get("tipo_depositante", "adquiriente")
    download_token = request.form.get("download_token", "")

    if not files or not files[0].filename:
        return _error_html("❌ No se seleccionaron archivos.", 400)
    if not (lote.isdigit() and len(lote) == 6):
        return _error_html("❌ El lote debe tener 6 dígitos (ej: 250001).", 400)

    # --- LÓGICA DE VERIFICACIÓN DE CUOTAS NUEVA ---
    # Asumimos que el servicio de detracciones tiene ID=1
    DETRACCIONES_SERVICE_ID = 1
    total_xml_a_subir = 0

    try:
        for f in files:
            total_xml_a_subir += _count_xml_in_filestorage(f)
    except zipfile.BadZipFile:
        return _error_html("❌ El ZIP está dañado o no es válido.", 400)

    if not is_admin:
        from proc_detracciones.models import UserPlanSubscription, PlanServiceQuota
        
        # Obtener la cuota actual del usuario para el servicio de detracciones
        quota_info = current_user.get_current_quota(DETRACCIONES_SERVICE_ID)

        # Verificar si el plan es ilimitado
        if not quota_info['is_unlimited']:
            # 1. Verificar cuota de XML
            if quota_info['remaining'] < total_xml_a_subir:
                return _error_html(
                    f"⚠️ Límite de XML alcanzado. Usados: {quota_info['used']}, Cuota: {quota_info['quota']}. "
                    f"Intentaste subir {total_xml_a_subir} XML adicionales.",
                    403
                )
            
            # 2. Verificar cuota de ejecuciones (runs)
            active_sub = current_user.get_active_subscription()
            if active_sub:
                plan_quota = PlanServiceQuota.query.filter_by(
                    plan_id=active_sub.plan_id,
                    service_id=DETRACCIONES_SERVICE_ID
                ).first()
                
                if plan_quota and plan_quota.runs_quota != -1:
                    # Contar ejecuciones ya realizadas
                    from ..models import ServiceUsageLog
                    runs_used = ServiceUsageLog.query.filter_by(
                        user_id=current_user.id,
                        service_id=DETRACCIONES_SERVICE_ID
                    ).count()
                    
                    if runs_used >= plan_quota.runs_quota:
                        return _error_html(
                            f"⚠️ Límite de ejecuciones alcanzado. Has usado {runs_used} de {plan_quota.runs_quota} ejecuciones permitidas. "
                            f"Actualiza tu plan para continuar.",
                            403
                        )
            
        # TODO: Aquí podrías añadir una lógica similar para las 'runs_quota' si la necesitas en el futuro.
        # Por ahora, nos enfocamos solo en la cuota de XML.

    # --- FIN DE LA LÓGICA DE VERIFICACIÓN ---

    with TemporaryDirectory() as input_temp, TemporaryDirectory() as output_temp:
        for f in files:
            path = os.path.join(input_temp, secure_filename(f.filename))
            f.save(path)

        try:
            run_pipeline(
                input_dir=input_temp,
                output_dir=output_temp,
                lote=lote,
                min_monto=Decimal("700.00"),
                tipo_operacion_txt="01",
                enforce_code_whitelist=False,
                code_whitelist=set(),
                tipo_depositante=tipo_depositante
            )
        except Exception as e:
            return _error_html(f"❌ No se generó el TXT. Detalle: {str(e)}", 400)

        txt_files = [f for f in os.listdir(output_temp) if f.endswith(".txt")]
        csv_files = [f for f in os.listdir(output_temp) if f == "omitidos.csv"]
        if not txt_files:
            return _error_html("❌ No se generó ningún .txt. Revisa los XML y omitidos.csv.", 400)

        txt_name = txt_files[0]
        ruc = txt_name[1:12] if len(txt_name) >= 13 else "desconocido"
        zip_name = f"detracciones_{ruc}.zip"
        zip_path = os.path.join(output_temp, zip_name)

        with zipfile.ZipFile(zip_path, "w") as z:
            z.write(os.path.join(output_temp, txt_name), arcname=txt_name)
            if csv_files:
                z.write(os.path.join(output_temp, csv_files[0]), arcname=csv_files[0])

        # --- REGISTRO DEL USO ---
        # Ya no actualizamos campos en el User, sino que registramos el uso en la tabla de logs.
        if not is_admin:
            from ..models import ServiceUsageLog
            log = ServiceUsageLog(
                user_id=current_user.id,
                service_id=DETRACCIONES_SERVICE_ID,
                xml_processed=total_xml_a_subir,
                files_uploaded=len(files),
                lote_number=lote,
                ip_address=request.headers.get("X-Forwarded-For", request.remote_addr),
                user_agent=request.headers.get("User-Agent"),
                completed_at=_utcnow()
            )
            db.session.add(log)
            db.session.commit()
        # --- FIN DEL REGISTRO ---

        with open(zip_path, "rb") as fh:
            data = fh.read()

        resp = send_file(BytesIO(data), as_attachment=True, download_name=zip_name, mimetype="application/zip")
        if download_token:
            resp.set_cookie("fileDownloadToken", download_token, max_age=60, path="/")
        return resp