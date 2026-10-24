"""
Dependencias entre entidades administrativas.

Se usan para impedir eliminaciones (o cambios de rol) que dejarían datos
huérfanos o que, por `on_delete=CASCADE`, borrarían registros dependientes
sin una regla de negocio que lo justifique.
Cada función devuelve solo las dependencias con registros: {'etiqueta': cantidad}.
"""


def _solo_con_datos(d: dict) -> dict:
    return {k: v for k, v in d.items() if v}


def dependencias_usuario(u) -> dict:
    from api_app.models import Programa, Asignatura, Horario, Matricula, Notificacion
    return _solo_con_datos({
        'programas_coordinados':   Programa.objects.filter(coordinador=u).count(),
        'horarios_como_gestor':    Horario.objects.filter(gestor=u).count(),
        'asignaturas_como_gestor': Asignatura.objects.filter(gestores=u).count(),
        'matriculas':              Matricula.objects.filter(estudiante=u).count(),
        'notificaciones_enviadas': Notificacion.objects.filter(emisor=u).count(),
    })


# Qué dependencias hacen incompatible abandonar un rol
DEPENDENCIAS_POR_ROL = {
    'CO': ('programas_coordinados',),
    'GC': ('horarios_como_gestor', 'asignaturas_como_gestor'),
    'ES': ('matriculas',),
    'TI': ('notificaciones_enviadas',),
}


def dependencias_programa(p) -> dict:
    from api_app.models import Asignatura
    return _solo_con_datos({'asignaturas': Asignatura.objects.filter(programa=p).count()})


def dependencias_asignatura(a) -> dict:
    from api_app.models import Horario, Matricula, Notificacion
    return _solo_con_datos({
        'horarios':        Horario.objects.filter(asignatura=a).count(),
        'matriculas':      Matricula.objects.filter(asignatura=a).count(),
        'notificaciones':  Notificacion.objects.filter(asignatura=a).count(),
    })


def dependencias_salon(s) -> dict:
    from api_app.models import Horario
    return _solo_con_datos({'horarios': Horario.objects.filter(salon=s).count()})


def describir(dep: dict) -> str:
    return ', '.join(f'{n} {k.replace("_", " ")}' for k, n in dep.items())
