# proc_detracciones/routes/home.py
from flask import Blueprint, render_template
from flask_login import login_required

home_bp = Blueprint("home", __name__)

@home_bp.route("/")
@home_bp.route("/home")
@login_required
def index():
    """Dashboard principal - selector de servicios."""
    return render_template("home.html")