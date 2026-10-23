from django.db import models
from django.contrib.auth.models import AbstractUser


class Usuario(AbstractUser):
    ROLES = (
        ('CO', 'Coordinador de Programa'),
        ('GC', 'Gestor del Conocimiento'),
        ('ES', 'Estudiante'),
        ('TI', 'Técnico de Soporte')
    )

    rol = models.CharField(max_length=2, choices=ROLES)

    groups = models.ManyToManyField(
        'auth.Group',
        verbose_name='groups',
        blank=True,
        help_text='The groups this user belongs to.',
        related_name="api_app_usuario_groups",
        related_query_name="api_app_usuario",
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        verbose_name='user permissions',
        blank=True,
        help_text='Specific permissions for this user.',
        related_name="api_app_usuario_permissions",
        related_query_name="api_app_usuario",
    )

    def __str__(self):
        return f"{self.get_full_name()} ({self.get_rol_display()})"


class Programa(models.Model):
    nombre = models.CharField(max_length=100)
    codigo = models.CharField(max_length=20, unique=True)
    coordinador = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        limit_choices_to={'rol': 'CO'},
    )

    def __str__(self):
        return self.nombre


class Asignatura(models.Model):
    codigo    = models.CharField(max_length=20, unique=True)
    nombre    = models.CharField(max_length=100)
    programa  = models.ForeignKey(Programa, on_delete=models.CASCADE, related_name='asignaturas')
    # ↓ blank=True permite que el campo quede vacío al crear la asignatura.
    #   Los gestores se asignan después al generar el horario.
    gestores  = models.ManyToManyField(
        Usuario,
        limit_choices_to={'rol': 'GC'},
        blank=True,                        # ← CAMBIO CLAVE
    )
    creditos  = models.PositiveSmallIntegerField()
    # Horas de clase por semana que debe llevar el núcleo (horas reloj).
    # 0 = "sin definir" (registros anteriores); el formulario exige >= 1.
    # El validador impide programar más horas que estas por semana.
    horas_semana = models.PositiveSmallIntegerField(default=0)
    # True si el núcleo debe dictarse en sala de cómputo.
    requiere_sala_computo = models.BooleanField(default=False)
    # ↓ Semestre al que pertenece el núcleo temático (ej. "1", "2", ... "10")
    semestre  = models.CharField(max_length=10, blank=True, default='')

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"


class Salon(models.Model):
    TIPOS = (
        ('AULA', 'Aula convencional'),
        ('COMP', 'Sala de cómputo'),
    )

    codigo    = models.CharField(max_length=20, unique=True)
    capacidad = models.PositiveSmallIntegerField()
    edificio  = models.CharField(max_length=50)
    tipo      = models.CharField(max_length=4, choices=TIPOS, default='AULA')

    def __str__(self):
        return self.codigo


class Horario(models.Model):
    DIAS_SEMANA = (
        ('LUN', 'Lunes'),
        ('MAR', 'Martes'),
        ('MIE', 'Miércoles'),
        ('JUE', 'Jueves'),
        ('VIE', 'Viernes'),
        # Sábado solo se admite para núcleos CAI (ver DIAS_REGULARES en settings).
        ('SAB', 'Sábado'),
    )

    asignatura  = models.ForeignKey(Asignatura, on_delete=models.CASCADE)
    salon       = models.ForeignKey(Salon, on_delete=models.CASCADE)
    gestor      = models.ForeignKey(
        Usuario,
        on_delete=models.CASCADE,
        limit_choices_to={'rol': 'GC'},
    )
    dia         = models.CharField(max_length=3, choices=DIAS_SEMANA)
    hora_inicio = models.TimeField()
    hora_fin    = models.TimeField()
    # Semestre al que pertenece este bloque (ej. "1", "2"...).
    # Se guarda aquí directamente para no depender del campo semestre
    # de Asignatura, que puede estar vacío.
    semestre    = models.CharField(max_length=10, blank=True, default='')

    class Meta:
        ordering = ['dia', 'hora_inicio']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(hora_fin__gt=models.F('hora_inicio')),
                name='hora_fin_mayor_hora_inicio',
            ),
        ]

    def __str__(self):
        return f"{self.asignatura} - {self.get_dia_display()} {self.hora_inicio}-{self.hora_fin}"


class Matricula(models.Model):
    estudiante = models.ForeignKey(
        Usuario,
        on_delete=models.CASCADE,
        limit_choices_to={'rol': 'ES'},
    )
    asignatura = models.ForeignKey(Asignatura, on_delete=models.CASCADE)
    semestre   = models.CharField(max_length=10)

    class Meta:
        unique_together = ('estudiante', 'asignatura')

    def __str__(self):
        return f"{self.estudiante} - {self.asignatura}"


class Notificacion(models.Model):
    TIPOS = (
        ('GEN', 'General'),
        ('ASI', 'Asignatura'),
        ('HOR', 'Horario'),
    )

    titulo      = models.CharField(max_length=100)
    mensaje     = models.TextField()
    tipo        = models.CharField(max_length=3, choices=TIPOS)
    emisor      = models.ForeignKey(
        Usuario,
        on_delete=models.CASCADE,
        related_name='notificaciones_enviadas',
    )
    fecha_envio = models.DateTimeField(auto_now_add=True)
    asignatura  = models.ForeignKey(Asignatura, on_delete=models.CASCADE, null=True, blank=True)
    horario     = models.ForeignKey(Horario, on_delete=models.CASCADE, null=True, blank=True)

    def __str__(self):
        return self.titulo


class NotificacionUsuario(models.Model):
    notificacion = models.ForeignKey(Notificacion, on_delete=models.CASCADE)
    usuario      = models.ForeignKey(
        Usuario,
        on_delete=models.CASCADE,
        related_name='notificaciones_recibidas',
    )
    leida       = models.BooleanField(default=False)
    fecha_leida = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('notificacion', 'usuario')

    def __str__(self):
        return f"{self.usuario} - {self.notificacion}"


class ConfiguracionUsuario(models.Model):
    usuario     = models.OneToOneField(
        Usuario,
        on_delete=models.CASCADE,
        related_name='configuracion',
    )
    tema_oscuro = models.BooleanField(default=False)

    def __str__(self):
        return f"Configuración de {self.usuario}"


# ─────────────────────────────────────────────────────────────────────────────
# AUDITORÍA
# ─────────────────────────────────────────────────────────────────────────────

class RegistroAuditoria(models.Model):
    """
    Registro de auditoría para rastrear creaciones, actualizaciones y
    eliminaciones en los modelos principales del sistema (Horario, Asignatura,
    Matricula, Usuario, etc.).

    Se alimenta automáticamente mediante la función de utilidad
    `api_app.utils.registrar_auditoria()`, que debe invocarse desde los
    ViewSets en los métodos create(), update() y destroy().

    Roles con acceso de lectura:
      - TI  (Técnico de Soporte): ve todos los registros.
      - CO  (Coordinador de Programa): ve los registros de su programa.
    """

    ACCIONES = (
        ('CREATE', 'Creación'),
        ('UPDATE', 'Actualización'),
        ('DELETE', 'Eliminación'),
        ('LOGIN',  'Inicio de sesión'),
        ('OTHER',  'Otro'),
    )

    # Quién realizó la acción (null si fue una operación del sistema)
    usuario       = models.ForeignKey(
        Usuario,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='auditoria',
    )
    # Qué tipo de acción
    accion        = models.CharField(max_length=10, choices=ACCIONES, default='OTHER')
    # Sobre qué modelo (ej. "Horario", "Asignatura")
    modelo        = models.CharField(max_length=50)
    # ID del objeto afectado
    objeto_id     = models.PositiveIntegerField(null=True, blank=True)
    # Descripción legible del cambio
    descripcion   = models.TextField(blank=True, default='')
    # Datos previos al cambio (JSON) — útil para comparación
    datos_antes   = models.JSONField(null=True, blank=True)
    # Datos posteriores al cambio (JSON)
    datos_despues = models.JSONField(null=True, blank=True)
    # IP desde la que se realizó la acción
    ip            = models.GenericIPAddressField(null=True, blank=True)
    # Cuándo ocurrió (indexado para consultas eficientes)
    fecha         = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-fecha']
        verbose_name = 'Registro de auditoría'
        verbose_name_plural = 'Registros de auditoría'
        indexes = [
            models.Index(fields=['-fecha']),
            models.Index(fields=['modelo', '-fecha']),
            models.Index(fields=['usuario', '-fecha']),
            models.Index(fields=['accion', '-fecha']),
        ]

    def __str__(self):
        user_str = str(self.usuario) if self.usuario else 'Sistema'
        return f"[{self.fecha:%Y-%m-%d %H:%M}] {user_str} – {self.get_accion_display()} en {self.modelo}"
