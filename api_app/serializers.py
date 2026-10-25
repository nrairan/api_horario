from rest_framework import serializers
from django.contrib.auth.hashers import make_password
from datetime import time, timedelta
from .models import (
    Usuario, Programa, Asignatura, Salon,
    Horario, Matricula, Notificacion,
    NotificacionUsuario, ConfiguracionUsuario,
    RegistroAuditoria,
)

# === Serializer para Usuario (Custom User) ===
class UsuarioSerializer(serializers.ModelSerializer):
    # Obligatoria al crear; al editar solo se cambia si se envía.
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = Usuario
        fields = [
            'id', 'username', 'email', 'password', 'first_name', 'last_name',
            'rol', 'groups', 'user_permissions'
        ]
        extra_kwargs = {
            'groups': {'required': False},
            'user_permissions': {'required': False}
        }

    def validate_password(self, value):
        if len(value) < 5:
            raise serializers.ValidationError("La contraseña debe tener al menos 5 caracteres.")
        if not any(char.isupper() for char in value):
            raise serializers.ValidationError("La contraseña debe contener al menos una letra mayúscula.")
        if not any(char.isdigit() for char in value):
            raise serializers.ValidationError("La contraseña debe contener al menos un número.")
        return value

    def validate_email(self, value):
        if not value.endswith('@ucundinamarca.edu.co'):
            raise serializers.ValidationError("Debe usar un correo institucional de la Universidad de Cundinamarca.")
        # El login busca por correo: no puede haber dos usuarios con el mismo.
        qs = Usuario.objects.filter(email__iexact=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Ya existe un usuario con ese correo.")
        return value

    def validate(self, data):
        if self.instance is None and not data.get('password'):
            raise serializers.ValidationError({'password': 'Este campo es obligatorio.'})

        # No se puede cambiar el rol de un usuario con registros ligados a su rol actual.
        nuevo = data.get('rol')
        if self.instance is not None and nuevo and nuevo != self.instance.rol:
            from api_app.services.dependencias import (
                dependencias_usuario, DEPENDENCIAS_POR_ROL, describir,
            )
            deps = dependencias_usuario(self.instance)
            bloqueantes = {k: v for k, v in deps.items()
                           if k in DEPENDENCIAS_POR_ROL.get(self.instance.rol, ())}
            if bloqueantes:
                raise serializers.ValidationError({
                    'rol': f'No se puede cambiar el rol: el usuario tiene {describir(bloqueantes)}.'
                })
        return data

    def create(self, validated_data):
        password = validated_data.pop('password')
        m2m = {k: validated_data.pop(k) for k in ('groups', 'user_permissions') if k in validated_data}
        user = Usuario.objects.create(**validated_data)
        user.set_password(password)
        user.save()
        for campo, valores in m2m.items():
            getattr(user, campo).set(valores)
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        instance = super().update(instance, validated_data)
        if password:  # hash, nunca texto plano
            instance.set_password(password)
            instance.save(update_fields=['password'])
        return instance


# === Serializer para Programa ===
class ProgramaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Programa
        fields = ['id', 'nombre', 'codigo', 'coordinador']

    def validate_codigo(self, value):
        if not value.isalnum():
            raise serializers.ValidationError("El código debe ser alfanumérico.")
        return value


# === Serializer para Asignatura ===
class AsignaturaSerializer(serializers.ModelSerializer):
    # ── CORRECCIONES ──────────────────────────────────────────────────────────
    # required=False  → no obliga a enviar gestores en el POST
    # allow_empty=True → acepta lista vacía []
    # Se asignan después al generar el horario desde crearHorario.dart
    gestores = serializers.PrimaryKeyRelatedField(
        queryset=Usuario.objects.filter(rol='GC'),
        many=True,
        required=False,
        allow_empty=True,
    )
    # semestre es opcional al crear la asignatura
    semestre = serializers.CharField(
        required=False,
        allow_blank=True,
        default='',
    )

    class Meta:
        model = Asignatura
        fields = [
            'id', 'codigo', 'nombre', 'programa', 'gestores', 'creditos', 'semestre',
            'horas_semana', 'requiere_sala_computo', 'horas_asignadas',
        ]

    # Horas ya programadas en Horario (solo lectura; la app muestra "asignadas de N")
    horas_asignadas = serializers.SerializerMethodField()

    def get_horas_asignadas(self, obj):
        minutos = sum(
            (h.hora_fin.hour * 60 + h.hora_fin.minute) - (h.hora_inicio.hour * 60 + h.hora_inicio.minute)
            for h in obj.horario_set.all()
        )
        return round(minutos / 60, 2)

    def validate_creditos(self, value):
        if value < 1 or value > 6:
            raise serializers.ValidationError("Los créditos deben estar entre 1 y 6.")
        return value

    def validate_horas_semana(self, value):
        if value > 40:
            raise serializers.ValidationError("Las horas por semana no pueden superar 40.")
        return value

    def validate(self, data):
        # Al CREAR un núcleo las horas semanales son obligatorias (>= 1).
        if self.instance is None and (data.get('horas_semana') or 0) < 1:
            raise serializers.ValidationError(
                {'horas_semana': 'Indica cuántas horas por semana lleva el núcleo (mínimo 1).'}
            )
        return data

    def create(self, validated_data):
        # ManyToMany no se puede pasar directo al create(), se maneja aparte
        gestores = validated_data.pop('gestores', [])
        asignatura = Asignatura.objects.create(**validated_data)
        asignatura.gestores.set(gestores)   # set([]) deja el campo vacío sin error
        return asignatura

    def update(self, instance, validated_data):
        gestores = validated_data.pop('gestores', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if gestores is not None:
            instance.gestores.set(gestores)
        return instance


# === Serializer para Salón ===
class SalonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Salon
        fields = ['id', 'codigo', 'capacidad', 'edificio', 'tipo']

    def validate_capacidad(self, value):
        if value < 10:
            raise serializers.ValidationError("La capacidad mínima es 10.")
        return value

    def validate(self, data):
        # Al editar un salón con horarios, no se puede dejar incompatible con ellos.
        if self.instance is not None:
            capacidad = data.get('capacidad', self.instance.capacidad)
            tipo = data.get('tipo', self.instance.tipo)
            problemas = []
            for h in Horario.objects.filter(salon=self.instance).select_related('asignatura'):
                a = h.asignatura
                inscritos = Matricula.objects.filter(asignatura=a).count()
                if inscritos > capacidad:
                    problemas.append(f'"{a.codigo}" tiene {inscritos} matriculados (capacidad {capacidad})')
                if a.requiere_sala_computo and tipo != 'COMP':
                    problemas.append(f'"{a.codigo}" requiere sala de cómputo')
            if problemas:
                raise serializers.ValidationError({
                    'salon': 'El cambio es incompatible con horarios existentes: '
                             + '; '.join(sorted(set(problemas))) + '.'
                })
        return data


# === Serializer para Horario (validaciones centralizadas en services/validacion_horarios.py) ===
class HorarioSerializer(serializers.ModelSerializer):
    # Soporte obligatorio de asignación y validación del salón
    salon = serializers.PrimaryKeyRelatedField(
        queryset=Salon.objects.all(),
        required=True,
        allow_null=False,
    )

    # semestre es opcional al crear (se llena desde Flutter al guardar)
    semestre = serializers.CharField(required=False, allow_blank=True, default='')

    class Meta:
        model = Horario
        fields = [
            'id', 'asignatura', 'salon', 'gestor', 'dia',
            'hora_inicio', 'hora_fin', 'semestre'
        ]

    def validate(self, data):
        """
        Delega todas las validaciones de negocio al servicio centralizado.
        Se ejecuta tanto en create() como en update() / partial_update().

        Durante una actualización, self.instance contiene el horario que
        se está editando y se excluye explícitamente mediante exclude(pk=self.instance.pk)
        para que el horario no colisione consigo mismo.
        """
        from api_app.services.validacion_horarios import evaluar_horario, semestre_efectivo

        # En actualizaciones (PUT/PATCH), los campos omitidos toman el valor
        # de la instancia existente para poder validar el bloque completo.
        if self.instance is not None:
            hora_inicio = data.get('hora_inicio', self.instance.hora_inicio)
            hora_fin    = data.get('hora_fin', self.instance.hora_fin)
            asignatura  = data.get('asignatura', self.instance.asignatura)
            gestor      = data.get('gestor', self.instance.gestor)
            dia         = data.get('dia', self.instance.dia)
            semestre    = data.get('semestre', self.instance.semestre)
            salon       = data.get('salon', self.instance.salon)
            excluir_id  = self.instance.pk
            instancia   = self.instance
        else:
            hora_inicio = data.get('hora_inicio')
            hora_fin    = data.get('hora_fin')
            asignatura  = data.get('asignatura')
            gestor      = data.get('gestor')
            dia         = data.get('dia')
            semestre    = data.get('semestre', '')
            salon       = data.get('salon')
            excluir_id  = None
            instancia   = None

        if salon is None:
            raise serializers.ValidationError({
                'salon': 'El campo salon es obligatorio y no puede ser nulo.'
            })

        # Alcance por programa: un CO solo puede programar asignaturas de su programa
        # (el salón y el gestor son recursos compartidos y no se restringen por programa).
        request = self.context.get('request')
        usuario = getattr(request, 'user', None)
        if usuario is not None:
            from api_app.services.alcance_programa import exigir_asignatura_propia
            exigir_asignatura_propia(usuario, asignatura)

        resultado = evaluar_horario(
            asignatura  = asignatura,
            salon       = salon,
            gestor      = gestor,
            dia         = dia,
            hora_inicio = hora_inicio,
            hora_fin    = hora_fin,
            semestre    = semestre,
            excluir_id  = excluir_id,
            instancia   = instancia,
        )

        # Solo las restricciones DURAS bloquean el guardado; las blandas
        # (almuerzo, ventanas, carga equilibrada) se consultan en /horarios/validar/.
        if resultado['errores']:
            from api_app.services.alcance_programa import sanitizar_conflictos
            raise serializers.ValidationError({
                'conflictos': sanitizar_conflictos(resultado['errores'], usuario),
            })

        # El semestre del bloque es el del núcleo cuando el cliente no lo envía.
        data['semestre'] = semestre_efectivo(asignatura, semestre)
        return data

    def create(self, validated_data):
        if 'salon' not in validated_data or validated_data.get('salon') is None:
            raise serializers.ValidationError({
                'salon': 'El campo salon es obligatorio y no puede ser nulo.'
            })
        return super().create(validated_data)


# === Serializer para Matrícula ===
class MatriculaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Matricula
        # ── CORRECCIÓN: se eliminó 'programa' porque ya no existe en el modelo
        fields = ['id', 'estudiante', 'asignatura', 'semestre']

    def validate(self, data):
        # Funciona para crear, PUT y PATCH (en PATCH faltan campos: se toman de la instancia).
        inst = self.instance
        estudiante = data.get('estudiante', getattr(inst, 'estudiante', None))
        asignatura = data.get('asignatura', getattr(inst, 'asignatura', None))
        semestre   = data.get('semestre',   getattr(inst, 'semestre', None))

        previas = Matricula.objects.all()
        if inst is not None:
            previas = previas.exclude(pk=inst.pk)   # no compararse consigo misma

        # Validación: Estudiante no puede repetir asignatura
        if previas.filter(estudiante=estudiante, asignatura=asignatura).exists():
            raise serializers.ValidationError("El estudiante ya está matriculado en esta asignatura.")

        # Validación: Límite de 8 asignaturas por semestre
        if previas.filter(estudiante=estudiante, semestre=semestre).count() >= 8:
            raise serializers.ValidationError("El estudiante no puede matricular más de 8 asignaturas por semestre.")

        return data


# === Serializer para Notificaciones ===
class NotificacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notificacion
        fields = [
            'id', 'titulo', 'mensaje', 'tipo', 'emisor',
            'fecha_envio', 'asignatura', 'horario'
        ]


# === Serializer para NotificacionesUsuario ===
class NotificacionUsuarioSerializer(serializers.ModelSerializer):
    notificacion = NotificacionSerializer(read_only=True)

    class Meta:
        model = NotificacionUsuario
        fields = ['id', 'notificacion', 'usuario', 'leida', 'fecha_leida']


# === Serializer para Configuración de Usuario ===
class ConfiguracionUsuarioSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConfiguracionUsuario
        fields = ['id', 'usuario', 'tema_oscuro']
        read_only_fields = ['usuario']

# === Serializer para Registro de Auditoría ===
class RegistroAuditoriaSerializer(serializers.ModelSerializer):
    usuario_nombre = serializers.SerializerMethodField()
    accion_display = serializers.CharField(source='get_accion_display', read_only=True)

    class Meta:
        model = RegistroAuditoria
        fields = [
            'id', 'fecha', 'usuario', 'usuario_nombre', 'accion',
            'accion_display', 'modelo', 'objeto_id', 'descripcion',
            'datos_antes', 'datos_despues', 'ip',
        ]
        read_only_fields = fields

    def get_usuario_nombre(self, obj):
        if obj.usuario:
            nombre = obj.usuario.get_full_name().strip()
            return nombre if nombre else obj.usuario.username
        return 'Sistema'
