"""
Alcance por programa del rol CO (Coordinador de Programa).

La fuente de autorización es `Programa.coordinador` (FK a Usuario). La identidad
sale SIEMPRE del usuario autenticado (`request.user`, JWT): ningún identificador de
coordinador o programa enviado por el cliente se usa para decidir permisos.

  - CO: ve y modifica solo lo ligado a los programas que coordina.
  - TI / superusuario y demás roles: sin cambios (no se filtra).

Los salones y los gestores del conocimiento son recursos compartidos y NO se
filtran por programa; solo se oculta el detalle privado de clases de otros programas
cuando se informa un conflicto de disponibilidad.
"""
from rest_framework.exceptions import PermissionDenied


def rol_efectivo(user):
    """'TI' para superusuarios; el rol del usuario si no; None si no está autenticado."""
    if not (user and user.is_authenticated):
        return None
    return 'TI' if user.is_superuser else getattr(user, 'rol', None)


def es_coordinador(user) -> bool:
    return rol_efectivo(user) == 'CO'


def tiene_programa(user) -> bool:
    from api_app.models import Programa
    return Programa.objects.filter(coordinador=user).exists()


# ── Filtros de consulta (solo afectan al rol CO) ─────────────────────────────

def filtrar_programas(qs, user):
    return qs.filter(coordinador=user) if es_coordinador(user) else qs


def filtrar_asignaturas(qs, user):
    return qs.filter(programa__coordinador=user) if es_coordinador(user) else qs


def filtrar_horarios(qs, user):
    return qs.filter(asignatura__programa__coordinador=user) if es_coordinador(user) else qs


# ── Escritura: la asignatura debe pertenecer al programa del coordinador ─────

def exigir_asignatura_propia(user, asignatura):
    """Lanza 403 si un CO intenta usar una asignatura de un programa ajeno."""
    if not es_coordinador(user) or asignatura is None:
        return
    if not tiene_programa(user):
        raise PermissionDenied('Tu usuario no tiene un programa asignado.')
    if asignatura.programa.coordinador_id != user.pk:
        raise PermissionDenied('La asignatura seleccionada no pertenece a tu programa.')


# ── Privacidad en mensajes de conflicto ──────────────────────────────────────

def sanitizar_conflictos(conflictos, user):
    """
    Para un CO, oculta el detalle (nombre de la asignatura e id del horario) de las
    clases de OTROS programas que causan un conflicto. Conserva el hecho de que el
    recurso está ocupado y el intervalo. Para los demás roles devuelve lo mismo.
    """
    if not es_coordinador(user):
        return conflictos
    from api_app.models import Horario
    limpios = []
    for c in conflictos:
        hid = c.get('horario_id')
        if hid:
            h = Horario.objects.select_related('asignatura__programa').filter(pk=hid).first()
            if h is not None and h.asignatura.programa.coordinador_id != user.pk:
                c = dict(c)
                c['mensaje'] = c['mensaje'].replace(f' ({h.asignatura})', '')
                c['horario_id'] = None
        limpios.append(c)
    return limpios
