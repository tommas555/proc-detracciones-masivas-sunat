# proc_detracciones/commands.py
import sys
import csv
import json
import random
import click
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select, or_, and_

from .extensions import db
from .models import User
from .routes.auth import create_magic_link


# ───────────────────────── Helpers ─────────────────────────
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _fmt_lima(dt) -> str:
    if not dt:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("America/Lima")).strftime("%d/%m/%Y - %H:%M:%S")

def _find_user(ident: str):
    stmt = select(User).where(or_(User.email == ident, User.username == ident))
    return db.session.execute(stmt).scalar_one_or_none()

def _gen_username(prefix: str = "user", digits: int = 4) -> str:
    """Genera userNNNN con NNNN aleatorio, garantizando unicidad."""
    while True:
        uname = f"{prefix}{random.randint(10**(digits-1), 10**digits - 1)}"
        if _find_user(uname) is None:
            return uname

def _apply_quota_defaults(u: User, app):
    """Inicializa cuotas/contadores si están vacíos."""
    if getattr(u, "xml_quota", None) is None:
        u.xml_quota = int(app.config.get("TRIAL_XML_QUOTA", 40))
    if getattr(u, "runs_quota", None) is None:
        u.runs_quota = int(app.config.get("TRIAL_RUNS_QUOTA", 2))
    if getattr(u, "xml_used", None) is None:
        u.xml_used = 0
    if getattr(u, "runs_used", None) is None:
        u.runs_used = 0


# ───────────────────────── CLI ─────────────────────────
def register_cli(app):

    @app.cli.command("trial:invite")
    @click.argument("ident")  # email o username existente/nuevo
    def trial_invite(ident):
        """Genera un Magic Link (invite_trial) para el ident dado."""
        try:
            link = create_magic_link(ident, "invite_trial")
        except Exception as e:
            raise click.ClickException(str(e))

        # Normaliza URL absoluta si vino ruta relativa
        if link.startswith("/"):
            scheme = (app.config.get("PREFERRED_URL_SCHEME") or "http")
            server = (app.config.get("SERVER_NAME") or "127.0.0.1:5000")
            click.echo(f"{scheme}://{server}{link}")
        else:
            click.echo(link)

    @app.cli.command("trial:new")
    @click.option("--prefix", default="user", help="Prefijo del username (por defecto: user)")
    @click.option("--digits", default=4, type=int, help="Cantidad de dígitos numéricos")
    @click.option("--xml", type=int, default=None, help="XML quota (opcional)")
    @click.option("--runs", type=int, default=None, help="Runs quota (opcional)")
    def trial_new(prefix, digits, xml, runs):
        """
        Crea un usuario trial NUEVO (username aleatorio) y devuelve un Magic Link.
        Nota: el trial empieza al primer uso del link (no se fija aquí).
        """
        uname = _gen_username(prefix=prefix, digits=digits)

        # Crear usuario con cuotas/contadores; SIN trial_ends_at (se fija en magic_post)
        u = User(username=uname)
        _apply_quota_defaults(u, app)
        if xml is not None:  u.xml_quota = xml
        if runs is not None: u.runs_quota = runs
        db.session.add(u)
        db.session.commit()

        # Generar magic link
        try:
            link = create_magic_link(uname, "invite_trial")
        except Exception as e:
            raise click.ClickException(str(e))

        if link.startswith("/"):
            scheme = (app.config.get("PREFERRED_URL_SCHEME") or "http")
            server = (app.config.get("SERVER_NAME") or "127.0.0.1:5000")
            click.echo(f"username={uname}\n{scheme}://{server}{link}")
        else:
            click.echo(f"username={uname}\n{link}")

    @app.cli.command("trial:set")
    @click.argument("ident")
    @click.option("--seconds", type=int, default=None)
    @click.option("--minutes", type=int, default=None)
    @click.option("--hours", type=int, default=None)
    @click.option("--days", type=int, default=None)
    @click.option("--xml", type=int, default=None, help="XML quota")
    @click.option("--runs", type=int, default=None, help="Runs quota")
    def trial_set(ident, seconds, minutes, hours, days, xml, runs):
        """Ajusta cuotas/tiempo para un usuario concreto (fija trial_ends_at desde ahora)."""
        u = _find_user(ident)
        if not u:
            raise click.ClickException("Usuario no encontrado")

        if xml is not None:  u.xml_quota = xml
        if runs is not None: u.runs_quota = runs

        if any(v is not None for v in (seconds, minutes, hours, days)):
            s = seconds or 0
            m = minutes or 0
            h = hours or 0
            d = days or 0
            delta = timedelta(seconds=s, minutes=m, hours=h, days=d)
            if delta.total_seconds() <= 0:
                raise click.ClickException("Duración inválida (todo cero).")
            u.trial_ends_at = _utcnow() + delta

        db.session.commit()
        click.echo("OK")

    @app.cli.command("trial:expire")
    @click.argument("ident")
    def trial_expire(ident):
        """Marca el trial como vencido ahora mismo."""
        u = _find_user(ident)
        if not u:
            raise click.ClickException("Usuario no encontrado")
        u.trial_ends_at = _utcnow() - timedelta(seconds=1)
        db.session.commit()
        click.echo("Trial marcado como vencido")

    @app.cli.command("trial:reset-counters")
    @click.argument("ident")
    def trial_reset_counters(ident):
        """Resetea xml_used/runs_used a 0."""
        u = _find_user(ident)
        if not u:
            raise click.ClickException("Usuario no encontrado")
        u.xml_used = 0
        u.runs_used = 0
        db.session.commit()
        click.echo("Contadores reseteados")

    @app.cli.command("trial:show")
    @click.argument("ident")
    def trial_show(ident):
        """Muestra estado del trial para un usuario."""
        u = _find_user(ident)
        if not u:
            raise click.ClickException("Usuario no encontrado")
        now = _utcnow()
        click.echo(
            f"id={u.id}\n"
            f"username={u.username}\nemail={u.email}\n"
            f"xml={u.xml_used}/{u.xml_quota}\nruns={u.runs_used}/{u.runs_quota}\n"
            f"trial_ends_at={_fmt_lima(u.trial_ends_at)} (UTC: {u.trial_ends_at})\n"
            f"now={_fmt_lima(now)} (UTC: {now})"
        )

    # ───────── Listados / Exportación ─────────

    @app.cli.command("users:all")
    @click.option("--limit", default=100, type=int)
    def users_all(limit):
        """Lista usuarios (id, user, email, trial_ends_at, xml/runs)."""
        rows = db.session.execute(
            select(User).order_by(User.id.desc()).limit(limit)
        ).scalars().all()
        for u in rows:
            click.echo(
                f"id={u.id} user={u.username} email={u.email} "
                f"trial_ends_at={_fmt_lima(u.trial_ends_at)} "
                f"xml={u.xml_used}/{u.xml_quota} runs={u.runs_used}/{u.runs_quota}"
            )

    @app.cli.command("users:started")
    def users_started():
        """Usuarios que YA iniciaron la prueba (trial_ends_at NO nulo)."""
        rows = db.session.execute(
            select(User).where(User.trial_ends_at.isnot(None)).order_by(User.id.desc())
        ).scalars().all()
        for u in rows:
            click.echo(f"id={u.id} user={u.username} trial_ends_at={_fmt_lima(u.trial_ends_at)}")

    @app.cli.command("users:no-usage")
    def users_no_usage():
        """Usuarios que no han procesado nada (xml_used=0 y runs_used=0)."""
        rows = db.session.execute(
            select(User).where(and_(User.xml_used == 0, User.runs_used == 0)).order_by(User.id.desc())
        ).scalars().all()
        for u in rows:
            click.echo(f"id={u.id} user={u.username} xml={u.xml_used} runs={u.runs_used}")

    @app.cli.command("users:export")
    @click.option("--which", type=click.Choice(["all", "started", "no-usage"]), default="all",
                  help="all=todos, started=trial iniciado, no-usage=sin uso")
    @click.option("--limit", default=1000, type=int, help="Máximo de filas")
    @click.option("--format", "fmt", type=click.Choice(["table", "csv", "json"]), default="table")
    @click.option("--out", type=click.Path(writable=True), default="-",
                  help="'-' imprime en consola; si pasas ruta guarda a archivo")
    def users_export(which, limit, fmt, out):
        """Muestra/Exporta usuarios (tabla/csv/json)."""
        q = select(User).order_by(User.id.desc())
        if which == "started":
            q = q.where(User.trial_ends_at.isnot(None))
        elif which == "no-usage":
            q = q.where(and_(User.xml_used == 0, User.runs_used == 0))
        users = db.session.execute(q.limit(limit)).scalars().all()

        rows = [{
            "id": u.id,
            "username": u.username or "",
            "email": u.email or "",
            "trial_ends_at": _fmt_lima(getattr(u, "trial_ends_at", None)),
            "xml_used": getattr(u, "xml_used", 0),
            "xml_quota": getattr(u, "xml_quota", 0),
            "runs_used": getattr(u, "runs_used", 0),
            "runs_quota": getattr(u, "runs_quota", 0),
            "created_at": _fmt_lima(getattr(u, "created_at", None)),
        } for u in users]

        if fmt == "table":
            if not rows:
                click.echo("<< vacío >>")
                return
            headers = list(rows[0].keys())
            widths = {h: max(len(h), max(len(str(r[h])) for r in rows)) for h in headers}
            sep = "+" + "+".join("-" * (widths[h] + 2) for h in headers) + "+"
            click.echo(sep)
            click.echo("|" + "|".join(f" {h.ljust(widths[h])} " for h in headers) + "|")
            click.echo(sep)
            for r in rows:
                click.echo("|" + "|".join(f" {str(r[h]).ljust(widths[h])} " for h in headers) + "|")
            click.echo(sep)
            return

        stream = sys.stdout if out == "-" else open(out, "w", newline="", encoding="utf-8")
        try:
            if fmt == "csv":
                fieldnames = list(rows[0].keys()) if rows else [
                    "id","username","email","trial_ends_at",
                    "xml_used","xml_quota","runs_used","runs_quota","created_at"
                ]
                writer = csv.DictWriter(stream, fieldnames=fieldnames)
                writer.writeheader()
                for r in rows:
                    writer.writerow(r)
            elif fmt == "json":
                json.dump(rows, stream, ensure_ascii=False, indent=2)
        finally:
            if stream is not sys.stdout:
                stream.close()
        click.echo("OK" if out != "-" else "")
