# proc_detracciones/routes/web.py
import os
import io
import zipfile
from io import BytesIO
from decimal import Decimal
from datetime import datetime, timezone
from tempfile import TemporaryDirectory

from flask import Blueprint, request, send_file, render_template, make_response, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from zoneinfo import ZoneInfo
from datetime import timezone


from zoneinfo import ZoneInfo

from ..extensions import db
from ..services.procesador import run_pipeline

web_bp = Blueprint("web", __name__)

# ---------------------- Helpers fecha/UTC & TZ ----------------------
def _utcnow():
    return datetime.now(timezone.utc)

def _coerce_utc(dt):
    """Convierte datetimes naive a UTC, asumiendo que ya estaban en UTC."""
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

# ---------------------- Helpers de UI ----------------------
def _error_html(message: str, status: int = 400):
    html = f"<!doctype html><html><body><div>{message}</div></body></html>"
    return make_response(html, status, {"Content-Type": "text/html; charset=utf-8"})

# ---------------------- Helpers de trial ----------------------
def _is_trial() -> bool:
    """Es trial si tiene fecha de fin (cuando convierta a cuenta real, pon None)."""
    return getattr(current_user, "trial_ends_at", None) is not None

def _trial_expired() -> bool:
    ends = _coerce_utc(getattr(current_user, "trial_ends_at", None))
    return bool(ends and _utcnow() > ends)

def _trial_expired_msg():
    ends = _coerce_utc(getattr(current_user, "trial_ends_at", None))
    return f"⚠️ Tu período de prueba venció el {_fmt_local(ends)}.", 403

def _get_quota_pair():
    """(xml_used, xml_quota, runs_used, runs_quota) como enteros."""
    xu = int(getattr(current_user, "xml_used", 0) or 0)
    xq = int(getattr(current_user, "xml_quota", 0) or 0)
    ru = int(getattr(current_user, "runs_used", 0) or 0)
    rq = int(getattr(current_user, "runs_quota", 0) or 0)
    return xu, xq, ru, rq

def _count_xml_in_filestorage(fs) -> int:
    """
    Cuenta XML válidos:
      - .xml suelto -> 1
      - .zip -> número de .xml dentro
    Reconstituye el stream para poder fs.save(...) después.
    """
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

# ---------------------- Rutas ----------------------
@web_bp.get("/")
@login_required
def form():
    ends = getattr(current_user, "trial_ends_at", None)
    if ends is not None and ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)

    tzname = current_app.config.get("TRIAL_TZ", "America/Lima")
    try:
        tz = ZoneInfo(tzname)
    except Exception:
        tz = timezone.utc

    # 👇 Formato Perú: dd/mm/aaaa HH:MM:SS
    trial_expires_local = (
        ends.astimezone(tz).strftime('%d/%m/%Y - %H:%M:%S (hora de Lima)') if ends else None
    )

    return render_template("web.html", trial_expires_local=trial_expires_local)


@web_bp.post("/")
@login_required
def process():
    """
    Sube archivos, ejecuta pipeline y devuelve ZIP.
    Reglas de trial:
      - Expira por fecha (mostrando hora local configurada)
      - Expira por cuotas (XML y runs), según TRIAL_ENDS_BY: "date" | "quota" | "both"
    """
    files = request.files.getlist("files")
    lote = (request.form.get("lote") or "").strip()
    tipo_depositante = request.form.get("tipo_depositante", "adquiriente")
    download_token = request.form.get("download_token", "")

    if not files or not files[0].filename:
        return _error_html("❌ No se seleccionaron archivos.", 400)
    if not (lote.isdigit() and len(lote) == 6):
        return _error_html("❌ El lote debe tener 6 dígitos (ej: 250001).", 400)

    # --------- Checks de TRIAL antes de procesar ----------
    mode = current_app.config.get("TRIAL_ENDS_BY", "both").lower()
    total_xml_a_subir = 0
    if _is_trial():
        # 1) Fecha primero (bloque fuerte)
        if mode in ("date", "both") and _trial_expired():
            msg, code = _trial_expired_msg()
            return _error_html(msg, code)

        # 2) Contar XML (puede tomar tiempo; re-chequeamos fecha luego)
        try:
            for f in files:
                total_xml_a_subir += _count_xml_in_filestorage(f)
        except zipfile.BadZipFile:
            return _error_html("❌ El ZIP está dañado o no es válido.", 400)

        # 3) Re-chequeo de fecha (por si expiró mientras contábamos)
        if mode in ("date", "both") and _trial_expired():
            msg, code = _trial_expired_msg()
            return _error_html(msg, code)

        # 4) Chequeo de cuotas si aplica
        if mode in ("quota", "both"):
            xml_used, xml_quota, runs_used, runs_quota = _get_quota_pair()
            if xml_quota <= 0 or (xml_used + total_xml_a_subir) > xml_quota:
                return _error_html(
                    f"⚠️ Límite de XML alcanzado ({xml_used}/{xml_quota}). "
                    f"Intentaste subir {total_xml_a_subir} XML adicionales.",
                    403
                )
            if runs_quota <= 0 or runs_used >= runs_quota:
                return _error_html(
                    f"⚠️ Límite de ejecuciones alcanzado ({runs_used}/{runs_quota}).",
                    403
                )

    # --------- Guardar archivos en un directorio temporal ----------
    with TemporaryDirectory() as input_temp, TemporaryDirectory() as output_temp:
        for f in files:
            path = os.path.join(input_temp, secure_filename(f.filename))
            f.save(path)

        # --------- Ejecutar pipeline ----------
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

        # --------- Hallar salidas ----------
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

        # --------- Ultimo re-chequeo de expiración antes de contar run ----------
        if _is_trial() and mode in ("date", "both") and _trial_expired():
            msg, code = _trial_expired_msg()
            return _error_html(msg, code)

        # --------- Actualizar cuotas (solo si trial y TODO OK) ----------
        if _is_trial() and mode in ("quota", "both"):
            xml_used, xml_quota, runs_used, runs_quota = _get_quota_pair()
            current_user.xml_used = xml_used + total_xml_a_subir
            current_user.runs_used = runs_used + 1
            db.session.commit()

        # --------- Responder ZIP ----------
        with open(zip_path, "rb") as fh:
            data = fh.read()

        resp = send_file(BytesIO(data), as_attachment=True, download_name=zip_name, mimetype="application/zip")
        if download_token:
            resp.set_cookie("fileDownloadToken", download_token, max_age=60, path="/")
        return resp
