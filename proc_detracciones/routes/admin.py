# proc_detracciones/routes/admin.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, jsonify
from flask_login import login_required, current_user
from functools import wraps
from datetime import datetime, timedelta, timezone
from sqlalchemy import or_
import io
import csv

from proc_detracciones.extensions import db
from proc_detracciones.models import User

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Acceso denegado', 'danger')
            return redirect(url_for('home.index'))
        return f(*args, **kwargs)
    return decorated_function

@admin_bp.route('/users')
@login_required
@admin_required
def users_list():
    from proc_detracciones.models import Plan # <-- IMPORTACIÓN LOCAL PARA MÁS EFICIENCIA
    
    status_filter = request.args.get('status', 'all')
    search_query = request.args.get('search', '').strip()
    
    query = User.query
    
    # Filtro por estado
    if status_filter != 'all':
        if status_filter == 'PENDIENTE_APROBACION':
            # Incluir tanto PENDIENTE_REVISION como PENDIENTE_PAGO
            from sqlalchemy import or_
            query = query.filter(or_(User.account_status == 'PENDIENTE_REVISION', 
                                     User.account_status == 'PENDIENTE_PAGO'))
        else:
            query = query.filter_by(account_status=status_filter)


    # Búsqueda por email, username, nombre o apellido
    if search_query:
        search_pattern = f"%{search_query}%"
        query = query.filter(
            or_(
                User.email.ilike(search_pattern),
                User.username.ilike(search_pattern),
                User.first_name.ilike(search_pattern),
                User.last_name.ilike(search_pattern)
            )
        )
    
    users = query.order_by(User.created_at.desc()).all()
    
    # Estadísticas
    pending_verification = User.query.filter_by(account_status='PENDIENTE_VERIFICACION').count()
    from sqlalchemy import or_

    pending_review = User.query.filter(
        or_(
            User.account_status == 'PENDIENTE_REVISION',
            User.account_status == 'PENDIENTE_PAGO'
        )
    ).count()
    trial_active = User.query.filter_by(account_status='TRIAL_ACTIVO').count()
    active = User.query.filter_by(account_status='ACTIVO').count()
    suspended = User.query.filter_by(account_status='SUSPENDIDO').count()
    
    # Obtener los planes activos para el modal
    plans = Plan.query.filter_by(is_active=True).all()
    
    return render_template('admin/users.html',
                      users=users,
                      pending_verification=pending_verification,
                      pending_review=pending_review,
                      trial_active=trial_active,
                      active=active,
                      suspended=suspended,
                      plans=plans,
                      search_query=search_query,
                      status_filter=status_filter)



@admin_bp.route('/user/<int:user_id>/approve', methods=['POST'])
@login_required
@admin_required
def approve_user(user_id):
    from proc_detracciones.models import UserPlanSubscription, Plan
    
    user = User.query.get_or_404(user_id)
    months = int(request.form.get('months', 1))
    plan_id = int(request.form.get('plan_id'))
    
    if months < 1:
        flash('Debes ingresar al menos 1 mes', 'warning')
        return redirect(url_for('admin.users_list'))
        
    plan = Plan.query.get_or_404(plan_id)
    now = datetime.now(timezone.utc)
    days = months * 30
    
    # ✅ DESACTIVAR SUSCRIPCIONES ANTERIORES
    UserPlanSubscription.query.filter_by(user_id=user.id, is_active=True).update({'is_active': False})
    db.session.flush()
    
    # Crear la nueva suscripción
    subscription = UserPlanSubscription(
        user_id=user.id,
        plan_id=plan_id,
        starts_at=now,
        ends_at=now + timedelta(days=days),
        is_active=True,
        auto_renew=False
    )
    db.session.add(subscription)
    
    # ✅ ACTUALIZAR ESTADO SEGÚN EL TIPO DE PLAN
    if plan.slug and 'trial' in plan.slug.lower():
        user.account_status = 'TRIAL_ACTIVO'
    else:
        user.account_status = 'ACTIVO'
    
    db.session.commit()
    
    end_date = subscription.ends_at.strftime('%d/%m/%Y %H:%M')
    flash(f'Usuario {user.email} activado con el plan "{plan.name}" por {months} mes(es). Vence: {end_date}', 'success')
    return redirect(url_for('admin.users_list'))




@admin_bp.route('/user/<int:user_id>/suspend', methods=['POST'])
@login_required
@admin_required
def suspend_user(user_id):
    user = User.query.get_or_404(user_id)
    user.account_status = 'SUSPENDIDO'
    db.session.commit()
    flash(f'Usuario {user.email} suspendido', 'warning')
    return redirect(url_for('admin.users_list'))

@admin_bp.route('/user/<int:user_id>/reactivate', methods=['POST'])
@login_required
@admin_required
def reactivate_user(user_id):
    user = User.query.get_or_404(user_id)
    
    # Verificar si tiene una suscripción activa válida
    active_sub = user.get_active_subscription()
    
    if active_sub:
        user.account_status = 'ACTIVO'
        flash(f'Usuario {user.email} reactivado', 'success')
    else:
        flash('El usuario no tiene suscripción válida. Usa "Activar Usuario" para asignar un plan.', 'warning')
    
    db.session.commit()
    return redirect(url_for('admin.users_list'))

@admin_bp.route('/user/<int:user_id>/set-pending', methods=['POST'])
@login_required
@admin_required
def set_pending(user_id):
    user = User.query.get_or_404(user_id)
    user.account_status = 'PENDIENTE_REVISION'
    db.session.commit()
    flash(f'Usuario {user.email} marcado como pendiente de revisión', 'info')
    return redirect(url_for('admin.users_list'))




@admin_bp.route('/user/<int:user_id>/extend', methods=['POST'])
@login_required
@admin_required
def extend_subscription(user_id):
    from proc_detracciones.models import UserPlanSubscription, Plan
    
    user = User.query.get_or_404(user_id)
    months = int(request.form.get('months', 0))
    plan_id = int(request.form.get('plan_id'))
    
    if months < 0:
        flash('Los meses no pueden ser negativos', 'warning')
        return redirect(url_for('admin.users_list'))
        
    plan = Plan.query.get_or_404(plan_id)
    now = datetime.now(timezone.utc)
    days = months * 30
    
    # Buscar suscripción activa
    active_sub = user.get_active_subscription()
    
    if active_sub and active_sub.plan_id == plan_id and months > 0:
        # Extender la suscripción existente del mismo plan
        active_sub.ends_at = active_sub.ends_at + timedelta(days=days)
        end_date = active_sub.ends_at
    else:
        # ✅ DESACTIVAR SUSCRIPCIONES ANTERIORES
        UserPlanSubscription.query.filter_by(user_id=user.id, is_active=True).update({'is_active': False})
        db.session.flush()
        
        # Crear una nueva suscripción con el plan diferente
        new_sub = UserPlanSubscription(
            user_id=user.id,
            plan_id=plan_id,
            starts_at=now,
            ends_at=now + timedelta(days=days) if days > 0 else (active_sub.ends_at if active_sub else now + timedelta(days=2)),
            is_active=True,
            auto_renew=False
        )
        db.session.add(new_sub)
        end_date = new_sub.ends_at
    
    # ✅ ACTUALIZAR ESTADO SEGÚN EL TIPO DE PLAN
    if plan.slug and 'trial' in plan.slug.lower():
        user.account_status = 'TRIAL_ACTIVO'
    else:
        user.account_status = 'ACTIVO'
    
    db.session.commit()
    
    flash(f'Suscripción actualizada por {months} mes(es) con el plan "{plan.name}". Vencimiento: {end_date.strftime("%d/%m/%Y %H:%M")}', 'success')
    return redirect(url_for('admin.users_list'))



@admin_bp.route('/export-users')
@login_required
@admin_required
def export_users():
    users = User.query.all()
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow([
        'ID', 'Username', 'Email', 'Nombre', 'Apellido', 
        'Estado', 'Rol', 'Meses Pagados', 'Inicio Suscripción', 
        'Vencimiento', 'Último Pago', 'Email Verificado', 'Fecha Registro'
    ])
    
    for user in users:
        active_sub = user.get_active_subscription()
        writer.writerow([
            user.id,
            user.username or '',
            user.email or '',
            user.first_name or '',
            user.last_name or '',
            user.account_status or '',
            user.role or '',
            active_sub.plan.name if active_sub else '',
            active_sub.starts_at.strftime('%d/%m/%Y %H:%M') if active_sub else '',
            active_sub.ends_at.strftime('%d/%m/%Y %H:%M') if active_sub else '',
            '',  # Ya no hay last_payment_date
            'Sí' if user.is_email_verified else 'No',
            user.created_at.strftime('%d/%m/%Y %H:%M') if user.created_at else ''
        ])
    
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'usuarios_{datetime.now().strftime("%Y%m%d_%H%M")}.csv'
    )


@admin_bp.route('/user/<int:user_id>/make-admin', methods=['POST'])
@login_required
@admin_required
def make_admin(user_id):
    user = User.query.get_or_404(user_id)
    
    if user.role == 'admin':
        flash(f'{user.email} ya es administrador', 'info')
    else:
        user.role = 'admin'
        db.session.commit()
        flash(f'{user.email} ahora es administrador', 'success')
    
    return redirect(url_for('admin.users_list'))

@admin_bp.route('/user/<int:user_id>/remove-admin', methods=['POST'])
@login_required
@admin_required
def remove_admin(user_id):
    user = User.query.get_or_404(user_id)
    
    admin_count = User.query.filter_by(role='admin').count()
    if admin_count <= 1:
        flash('No puedes quitar el rol admin al último administrador', 'danger')
        return redirect(url_for('admin.users_list'))
    
    user.role = 'user'
    db.session.commit()
    flash(f'{user.email} ya no es administrador', 'warning')
    return redirect(url_for('admin.users_list'))



@admin_bp.route('/user/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    from proc_detracciones.models import UserPlanSubscription, ServiceUsageLog
    
    user = User.query.get_or_404(user_id)
    
    # Evitar que el admin se elimine a sí mismo
    if user.id == current_user.id:
        flash('No puedes eliminar tu propia cuenta de administrador', 'danger')
        return redirect(url_for('admin.users_list'))
    
    # Evitar eliminar al último admin
    if user.role == 'admin':
        admin_count = User.query.filter_by(role='admin').count()
        if admin_count <= 1:
            flash('No puedes eliminar al último administrador del sistema', 'danger')
            return redirect(url_for('admin.users_list'))
    
    email = user.email
    
    try:
        # Eliminar suscripciones del usuario
        UserPlanSubscription.query.filter_by(user_id=user.id).delete()
        
        # Eliminar logs de uso (opcional, o puedes mantenerlos para auditoría)
        ServiceUsageLog.query.filter_by(user_id=user.id).delete()
        
        # Ahora sí eliminar al usuario
        db.session.delete(user)
        db.session.commit()
        
        flash(f'Usuario {email} y toda su información eliminados permanentemente', 'warning')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar usuario: {str(e)}', 'danger')
    
    return redirect(url_for('admin.users_list'))



@admin_bp.route('/plan/create', methods=['GET', 'POST'])
@login_required
@admin_required
def plan_create():
    from proc_detracciones.models import Plan, Service, PlanServiceQuota
    
    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            slug = request.form.get('slug', '').strip().lower()
            description = request.form.get('description', '').strip()
            is_unlimited = request.form.get('is_unlimited') == 'on'
            
            # Validar precio
            price_str = request.form.get('price_monthly', '0').strip()

           
            try:
                price_float = float(price_str)
                if price_float != int(price_float):
                    flash('El precio debe ser un número entero sin centavos. Ejemplo: 50, 100, 150', 'warning')
                    services = Service.query.filter_by(is_active=True).all()
                    return render_template('admin/plan_form.html', plan=None, services=services)
                price_monthly = int(price_float) * 100
            except ValueError:
                flash('El precio debe ser un número válido', 'danger')
                services = Service.query.filter_by(is_active=True).all()
                return render_template('admin/plan_form.html', plan=None, services=services)
            

            # ✅ AGREGAR ESTO
            # Leer trial_days (puede ser None)
            trial_days_str = request.form.get('trial_days', '').strip()
            trial_days = int(trial_days_str) if trial_days_str else None
            
            if not name or not slug:
                flash('Nombre y slug son obligatorios', 'danger')
                services = Service.query.filter_by(is_active=True).all()
                return render_template('admin/plan_form.html', plan=None, services=services)
            
            # Verificar que el slug no exista
            existing_plan = Plan.query.filter_by(slug=slug).first()
            if existing_plan:
                flash(f'Ya existe un plan con el slug "{slug}". Usa otro identificador.', 'warning')
                services = Service.query.filter_by(is_active=True).all()
                return render_template('admin/plan_form.html', plan=None, services=services)
            
            # Obtener servicios seleccionados
            selected_services = request.form.getlist('services')
            
            if not selected_services:
                flash('Debes seleccionar al menos un servicio', 'warning')
                services = Service.query.filter_by(is_active=True).all()
                return render_template('admin/plan_form.html', plan=None, services=services)
            
            # Crear plan
            plan = Plan(
                name=name,
                slug=slug,
                description=description,
                price_monthly=price_monthly,
                is_unlimited=is_unlimited,
                trial_days=trial_days,  # ✅ AGREGAR ESTA LÍNEA
                is_active=True
            )
            db.session.add(plan)
            db.session.flush()
            
            # Crear quotas solo para servicios seleccionados
            for service_id in selected_services:
                service_id = int(service_id)
                xml_quota = int(request.form.get(f'xml_quota_{service_id}', '-1'))
                runs_quota = int(request.form.get(f'runs_quota_{service_id}', '-1'))
                
                quota = PlanServiceQuota(
                    plan_id=plan.id,
                    service_id=service_id,
                    xml_quota=xml_quota,
                    runs_quota=runs_quota
                )
                db.session.add(quota)
            
            db.session.commit()
            flash(f'Plan "{name}" creado exitosamente', 'success')
            return redirect(url_for('admin.plans_list'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error al crear el plan: {str(e)}', 'danger')
            services = Service.query.filter_by(is_active=True).all()
            return render_template('admin/plan_form.html', plan=None, services=services)
    
    services = Service.query.filter_by(is_active=True).all()
    return render_template('admin/plan_form.html', plan=None, services=services)


@admin_bp.route('/plans')
@login_required
@admin_required
def plans_list():
    from proc_detracciones.models import Plan, Service
    plans = Plan.query.order_by(Plan.created_at.desc()).all()
    services = Service.query.filter_by(is_active=True).all()
    return render_template('admin/plans.html', plans=plans, services=services)




@admin_bp.route('/plan/<int:plan_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def plan_edit(plan_id):
    from proc_detracciones.models import Plan, Service, PlanServiceQuota
    
    plan = Plan.query.get_or_404(plan_id)
    
    if request.method == 'POST':
        try:
            plan.name = request.form.get('name', '').strip()
            plan.slug = request.form.get('slug', '').strip().lower()
            plan.description = request.form.get('description', '').strip()
            plan.is_unlimited = request.form.get('is_unlimited') == 'on'
            
            # Validar precio
            price_str = request.form.get('price_monthly', '0').strip()
            try:
                price_float = float(price_str)
                if price_float != int(price_float):
                    flash('El precio debe ser un número entero sin centavos. Ejemplo: 50, 100, 150', 'warning')
                    services = Service.query.filter_by(is_active=True).all()
                    return render_template('admin/plan_form.html', plan=plan, services=services)
                plan.price_monthly = int(price_float) * 100
            except ValueError:
                flash('El precio debe ser un número válido', 'danger')
                services = Service.query.filter_by(is_active=True).all()
                return render_template('admin/plan_form.html', plan=plan, services=services)
            
            # ✅ AGREGAR ESTO
            # Leer trial_days (puede ser None)
            trial_days_str = request.form.get('trial_days', '').strip()
            plan.trial_days = int(trial_days_str) if trial_days_str else None

            
            # Verificar que el slug no exista en otro plan
            existing_plan = Plan.query.filter(Plan.slug == plan.slug, Plan.id != plan.id).first()
            if existing_plan:
                flash(f'Ya existe otro plan con el slug "{plan.slug}". Usa otro identificador.', 'warning')
                services = Service.query.filter_by(is_active=True).all()
                return render_template('admin/plan_form.html', plan=plan, services=services)
            
            # Obtener servicios seleccionados
            selected_services = request.form.getlist('services')
            
            if not selected_services:
                flash('Debes seleccionar al menos un servicio', 'warning')
                services = Service.query.filter_by(is_active=True).all()
                return render_template('admin/plan_form.html', plan=plan, services=services)
            
            # Eliminar quotas de servicios no seleccionados
            PlanServiceQuota.query.filter_by(plan_id=plan.id).delete()
            
            # Crear/actualizar quotas solo para servicios seleccionados
            for service_id in selected_services:
                service_id = int(service_id)
                xml_quota = int(request.form.get(f'xml_quota_{service_id}', '-1'))
                runs_quota = int(request.form.get(f'runs_quota_{service_id}', '-1'))
                
                quota = PlanServiceQuota(
                    plan_id=plan.id,
                    service_id=service_id,
                    xml_quota=xml_quota,
                    runs_quota=runs_quota
                )
                db.session.add(quota)
            
            db.session.commit()
            flash(f'Plan "{plan.name}" actualizado', 'success')
            return redirect(url_for('admin.plans_list'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error al actualizar el plan: {str(e)}', 'danger')
            services = Service.query.filter_by(is_active=True).all()
            return render_template('admin/plan_form.html', plan=plan, services=services)
    
    services = Service.query.filter_by(is_active=True).all()
    return render_template('admin/plan_form.html', plan=plan, services=services)

@admin_bp.route('/plan/<int:plan_id>/delete', methods=['POST'])
@login_required
@admin_required
def plan_delete(plan_id):
    from proc_detracciones.models import Plan
    
    plan = Plan.query.get_or_404(plan_id)
    name = plan.name
    
    db.session.delete(plan)
    db.session.commit()
    
    flash(f'Plan "{name}" eliminado', 'warning')
    return redirect(url_for('admin.plans_list'))

# ==================== GESTIÓN DE SERVICIOS ====================

@admin_bp.route('/services')
@login_required
@admin_required
def services_list():
    from proc_detracciones.models import Service
    services = Service.query.order_by(Service.created_at.desc()).all()
    return render_template('admin/services.html', services=services)

@admin_bp.route('/service/create', methods=['GET', 'POST'])
@login_required
@admin_required
def service_create():
    from proc_detracciones.models import Service
    
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        slug = request.form.get('slug', '').strip().lower()
        description = request.form.get('description', '').strip()
        
        if not name or not slug:
            flash('Nombre y slug son obligatorios', 'danger')
            return redirect(url_for('admin.service_create'))
        
        service = Service(name=name, slug=slug, description=description, is_active=True)
        db.session.add(service)
        db.session.commit()
        
        flash(f'Servicio "{name}" creado', 'success')
        return redirect(url_for('admin.services_list'))
    
    return render_template('admin/service_form.html', service=None)

@admin_bp.route('/service/<int:service_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def service_edit(service_id):
    from proc_detracciones.models import Service
    
    service = Service.query.get_or_404(service_id)
    
    if request.method == 'POST':
        service.name = request.form.get('name', '').strip()
        service.slug = request.form.get('slug', '').strip().lower()
        service.description = request.form.get('description', '').strip()
        
        db.session.commit()
        flash(f'Servicio "{service.name}" actualizado', 'success')
        return redirect(url_for('admin.services_list'))
    
    return render_template('admin/service_form.html', service=service)


@admin_bp.route('/user/<int:user_id>/edit-subscription', methods=['POST'])
@login_required
@admin_required
def edit_subscription(user_id):
    from proc_detracciones.models import UserPlanSubscription, Plan
    
    user = User.query.get_or_404(user_id)
    months = int(request.form.get('months', 1))
    plan_id = int(request.form.get('plan_id'))
    
    if months < 0:
        flash('Los meses no pueden ser negativos', 'warning')
        return redirect(url_for('admin.users_list'))
        
    plan = Plan.query.get_or_404(plan_id)
    now = datetime.now(timezone.utc)
    
    # Para trial, si months=0, usar trial_days del plan
    if months == 0 and plan.trial_days:
        days = plan.trial_days
    else:
        days = months * 30 if months > 0 else 2  # Mínimo 2 días
    
    # ✅ DESACTIVAR TODAS las suscripciones anteriores
    UserPlanSubscription.query.filter_by(user_id=user.id, is_active=True).update({'is_active': False})
    db.session.flush()
    
    # ✅ CREAR nueva suscripción con fecha DESDE HOY
    new_sub = UserPlanSubscription(
        user_id=user.id,
        plan_id=plan_id,
        starts_at=now,
        ends_at=now + timedelta(days=days),
        is_active=True,
        auto_renew=False
    )
    db.session.add(new_sub)
    
    # ✅ ACTUALIZAR ESTADO SEGÚN EL TIPO DE PLAN
    if plan.slug and 'trial' in plan.slug.lower():
        user.account_status = 'TRIAL_ACTIVO'
    else:
        user.account_status = 'ACTIVO'
    
    db.session.commit()
    
    flash(f'Suscripción editada: {months} mes(es) con el plan "{plan.name}". Vencimiento: {new_sub.ends_at.strftime("%d/%m/%Y %H:%M")}', 'success')
    return redirect(url_for('admin.users_list'))


@admin_bp.route('/magic-links', methods=['GET'])
@login_required
@admin_required
def magic_links_list():
    """Página para generar y ver magic links"""
    from proc_detracciones.models import AuthToken
    
    # Obtener todos los magic links creados (últimos 50)
    tokens = AuthToken.query.filter_by(purpose='invite_trial').order_by(AuthToken.created_at.desc()).limit(50).all()

    now = datetime.now()  # ✅ AGREGAR ESTA LÍNEA
    
    return render_template('admin/magic_links.html', tokens=tokens, now=now)  # ✅ PASAR now



@admin_bp.route('/magic-links/generate', methods=['POST'])
@login_required
@admin_required
def generate_magic_link():
    """Genera un nuevo magic link"""
    from proc_detracciones.routes.auth import create_magic_link
    import secrets
    
    reference_note = request.form.get('reference_note', '').strip()
    
    # Generar username aleatorio
    username = f"user{secrets.randbelow(10000):04d}"
    
    # Verificar que no exista
    while User.query.filter_by(username=username).first():
        username = f"user{secrets.randbelow(10000):04d}"
    
    try:
        # Generar el magic link
        link = create_magic_link(username, purpose='invite_trial', created_by_admin_id=current_user.id)
        
        # Guardar la referencia Y el link en el token
        from proc_detracciones.models import AuthToken
        user = User.query.filter_by(username=username).first()
        if user:
            token = AuthToken.query.filter_by(user_id=user.id, purpose='invite_trial').order_by(AuthToken.created_at.desc()).first()
            if token:
                token.reference_note = reference_note
                token.magic_link_url = link  # ✅ GUARDAR EL LINK COMPLETO
                db.session.commit()
        
        flash(f'✅ Magic Link generado exitosamente para: {reference_note or username}', 'success')
        return redirect(url_for('admin.magic_links_list'))
    
    except Exception as e:
        flash(f'❌ Error al generar magic link: {str(e)}', 'danger')
        return redirect(url_for('admin.magic_links_list'))
    

@admin_bp.route('/magic-links/revoke/<int:token_id>', methods=['POST'])
@login_required
@admin_required
def revoke_magic_link(token_id):
    """Revocar un magic link"""
    from proc_detracciones.models import AuthToken
    
    token = AuthToken.query.get_or_404(token_id)
    
    if token.purpose != 'invite_trial':
        flash('❌ Solo se pueden revocar magic links de trial', 'danger')
        return redirect(url_for('admin.magic_links_list'))
    
    if token.revoked:
        flash('⚠️ Este magic link ya estaba revocado', 'warning')
        return redirect(url_for('admin.magic_links_list'))
    
    token.revoked = True
    db.session.commit()
    
    flash(f'✅ Magic link revocado exitosamente (Usuario: {token.user.username})', 'success')
    return redirect(url_for('admin.magic_links_list'))