"""
api_app/services/validacion_horarios.py
========================================
Servicio centralizado de validación de restricciones para el modelo Horario.

Puede invocarse desde:
  - HorarioSerializer (validaciones al crear/actualizar)
  - HorarioViewSet    (endpoint POST /api/horarios/validar/)

Devuelve siempre una lista de diccionarios con la forma:
  [
    {
      "tipo":       "SALON_OCUPADO",          # código identificable por Flutter
      "mensaje":    "El salón ya tiene …",   # texto legible para el usuario
      "horario_id": 15,                       # None si no aplica
    },
    ...
  ]

Una lista vacía significa que no hay conflictos.
"""

from datetime import time, timedelta
from django.conf import settings
from django.db import models as db_models


def _get_horario_model():
    from api_app.models import Horario
    return Horario


def _get_matricula_model():
    from api_app.models import Matricula
    return Matricula


def _cfg(key, default=None):
    """Devuelve un valor de HORARIO_CONFIG con seguridad."""
    cfg = getattr(settings, 'HORARIO_CONFIG', {})
    if key in cfg:
        return cfg[key]
    default_map = {
        'MAX_CLASES_DIA': 4,
        'MAX_HORAS_DIA':  8,
        'DURACION_MIN_H': 2,
        'DURACION_MAX_H': 3,
        'JORNADA_INICIO': 7,
        'JORNADA_FIN':    18,
        'CAI_ASIGNATURAS': [],
        'CAI_HORARIOS_PREDEFINIDOS': {},
    }
    return default_map.get(key, default)


def _to_minutos(t: time) -> int:
    """Convierte un objeto time a minutos desde medianoche."""
    return t.hour * 60 + t.minute


def _solapan(ini_a: time, fin_a: time, ini_b: time, fin_b: time) -> bool:
    """
    Devuelve True si dos intervalos [ini_a, fin_a) y [ini_b, fin_b) se solapan.
    Clases consecutivas (fin_a == ini_b) NO se consideran solapadas.
    """
    return _to_minutos(ini_a) < _to_minutos(fin_b) and \
           _to_minutos(fin_a) > _to_minutos(ini_b)


def _normalizar_id(val):
    """Convierte un valor de ID a entero o None."""
    if val is None or val == '':
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return val


# ─────────────────────────────────────────────────────────────────────────────
# Reglas CAI (Campos de Aprendizaje Institucional / Horarios predefinidos)
# ─────────────────────────────────────────────────────────────────────────────

def es_asignatura_cai(asignatura) -> bool:
    """
    Determina si una asignatura es o pertenece a un CAI (Campo de Aprendizaje Institucional)
    o núcleo temático con horarios predefinidos.
    """
    if not asignatura:
        return False

    if getattr(asignatura, 'es_cai', False):
        return True

    codigo = (getattr(asignatura, 'codigo', '') or '').strip().upper()
    nombre = (getattr(asignatura, 'nombre', '') or '').strip().upper()

    if 'CAI' in codigo:
        return True
    if 'CAI' in nombre or 'CAMPO DE APRENDIZAJE' in nombre:
        return True

    cai_lista = [c.strip().upper() for c in _cfg('CAI_ASIGNATURAS', []) if c]
    if codigo in cai_lista or nombre in cai_lista:
        return True

    cai_predefinidos = _cfg('CAI_HORARIOS_PREDEFINIDOS', {})
    for k in cai_predefinidos.keys():
        if k.strip().upper() in (codigo, nombre):
            return True

    return False


def obtener_horario_predefinido_cai(asignatura):
    """
    Devuelve dict con la franja horaria fija configurada para el CAI:
      {'dia': '...', 'hora_inicio': time(...), 'hora_fin': time(...)}
    o None si no tiene franja explícita en settings.
    """
    if not asignatura:
        return None

    codigo = (getattr(asignatura, 'codigo', '') or '').strip().upper()
    nombre = (getattr(asignatura, 'nombre', '') or '').strip().upper()
    cai_predefinidos = _cfg('CAI_HORARIOS_PREDEFINIDOS', {})

    for k, val in cai_predefinidos.items():
        k_upper = k.strip().upper()
        if k_upper == codigo or k_upper == nombre:
            return val

    return None


def validar_cai(asignatura, dia: str, hora_inicio: time, hora_fin: time, instancia=None) -> list:
    """
    Valida las restricciones especiales para asignaturas CAI:
    1. Si tiene horario predefinido institucional en configuración, la franja
       (día, hora_inicio, hora_fin) debe coincidir con dicha regla.
    2. Si es un CAI y se está editando una instancia ya guardada, sus horarios
       son fijos y no permiten modificaciones arbitrarias a la franja horaria.
    """
    if not es_asignatura_cai(asignatura):
        return []

    conflictos = []
    horario_fijo = obtener_horario_predefinido_cai(asignatura)

    # 1. Comprobación contra regla predefinida en configuración
    if horario_fijo:
        dia_esp = horario_fijo.get('dia')
        ini_esp = horario_fijo.get('hora_inicio')
        fin_esp = horario_fijo.get('hora_fin')

        if isinstance(ini_esp, str):
            ini_esp = time.fromisoformat(ini_esp)
        if isinstance(fin_esp, str):
            fin_esp = time.fromisoformat(fin_esp)

        if dia and dia_esp and dia != dia_esp:
            conflictos.append({
                'tipo': 'CAI_HORARIO_FIJO',
                'mensaje': (
                    f'La asignatura "{asignatura.codigo} - {asignatura.nombre}" es un CAI con '
                    f'horario predefinido fijo para el día {dia_esp}. No se permite asignar en {dia}.'
                ),
                'horario_id': getattr(instancia, 'pk', None) if instancia else None,
            })

        if hora_inicio and ini_esp and hora_inicio != ini_esp:
            conflictos.append({
                'tipo': 'CAI_HORARIO_FIJO',
                'mensaje': (
                    f'La asignatura "{asignatura.codigo} - {asignatura.nombre}" es un CAI con '
                    f'hora de inicio predefinida a las {ini_esp.strftime("%H:%M")}.'
                ),
                'horario_id': getattr(instancia, 'pk', None) if instancia else None,
            })

        if hora_fin and fin_esp and hora_fin != fin_esp:
            conflictos.append({
                'tipo': 'CAI_HORARIO_FIJO',
                'mensaje': (
                    f'La asignatura "{asignatura.codigo} - {asignatura.nombre}" es un CAI con '
                    f'hora de fin predefinida a las {fin_esp.strftime("%H:%M")}.'
                ),
                'horario_id': getattr(instancia, 'pk', None) if instancia else None,
            })

    # 2. Si se está editando un horario ya existente de un CAI, restringir cambios en la franja
    if instancia is not None and getattr(instancia, 'pk', None) and not horario_fijo:
        cambio_dia = dia and dia != instancia.dia
        cambio_ini = hora_inicio and hora_inicio != instancia.hora_inicio
        cambio_fin = hora_fin and hora_fin != instancia.hora_fin

        if cambio_dia or cambio_ini or cambio_fin:
            conflictos.append({
                'tipo': 'CAI_EDICION_NO_PERMITIDA',
                'mensaje': (
                    f'La asignatura "{asignatura.codigo} - {asignatura.nombre}" es un CAI con '
                    f'horario predefinido fijo ({instancia.get_dia_display()} '
                    f'{instancia.hora_inicio.strftime("%H:%M")}-{instancia.hora_fin.strftime("%H:%M")}). '
                    f'No se permiten modificaciones arbitrarias en su programación predefinida.'
                ),
                'horario_id': instancia.pk,
            })

    return conflictos


# ─────────────────────────────────────────────────────────────────────────────
# Validaciones atómicas
# ─────────────────────────────────────────────────────────────────────────────

def validar_duracion_y_jornada(hora_inicio: time, hora_fin: time) -> list:
    """Valida orden de horas, duración mínima/máxima y rango de jornada."""
    errores = []

    if hora_inicio is None or hora_fin is None:
        errores.append({
            'tipo': 'HORA_INVALIDA',
            'mensaje': 'Los campos hora_inicio y hora_fin son obligatorios.',
            'horario_id': None,
        })
        return errores

    if hora_fin <= hora_inicio:
        errores.append({
            'tipo': 'HORA_FIN_INVALIDA',
            'mensaje': 'La hora de fin debe ser posterior a la hora de inicio.',
            'horario_id': None,
        })
        return errores

    dur_min = _to_minutos(hora_fin) - _to_minutos(hora_inicio)
    dur_min_min = _cfg('DURACION_MIN_H') * 60
    dur_max_min = _cfg('DURACION_MAX_H') * 60

    if not (dur_min_min <= dur_min <= dur_max_min):
        errores.append({
            'tipo': 'DURACION_INVALIDA',
            'mensaje': (
                f'La clase debe durar entre {_cfg("DURACION_MIN_H")} y '
                f'{_cfg("DURACION_MAX_H")} horas. '
                f'Duración recibida: {dur_min // 60}h {dur_min % 60}min.'
            ),
            'horario_id': None,
        })

    jornada_inicio = time(_cfg('JORNADA_INICIO'), 0)
    jornada_fin    = time(_cfg('JORNADA_FIN'), 0)

    if hora_inicio < jornada_inicio or hora_fin > jornada_fin:
        errores.append({
            'tipo': 'FUERA_DE_JORNADA',
            'mensaje': (
                f'Las clases deben impartirse entre las '
                f'{_cfg("JORNADA_INICIO")}:00 y las {_cfg("JORNADA_FIN")}:00.'
            ),
            'horario_id': None,
        })

    return errores


def validar_rol_gestor(gestor) -> list:
    """Verifica que el usuario tenga rol GC."""
    from api_app.models import Usuario

    if gestor is None:
        return [{
            'tipo': 'GESTOR_REQUERIDO',
            'mensaje': 'El campo gestor es obligatorio.',
            'horario_id': None,
        }]

    if isinstance(gestor, (int, str)):
        try:
            gestor = Usuario.objects.get(pk=int(gestor))
        except (Usuario.DoesNotExist, ValueError):
            return [{
                'tipo': 'GESTOR_NO_EXISTE',
                'mensaje': f'No existe un usuario con id {gestor}.',
                'horario_id': None,
            }]

    if gestor.rol != 'GC':
        return [{
            'tipo': 'GESTOR_ROL_INVALIDO',
            'mensaje': (
                f'El usuario "{gestor.get_full_name() or gestor.username}" '
                f'tiene rol "{gestor.get_rol_display()}", no es Gestor del Conocimiento (GC).'
            ),
            'horario_id': None,
        }]

    return []


def validar_gestor_en_asignatura(gestor, asignatura) -> list:
    """Verifica relación gestor y asignatura si la asignatura ya tiene gestores asignados."""
    if asignatura is None or gestor is None:
        return []

    gestores_asignatura = asignatura.gestores.all()
    if gestores_asignatura.exists() and not gestores_asignatura.filter(pk=gestor.pk).exists():
        return [{
            'tipo': 'GESTOR_NO_PERMITIDO',
            'mensaje': (
                f'El gestor "{gestor.get_full_name() or gestor.username}" '
                f'no está en la lista de gestores de la asignatura '
                f'"{asignatura.codigo} - {asignatura.nombre}".'
            ),
            'horario_id': None,
        }]

    return []


def validar_salon_disponible(salon, dia: str, hora_inicio: time,
                              hora_fin: time, excluir_id=None,
                              semestre: str = '') -> list:
    """
    Verifica que el salón esté libre en el día y horario solicitado.
    Excluye explícitamente el horario actual en edición (excluir_id).
    Filtra por semestre cuando se provee, evitando falsos conflictos con
    registros antiguos de otros periodos académicos.
    """
    if salon is None:
        return [{
            'tipo': 'SALON_REQUERIDO',
            'mensaje': 'El campo salón es obligatorio y no puede ser nulo.',
            'horario_id': None,
        }]

    if hora_inicio is None or hora_fin is None or not dia:
        return []

    Horario = _get_horario_model()
    excluir_id = _normalizar_id(excluir_id)

    qs = Horario.objects.filter(salon=salon, dia=dia)

    if semestre:
        qs = qs.filter(semestre=semestre)

    if excluir_id is not None:
        qs = qs.exclude(pk=excluir_id)

    conflictos = []
    for h in qs:
        if _solapan(hora_inicio, hora_fin, h.hora_inicio, h.hora_fin):
            conflictos.append({
                'tipo': 'SALON_OCUPADO',
                'mensaje': (
                    f'El salón "{salon.codigo}" ya tiene una clase asignada '
                    f'de {h.hora_inicio.strftime("%H:%M")} a '
                    f'{h.hora_fin.strftime("%H:%M")} ese día '
                    f'({h.asignatura}).'
                ),
                'horario_id': h.pk,
            })

    return conflictos


def validar_gestor_disponible(gestor, dia: str, hora_inicio: time,
                               hora_fin: time, excluir_id=None,
                               semestre: str = '') -> list:
    """
    Verifica que el gestor no tenga otra clase simultánea ese día.
    Excluye explícitamente el horario actual en edición (excluir_id).
    Filtra por semestre si se provee.
    """
    if gestor is None or dia is None or hora_inicio is None or hora_fin is None:
        return []

    Horario = _get_horario_model()
    excluir_id = _normalizar_id(excluir_id)

    qs = Horario.objects.filter(gestor=gestor, dia=dia)

    if semestre:
        qs = qs.filter(semestre=semestre)

    if excluir_id is not None:
        qs = qs.exclude(pk=excluir_id)

    conflictos = []
    for h in qs:
        if _solapan(hora_inicio, hora_fin, h.hora_inicio, h.hora_fin):
            conflictos.append({
                'tipo': 'GESTOR_OCUPADO',
                'mensaje': (
                    f'El gestor "{gestor.get_full_name() or gestor.username}" '
                    f'ya tiene una clase de {h.hora_inicio.strftime("%H:%M")} '
                    f'a {h.hora_fin.strftime("%H:%M")} ese día '
                    f'({h.asignatura}).'
                ),
                'horario_id': h.pk,
            })

    return conflictos


def validar_limite_clases_dia(gestor, dia: str, excluir_id=None,
                               semestre: str = '') -> list:
    """Verifica que el gestor no supere el máximo de clases en el día."""
    if gestor is None or dia is None:
        return []

    Horario = _get_horario_model()
    max_clases = _cfg('MAX_CLASES_DIA')
    excluir_id = _normalizar_id(excluir_id)

    qs = Horario.objects.filter(gestor=gestor, dia=dia)

    if semestre:
        qs = qs.filter(semestre=semestre)

    if excluir_id is not None:
        qs = qs.exclude(pk=excluir_id)

    if qs.count() >= max_clases:
        return [{
            'tipo': 'LIMITE_CLASES_DIA',
            'mensaje': (
                f'El gestor ya tiene {qs.count()} clase(s) el día "{dia}", '
                f'el máximo permitido es {max_clases}.'
            ),
            'horario_id': None,
        }]

    return []


def validar_limite_horas_dia(gestor, dia: str, hora_inicio: time,
                              hora_fin: time, excluir_id=None,
                              semestre: str = '') -> list:
    """Verifica que las horas sumadas del gestor no superen MAX_HORAS_DIA."""
    if gestor is None or dia is None or hora_inicio is None or hora_fin is None:
        return []

    Horario = _get_horario_model()
    max_horas = _cfg('MAX_HORAS_DIA')
    max_min = max_horas * 60
    excluir_id = _normalizar_id(excluir_id)

    qs = Horario.objects.filter(gestor=gestor, dia=dia)

    if semestre:
        qs = qs.filter(semestre=semestre)

    if excluir_id is not None:
        qs = qs.exclude(pk=excluir_id)

    minutos_existentes = sum(
        _to_minutos(h.hora_fin) - _to_minutos(h.hora_inicio)
        for h in qs
    )
    minutos_nuevo = _to_minutos(hora_fin) - _to_minutos(hora_inicio)
    total = minutos_existentes + minutos_nuevo

    if total > max_min:
        total_h = total // 60
        total_m = total % 60
        return [{
            'tipo': 'LIMITE_HORAS_DIA',
            'mensaje': (
                f'El gestor superaría el máximo de {max_horas} horas diarias. '
                f'Total resultante: {total_h}h {total_m}min.'
            ),
            'horario_id': None,
        }]

    return []


def validar_capacidad_salon(salon, asignatura) -> list:
    """Verifica que el salón tenga capacidad suficiente para las matrículas."""
    if salon is None or asignatura is None:
        return []

    Matricula = _get_matricula_model()
    num_estudiantes = Matricula.objects.filter(asignatura=asignatura).count()

    if num_estudiantes == 0:
        return []

    if num_estudiantes > salon.capacidad:
        return [{
            'tipo': 'CAPACIDAD_INSUFICIENTE',
            'mensaje': (
                f'El salón "{salon.codigo}" tiene capacidad para {salon.capacidad} '
                f'estudiante(s), pero la asignatura "{asignatura.codigo}" tiene '
                f'{num_estudiantes} matrícula(s).'
            ),
            'horario_id': None,
        }]

    return []


# ─────────────────────────────────────────────────────────────────────────────
# Punto de entrada único
# ─────────────────────────────────────────────────────────────────────────────

def validar_horario(
    asignatura,
    salon,
    gestor,
    dia: str,
    hora_inicio: time,
    hora_fin: time,
    semestre: str = '',
    excluir_id=None,
    instancia=None,
) -> list:
    """
    Ejecuta todas las validaciones de negocio para un bloque de horario.
    Garantiza la exclusión correcta del horario actual al actualizar (excluir_id/instancia)
    y la comprobación estricta de presencia y disponibilidad del salón y CAI.
    """
    conflictos = []

    # Resolver excluir_id a partir de la instancia si no fue explícito
    if excluir_id is None and instancia is not None:
        excluir_id = getattr(instancia, 'pk', None)

    excluir_id = _normalizar_id(excluir_id)

    # Si no se pasó semestre pero la instancia existe, heredar su semestre
    if not semestre and instancia is not None:
        semestre = getattr(instancia, 'semestre', '') or ''

    # 1. Validación estricta de presencia y no nulidad del salón
    if salon is None:
        conflictos.append({
            'tipo': 'SALON_REQUERIDO',
            'mensaje': 'El campo salón es obligatorio y no puede ser nulo.',
            'horario_id': None,
        })

    # 2. Duración y jornada
    errores_tiempo = validar_duracion_y_jornada(hora_inicio, hora_fin)
    conflictos.extend(errores_tiempo)

    # 3. Rol del gestor
    conflictos.extend(validar_rol_gestor(gestor))

    # Si hay errores críticos en los datos de entrada, detener antes de consultar cruces
    if errores_tiempo:
        return conflictos

    # 4. Validaciones especiales CAI (horarios fijos y restricción de edición)
    conflictos.extend(
        validar_cai(asignatura, dia, hora_inicio, hora_fin, instancia=instancia)
    )

    # 5. Gestor permitido en asignatura
    conflictos.extend(validar_gestor_en_asignatura(gestor, asignatura))

    # 6. Salón disponible (sin solapamientos en el mismo día/semestre, excluyendo instancia actual)
    if salon is not None:
        conflictos.extend(
            validar_salon_disponible(
                salon, dia, hora_inicio, hora_fin,
                excluir_id=excluir_id, semestre=semestre
            )
        )

    # 7. Gestor disponible (sin cruces de horario ese día/semestre)
    conflictos.extend(
        validar_gestor_disponible(
            gestor, dia, hora_inicio, hora_fin,
            excluir_id=excluir_id, semestre=semestre
        )
    )

    # 8. Límite de clases por día
    conflictos.extend(
        validar_limite_clases_dia(
            gestor, dia, excluir_id=excluir_id, semestre=semestre
        )
    )

    # 9. Límite de horas por día
    conflictos.extend(
        validar_limite_horas_dia(
            gestor, dia, hora_inicio, hora_fin,
            excluir_id=excluir_id, semestre=semestre
        )
    )

    # 10. Capacidad del salón
    if salon is not None:
        conflictos.extend(validar_capacidad_salon(salon, asignatura))

    return conflictos
