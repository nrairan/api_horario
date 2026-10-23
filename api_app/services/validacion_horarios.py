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
        'MAX_HORAS_SEMANA_GESTOR': 40,     # Alcances y limitaciones del anteproyecto
        'GRUPO_MIN':      8,               # estudiantes por grupo (mínimo)
        'GRUPO_MAX':      35,              # estudiantes por grupo (máximo)
        'DURACION_MIN_H': 1,
        'DURACION_MAX_H': 3,
        'JORNADA_INICIO': 7,
        'JORNADA_FIN':    18,
        'ALMUERZO_INICIO': 12,             # franja protegida (restricción blanda)
        'ALMUERZO_FIN':    13,
        'VENTANA_MIN_AVISO_MIN': 60,       # ventana entre clases que se advierte
        'DIAS_REGULARES': ['LUN', 'MAR', 'MIE', 'JUE', 'VIE'],
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

    # Un salón es un recurso físico único: el cruce se verifica contra TODOS los
    # semestres/programas (el campo `semestre` es el semestre de la malla, no el periodo).
    qs = Horario.objects.filter(salon=salon, dia=dia)

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

    # Un gestor es una persona: no puede estar en dos semestres a la vez.
    qs = Horario.objects.filter(gestor=gestor, dia=dia)

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


# ─────────────────────────────────────────────────────────────────────────────
# Restricciones del documento (Alcances y Limitaciones, Marco Metodológico)
# ─────────────────────────────────────────────────────────────────────────────

def _err(tipo, mensaje, horario_id=None):
    return {'tipo': tipo, 'mensaje': mensaje, 'horario_id': horario_id}


def _nombre_gestor(gestor):
    return (gestor.get_full_name() or gestor.username) if gestor else ''


def _horas(minutos: int) -> str:
    h, m = divmod(int(minutos), 60)
    return f'{h} h' if m == 0 else f'{h} h {m} min'


def semestre_efectivo(asignatura, semestre: str = '') -> str:
    """Semestre del bloque: el enviado o, si viene vacío, el de la asignatura."""
    return (semestre or getattr(asignatura, 'semestre', '') or '').strip()


def validar_semestre_asignatura(asignatura, semestre: str) -> list:
    """El bloque debe quedar en el semestre al que pertenece el núcleo temático."""
    sem_asig = (getattr(asignatura, 'semestre', '') or '').strip()
    if asignatura is not None and sem_asig and semestre and sem_asig != str(semestre):
        return [_err(
            'SEMESTRE_NO_COINCIDE',
            f'El núcleo "{asignatura.codigo}" pertenece al semestre {sem_asig}, '
            f'no al semestre {semestre}.',
        )]
    return []


def validar_dia_regular(asignatura, dia: str) -> list:
    """Clases regulares: lunes a viernes. El sábado solo aplica a núcleos CAI."""
    permitidos = _cfg('DIAS_REGULARES', ['LUN', 'MAR', 'MIE', 'JUE', 'VIE'])
    if dia and dia not in permitidos and not es_asignatura_cai(asignatura):
        return [_err(
            'DIA_NO_PERMITIDO',
            'Las clases se programan de lunes a viernes. '
            'El sábado solo está permitido para núcleos CAI.',
        )]
    return []


def validar_tipo_salon(salon, asignatura) -> list:
    """Un núcleo que requiere sala de cómputo solo puede ir en una sala de cómputo."""
    if salon is None or asignatura is None:
        return []
    if getattr(asignatura, 'requiere_sala_computo', False) and salon.tipo != 'COMP':
        return [_err(
            'TIPO_AULA_INCORRECTO',
            f'El núcleo "{asignatura.codigo} - {asignatura.nombre}" requiere sala de '
            f'cómputo y el salón "{salon.codigo}" es "{salon.get_tipo_display()}".',
        )]
    return []


def _clases_del_grupo(asignatura, dia, semestre, excluir_id):
    """Bloques del mismo grupo (mismo programa + mismo semestre) en un día."""
    Horario = _get_horario_model()
    qs = Horario.objects.filter(
        dia=dia, semestre=semestre, asignatura__programa=asignatura.programa_id
    ).select_related('asignatura')
    if excluir_id is not None:
        qs = qs.exclude(pk=excluir_id)
    return qs


def validar_grupo_disponible(asignatura, dia, hora_inicio, hora_fin,
                             semestre, excluir_id=None) -> list:
    """
    Cruce de GRUPO: los estudiantes de un mismo semestre del programa no pueden
    tener dos núcleos temáticos en la misma franja.
    """
    if asignatura is None or not dia or hora_inicio is None or hora_fin is None or not semestre:
        return []
    conflictos = []
    for h in _clases_del_grupo(asignatura, dia, semestre, excluir_id):
        if _solapan(hora_inicio, hora_fin, h.hora_inicio, h.hora_fin):
            conflictos.append(_err(
                'GRUPO_OCUPADO',
                f'El semestre {semestre} ya tiene "{h.asignatura.nombre}" de '
                f'{h.hora_inicio.strftime("%H:%M")} a {h.hora_fin.strftime("%H:%M")} '
                f'ese día; los estudiantes no pueden cursar dos núcleos a la vez.',
                h.pk,
            ))
    return conflictos


def _minutos_asignados_asignatura(asignatura, excluir_id=None) -> int:
    Horario = _get_horario_model()
    qs = Horario.objects.filter(asignatura=asignatura)
    if excluir_id is not None:
        qs = qs.exclude(pk=excluir_id)
    return sum(_to_minutos(h.hora_fin) - _to_minutos(h.hora_inicio) for h in qs)


def validar_horas_asignatura(asignatura, hora_inicio, hora_fin, excluir_id=None) -> list:
    """
    No se pueden programar más horas semanales de las que define el núcleo
    (campo Asignatura.horas_semana). Si vale 0 ("sin definir") no se valida.
    """
    if asignatura is None or hora_inicio is None or hora_fin is None:
        return []
    horas_semana = getattr(asignatura, 'horas_semana', 0) or 0
    if horas_semana <= 0:
        return []
    ya = _minutos_asignados_asignatura(asignatura, excluir_id)
    nuevo = _to_minutos(hora_fin) - _to_minutos(hora_inicio)
    if ya + nuevo > horas_semana * 60:
        return [_err(
            'HORAS_ASIGNATURA_EXCEDIDAS',
            f'"{asignatura.nombre}" tiene {horas_semana} h por semana y ya tiene '
            f'{_horas(ya)} programadas; con este bloque serían {_horas(ya + nuevo)}.',
        )]
    return []


def _minutos_semana_gestor(gestor, excluir_id=None) -> int:
    Horario = _get_horario_model()
    qs = Horario.objects.filter(gestor=gestor)
    if excluir_id is not None:
        qs = qs.exclude(pk=excluir_id)
    return sum(_to_minutos(h.hora_fin) - _to_minutos(h.hora_inicio) for h in qs)


def validar_limite_horas_semana(gestor, hora_inicio, hora_fin, excluir_id=None) -> list:
    """Carga máxima semanal por gestor del conocimiento (40 h)."""
    if gestor is None or hora_inicio is None or hora_fin is None:
        return []
    maximo = _cfg('MAX_HORAS_SEMANA_GESTOR')
    ya = _minutos_semana_gestor(gestor, excluir_id)
    total = ya + (_to_minutos(hora_fin) - _to_minutos(hora_inicio))
    if total > maximo * 60:
        return [_err(
            'LIMITE_HORAS_SEMANA',
            f'El gestor "{_nombre_gestor(gestor)}" superaría el máximo de {maximo} h '
            f'semanales (tiene {_horas(ya)}; con este bloque: {_horas(total)}).',
        )]
    return []


def validar_capacidad_salon(salon, asignatura) -> list:
    """Los inscritos del núcleo no pueden superar la capacidad del salón."""
    if salon is None or asignatura is None:
        return []
    inscritos = _get_matricula_model().objects.filter(asignatura=asignatura).count()
    if inscritos > salon.capacidad:
        return [_err(
            'CAPACIDAD_INSUFICIENTE',
            f'El salón "{salon.codigo}" tiene capacidad para {salon.capacidad} '
            f'estudiante(s), pero "{asignatura.codigo}" tiene {inscritos} matriculado(s).',
        )]
    return []


# ── Restricciones BLANDAS: no impiden guardar, se informan como advertencias ──

def _advertencia_tamano_grupo(asignatura) -> list:
    inscritos = _get_matricula_model().objects.filter(asignatura=asignatura).count()
    gmin, gmax = _cfg('GRUPO_MIN'), _cfg('GRUPO_MAX')
    if inscritos == 0:
        return []
    if inscritos > gmax:
        return [_err('GRUPO_EXCEDE_MAXIMO',
                     f'Hay {inscritos} matriculados y el máximo por grupo es {gmax}. '
                     f'Conviene dividir el núcleo en dos grupos.')]
    if inscritos < gmin:
        return [_err('GRUPO_BAJO_MINIMO',
                     f'Hay solo {inscritos} matriculados y el mínimo por grupo es {gmin}.')]
    return []


def _advertencia_almuerzo(asignatura, hora_inicio, hora_fin) -> list:
    if es_asignatura_cai(asignatura):
        return []
    a_ini = time(_cfg('ALMUERZO_INICIO'), 0)
    a_fin = time(_cfg('ALMUERZO_FIN'), 0)
    if _solapan(hora_inicio, hora_fin, a_ini, a_fin):
        return [_err('ALMUERZO_INVADIDO',
                     f'La clase invade la franja de almuerzo '
                     f'({a_ini.strftime("%H:%M")}–{a_fin.strftime("%H:%M")}).')]
    return []


def _advertencias_ventanas_y_balance(asignatura, dia, hora_inicio, hora_fin,
                                      semestre, excluir_id) -> list:
    """Ventanas (tiempos muertos) del grupo en el día y carga equilibrada en la semana."""
    if asignatura is None or not semestre:
        return []
    avisos = []
    s, e = _to_minutos(hora_inicio), _to_minutos(hora_fin)
    al_ini, al_fin = _cfg('ALMUERZO_INICIO') * 60, _cfg('ALMUERZO_FIN') * 60
    umbral = _cfg('VENTANA_MIN_AVISO_MIN')

    def _gap(a, b):  # hueco [a, b) sin contar el almuerzo
        if b <= a:
            return 0
        solape_almuerzo = max(0, min(b, al_fin) - max(a, al_ini))
        return (b - a) - solape_almuerzo

    del_dia = list(_clases_del_grupo(asignatura, dia, semestre, excluir_id))
    previos = [_to_minutos(h.hora_fin) for h in del_dia if _to_minutos(h.hora_fin) <= s]
    siguientes = [_to_minutos(h.hora_inicio) for h in del_dia if _to_minutos(h.hora_inicio) >= e]
    if previos:
        g = _gap(max(previos), s)
        if g >= umbral:
            avisos.append(_err('VENTANA_HORARIA',
                               f'Quedaría una ventana de {_horas(g)} antes de esta clase.'))
    if siguientes:
        g = _gap(e, min(siguientes))
        if g >= umbral:
            avisos.append(_err('VENTANA_HORARIA',
                               f'Quedaría una ventana de {_horas(g)} después de esta clase.'))

    # Carga equilibrada entre los días de la semana (grupo = programa + semestre)
    Horario = _get_horario_model()
    qs = Horario.objects.filter(semestre=semestre, asignatura__programa=asignatura.programa_id)
    if excluir_id is not None:
        qs = qs.exclude(pk=excluir_id)
    por_dia = {d: 0 for d in _cfg('DIAS_REGULARES')}
    for h in qs:
        por_dia[h.dia] = por_dia.get(h.dia, 0) + _to_minutos(h.hora_fin) - _to_minutos(h.hora_inicio)
    por_dia[dia] = por_dia.get(dia, 0) + (e - s)
    total = sum(por_dia.values())
    promedio = total / max(len(por_dia), 1)
    if total >= 8 * 60 and por_dia[dia] > max(4 * 60, 2 * promedio):
        avisos.append(_err('CARGA_DESEQUILIBRADA',
                           f'El semestre {semestre} tendría {_horas(por_dia[dia])} de clase ese '
                           f'día frente a un promedio de {_horas(round(promedio))}.'))
    return avisos


# ─────────────────────────────────────────────────────────────────────────────
# Punto de entrada único
# ─────────────────────────────────────────────────────────────────────────────

def evaluar_horario(asignatura, salon, gestor, dia, hora_inicio, hora_fin,
                    semestre='', excluir_id=None, instancia=None) -> dict:
    """
    Evalúa un bloque y devuelve:
      {'errores': [...],        # restricciones DURAS: impiden guardar
       'advertencias': [...],   # restricciones BLANDAS: se informan, no bloquean
       'resumen': {...}}        # horas asignadas/pendientes para mostrar en la app
    """
    errores, avisos = [], []

    if excluir_id is None and instancia is not None:
        excluir_id = getattr(instancia, 'pk', None)
    excluir_id = _normalizar_id(excluir_id)

    if not semestre and instancia is not None:
        semestre = getattr(instancia, 'semestre', '') or ''
    semestre = semestre_efectivo(asignatura, semestre)

    es_cai = es_asignatura_cai(asignatura)

    # 1. Datos obligatorios
    if asignatura is None:
        errores.append(_err('ASIGNATURA_REQUERIDA', 'La asignatura es obligatoria.'))
    if salon is None:
        errores.append(_err('SALON_REQUERIDO',
                            'El campo salón es obligatorio y no puede ser nulo.'))
    errores_rol = validar_rol_gestor(gestor)
    errores.extend(errores_rol)
    gestor_ok = not errores_rol

    # 2. Hora válida (orden). Duración/jornada no se exigen a los CAI (horario fijo
    #    institucional, gestionado por Fusagasugá).
    if es_cai:
        if hora_inicio is None or hora_fin is None or hora_fin <= hora_inicio:
            errores.extend(validar_duracion_y_jornada(hora_inicio, hora_fin))
    else:
        errores.extend(validar_duracion_y_jornada(hora_inicio, hora_fin))

    if asignatura is None or any(e['tipo'] in ('HORA_INVALIDA', 'HORA_FIN_INVALIDA',
                                              'DURACION_INVALIDA', 'FUERA_DE_JORNADA')
                                 for e in errores):
        return {'errores': errores, 'advertencias': avisos, 'resumen': {}}

    # 3. Reglas de asignatura
    errores.extend(validar_semestre_asignatura(asignatura, semestre))
    errores.extend(validar_dia_regular(asignatura, dia))
    errores.extend(validar_cai(asignatura, dia, hora_inicio, hora_fin, instancia=instancia))
    errores.extend(validar_gestor_en_asignatura(gestor, asignatura))

    # 4. Cruces (gestor, salón y grupo) contra TODOS los semestres
    if salon is not None:
        errores.extend(validar_salon_disponible(salon, dia, hora_inicio, hora_fin,
                                                excluir_id=excluir_id))
    if gestor_ok:
        errores.extend(validar_gestor_disponible(gestor, dia, hora_inicio, hora_fin,
                                                 excluir_id=excluir_id))
    errores.extend(validar_grupo_disponible(asignatura, dia, hora_inicio, hora_fin,
                                            semestre, excluir_id))

    # 5. Recursos del salón
    if salon is not None:
        errores.extend(validar_tipo_salon(salon, asignatura))
        errores.extend(validar_capacidad_salon(salon, asignatura))

    # 6. Cargas (no aplican a CAI: su programación es institucional)
    if not es_cai:
        errores.extend(validar_horas_asignatura(asignatura, hora_inicio, hora_fin, excluir_id))
        if gestor_ok:
            errores.extend(validar_limite_clases_dia(gestor, dia, excluir_id=excluir_id))
            errores.extend(validar_limite_horas_dia(gestor, dia, hora_inicio, hora_fin,
                                                    excluir_id=excluir_id))
            errores.extend(validar_limite_horas_semana(gestor, hora_inicio, hora_fin, excluir_id))

    # 7. Restricciones blandas
    avisos.extend(_advertencia_tamano_grupo(asignatura))
    avisos.extend(_advertencia_almuerzo(asignatura, hora_inicio, hora_fin))
    avisos.extend(_advertencias_ventanas_y_balance(asignatura, dia, hora_inicio, hora_fin,
                                                   semestre, excluir_id))
    if (getattr(asignatura, 'horas_semana', 0) or 0) == 0 and not es_cai:
        avisos.append(_err('HORAS_NO_DEFINIDAS',
                           f'El núcleo "{asignatura.codigo}" no tiene horas por semana definidas; '
                           f'no se puede verificar el total semanal.'))

    # 8. Resumen para la interfaz
    nuevo = _to_minutos(hora_fin) - _to_minutos(hora_inicio)
    ya_asig = _minutos_asignados_asignatura(asignatura, excluir_id)
    resumen = {
        'horas_semana_asignatura': getattr(asignatura, 'horas_semana', 0) or 0,
        'horas_asignadas_asignatura': round((ya_asig + nuevo) / 60, 2),
        'horas_semana_gestor': round(
            (_minutos_semana_gestor(gestor, excluir_id) + nuevo) / 60, 2) if gestor_ok else None,
        'max_horas_semana_gestor': _cfg('MAX_HORAS_SEMANA_GESTOR'),
        'inscritos': _get_matricula_model().objects.filter(asignatura=asignatura).count(),
    }
    return {'errores': errores, 'advertencias': avisos, 'resumen': resumen}


def validar_horario(asignatura, salon, gestor, dia: str, hora_inicio: time,
                    hora_fin: time, semestre: str = '', excluir_id=None,
                    instancia=None) -> list:
    """Compatibilidad: devuelve solo las restricciones duras incumplidas."""
    return evaluar_horario(asignatura, salon, gestor, dia, hora_inicio, hora_fin,
                           semestre=semestre, excluir_id=excluir_id,
                           instancia=instancia)['errores']
