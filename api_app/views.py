# api_app/views.py

from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from datetime import time
from .models import (
    Usuario, Programa, Asignatura, Salon,
    Horario, Matricula, Notificacion,
    NotificacionUsuario, ConfiguracionUsuario,
    RegistroAuditoria,
)
from .serializers import (
    UsuarioSerializer, ProgramaSerializer, AsignaturaSerializer,
    SalonSerializer, HorarioSerializer, MatriculaSerializer,
    NotificacionSerializer, NotificacionUsuarioSerializer,
    ConfiguracionUsuarioSerializer, RegistroAuditoriaSerializer,
)
from django.contrib.auth import authenticate
from api_app.utils import registrar_auditoria
from api_app.permissions import IsTIOrReadOnly, MatriculaPermission
from api_app.services.dependencias import (
    dependencias_usuario, dependencias_programa, dependencias_asignatura,
    dependencias_salon, describir,
)


class AdminCRUDMixin:
    """
    Comportamiento común de las entidades administrativas:
      - auditoría de CREATE / UPDATE / DELETE (quién, cuándo, antes/después);
      - DELETE bloqueado con 409 si hay registros dependientes (no se borra en cascada);
    Cada ViewSet define `modelo_auditoria` y, si aplica, `dependencias(obj)`.
    """
    modelo_auditoria = ''

    def dependencias(self, obj) -> dict:
        return {}

    def _etiqueta(self, obj):
        return str(obj)

    @transaction.atomic
    def perform_create(self, serializer):
        obj = serializer.save()
        registrar_auditoria(
            request=self.request, accion='CREATE', modelo=self.modelo_auditoria,
            objeto_id=obj.pk, descripcion=f'{self.modelo_auditoria} creado: {self._etiqueta(obj)}',
            datos_despues=dict(self.get_serializer(obj).data),
        )

    @transaction.atomic
    def perform_update(self, serializer):
        antes = dict(self.get_serializer(serializer.instance).data)
        obj = serializer.save()
        registrar_auditoria(
            request=self.request, accion='UPDATE', modelo=self.modelo_auditoria,
            objeto_id=obj.pk, descripcion=f'{self.modelo_auditoria} actualizado: {self._etiqueta(obj)}',
            datos_antes=antes, datos_despues=dict(self.get_serializer(obj).data),
        )

    def _bloqueo(self, obj, request):
        """Devuelve un Response 409 si la eliminación no es segura; si no, None."""
        dep = self.dependencias(obj)
        if dep:
            return Response({
                'error': f'No se puede eliminar: tiene registros asociados ({describir(dep)}).',
                'codigo': 'ELIMINACION_BLOQUEADA',
                'dependencias': dep,
            }, status=status.HTTP_409_CONFLICT)
        return None

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        bloqueo = self._bloqueo(obj, request)
        if bloqueo is not None:
            return bloqueo
        antes, obj_id, etiqueta = dict(self.get_serializer(obj).data), obj.pk, self._etiqueta(obj)
        self.perform_destroy(obj)
        registrar_auditoria(
            request=request, accion='DELETE', modelo=self.modelo_auditoria,
            objeto_id=obj_id, descripcion=f'{self.modelo_auditoria} eliminado: {etiqueta}',
            datos_antes=antes,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────
# PERMISOS PERSONALIZADOS
# ─────────────────────────────────────────────

class IsCoordinador(permissions.BasePermission):
    def has_permission(self, request, view):
        return hasattr(request.user, 'rol') and request.user.rol == 'CO'

class IsGestor(permissions.BasePermission):
    def has_permission(self, request, view):
        return hasattr(request.user, 'rol') and request.user.rol == 'GC'

class IsEstudiante(permissions.BasePermission):
    def has_permission(self, request, view):
        return hasattr(request.user, 'rol') and request.user.rol == 'ES'


# ─────────────────────────────────────────────
# USUARIOS
# ─────────────────────────────────────────────

class UsuarioViewSet(AdminCRUDMixin, viewsets.ModelViewSet):
    serializer_class = UsuarioSerializer
    permission_classes = [IsTIOrReadOnly]
    modelo_auditoria = 'Usuario'

    def get_queryset(self):
        queryset = Usuario.objects.all().order_by('id')
        rol = self.request.query_params.get('rol')
        if rol:
            queryset = queryset.filter(rol=rol)
        return queryset

    def dependencias(self, obj):
        return dependencias_usuario(obj)

    def _bloqueo(self, obj, request):
        if obj.pk == request.user.pk:
            return Response({
                'error': 'No puedes eliminar tu propia cuenta.',
                'codigo': 'ELIMINACION_BLOQUEADA',
                'dependencias': {},
            }, status=status.HTTP_409_CONFLICT)
        return super()._bloqueo(obj, request)


# ─────────────────────────────────────────────
# PROGRAMAS
# ─────────────────────────────────────────────

class ProgramaViewSet(AdminCRUDMixin, viewsets.ModelViewSet):
    queryset = Programa.objects.all().order_by('id')
    serializer_class = ProgramaSerializer
    permission_classes = [IsTIOrReadOnly]
    modelo_auditoria = 'Programa'

    def dependencias(self, obj):
        return dependencias_programa(obj)


# ─────────────────────────────────────────────
# ASIGNATURAS
# ─────────────────────────────────────────────

class AsignaturaViewSet(AdminCRUDMixin, viewsets.ModelViewSet):
    serializer_class = AsignaturaSerializer
    permission_classes = [IsTIOrReadOnly]
    modelo_auditoria = 'Asignatura'

    def dependencias(self, obj):
        return dependencias_asignatura(obj)

    def get_queryset(self):
        queryset = Asignatura.objects.all().order_by('id')
        programa_id = self.request.query_params.get('programa')
        semestre    = self.request.query_params.get('semestre')
        if programa_id:
            queryset = queryset.filter(programa_id=programa_id)
        if semestre:
            queryset = queryset.filter(semestre=semestre)
        return queryset


# ─────────────────────────────────────────────
# SALONES
# ─────────────────────────────────────────────

class SalonViewSet(AdminCRUDMixin, viewsets.ModelViewSet):
    queryset = Salon.objects.all().order_by('id')
    serializer_class = SalonSerializer
    permission_classes = [IsTIOrReadOnly]
    modelo_auditoria = 'Salon'

    def dependencias(self, obj):
        return dependencias_salon(obj)


# ─────────────────────────────────────────────
# HORARIOS
# ─────────────────────────────────────────────

class HorarioViewSet(viewsets.ModelViewSet):
    serializer_class = HorarioSerializer
    # ── AllowAny mientras no tengas autenticación por token en Flutter.
    #    Cuando implementes login con token, cambia a:
    #    permission_classes = [permissions.IsAuthenticated, IsCoordinador | IsGestor]
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = Horario.objects.all()
        dia        = self.request.query_params.get('dia')
        gestor     = self.request.query_params.get('gestor')
        asignatura = self.request.query_params.get('asignatura')
        if dia:
            queryset = queryset.filter(dia=dia)
        if gestor:
            queryset = queryset.filter(gestor_id=gestor)
        if asignatura:
            queryset = queryset.filter(asignatura_id=asignatura)
        return queryset

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """
        Crea un horario aplicando todas las validaciones del servicio centralizado.

        Las validaciones de duración, jornada, salón, gestor y límites diarios
        se ejecutan en HorarioSerializer.validate() mediante el servicio
        api_app/services/validacion_horarios.py.

        Se usa @transaction.atomic para garantizar que dos solicitudes simultáneas
        no puedan guardar horarios incompatibles: si la validación pasa pero otro
        proceso ya escribió un conflicto, la transacción falla de forma atómica.
        """
        response = super().create(request, *args, **kwargs)
        registrar_auditoria(
            request       = request,
            accion        = 'CREATE',
            modelo        = 'Horario',
            objeto_id     = response.data.get('id'),
            descripcion   = f"Horario creado (asignatura={request.data.get('asignatura')}, dia={request.data.get('dia')})",
            datos_despues = dict(response.data),
        )
        return response

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        """
        Actualiza un horario con las mismas validaciones que create().
        El serializer excluye el horario actual de las consultas de conflictos
        para que no colisione consigo mismo.
        """
        instancia_antes = self.get_object()
        datos_antes = HorarioSerializer(instancia_antes).data
        response = super().update(request, *args, **kwargs)
        registrar_auditoria(
            request       = request,
            accion        = 'UPDATE',
            modelo        = 'Horario',
            objeto_id     = response.data.get('id'),
            descripcion   = f"Horario actualizado (id={response.data.get('id')})",
            datos_antes   = dict(datos_antes),
            datos_despues = dict(response.data),
        )
        return response

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        """PATCH con validaciones completas (igual que update)."""
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        """Elimina un horario y registra la acción en la auditoría."""
        instancia = self.get_object()
        datos_antes = HorarioSerializer(instancia).data
        obj_id = instancia.id
        response = super().destroy(request, *args, **kwargs)
        registrar_auditoria(
            request       = request,
            accion        = 'DELETE',
            modelo        = 'Horario',
            objeto_id     = obj_id,
            descripcion   = f"Horario eliminado (id={obj_id})",
            datos_antes   = dict(datos_antes),
        )
        return response

    # ── Endpoint extra: GET /api/horarios/por_semestre/?semestre=1
    @action(detail=False, methods=['get'], url_path='por_semestre')
    def por_semestre(self, request):
        semestre = request.query_params.get('semestre')
        if not semestre:
            return Response(
                {"error": "Parámetro 'semestre' requerido."},
                status=status.HTTP_400_BAD_REQUEST
            )
        # Filtra por el campo semestre del propio modelo Horario
        # (más confiable que depender del semestre en Asignatura)
        horarios = Horario.objects.filter(
            semestre=semestre
        ).select_related('asignatura', 'gestor', 'salon')

        # Agrupar por día para devolver estructura conveniente
        resultado = {dia: [] for dia in ['LUN', 'MAR', 'MIE', 'JUE', 'VIE', 'SAB']}
        for h in horarios:
            resultado[h.dia].append({
                "id":            h.id,
                "asignatura":    h.asignatura.nombre,
                "asignatura_id": h.asignatura.id,
                "gestor":        f"{h.gestor.first_name} {h.gestor.last_name}".strip() or h.gestor.username,
                "gestor_id":     h.gestor.id,
                "salon":         h.salon.codigo if h.salon else None,
                "salon_id":      h.salon_id,
                "hora_inicio":   str(h.hora_inicio),
                "hora_fin":      str(h.hora_fin),
            })
        return Response(resultado)

    # ── Endpoint de consulta previa: POST /api/horarios/validar/
    @action(detail=False, methods=['post'], url_path='validar')
    def validar(self, request):
        """
        Evalúa si un bloque de horario sería válido SIN guardarlo en la base
        de datos. Útil para que Flutter verifique conflictos antes de confirmar.

        Cuerpo de la solicitud (JSON):
        {
          "asignatura":  <id>,
          "salon":       <id>,
          "gestor":      <id>,
          "dia":         "LUN" | "MAR" | "MIE" | "JUE" | "VIE",
          "hora_inicio": "HH:MM:SS",
          "hora_fin":    "HH:MM:SS",
          "semestre":    "<opcional>",
          "horario_id":  <id_opcional>  // para excluir el horario en edición
        }

        Respuesta exitosa (200):
        {
          "valido": true,
          "conflictos": []
        }

        Respuesta con conflictos (200):
        {
          "valido": false,
          "conflictos": [
            {
              "tipo":       "SALON_OCUPADO",
              "mensaje":    "El salón ya tiene una clase asignada en ese intervalo.",
              "horario_id": 15
            },
            ...
          ]
        }

        Respuesta con datos inválidos (400):
        {
          "error": "Descripción del error de formato"
        }

        Nota: esta consulta NO sustituye las validaciones definitivas que
        ocurren al crear o actualizar un horario.
        """
        from api_app.services.validacion_horarios import evaluar_horario

        data = request.data

        # ── Resolver referencias a objetos desde IDs ──────────────────────────
        try:
            asignatura_id = data.get('asignatura')
            salon_id      = data.get('salon')
            gestor_id     = data.get('gestor')
            dia           = data.get('dia', '')
            semestre      = data.get('semestre', '')
            horario_id    = data.get('horario_id') or data.get('excluir_id')  # para excluir al editar

            horario_instancia = Horario.objects.filter(pk=horario_id).first() if horario_id else None

            asignatura = Asignatura.objects.get(pk=asignatura_id) if asignatura_id else (
                horario_instancia.asignatura if horario_instancia else None
            )
            salon      = Salon.objects.get(pk=salon_id) if salon_id else (
                horario_instancia.salon if horario_instancia else None
            )
            gestor     = Usuario.objects.get(pk=gestor_id) if gestor_id else (
                horario_instancia.gestor if horario_instancia else None
            )
            dia        = dia or (horario_instancia.dia if horario_instancia else '')
            semestre   = semestre or (horario_instancia.semestre if horario_instancia else '')
        except (Asignatura.DoesNotExist, Salon.DoesNotExist, Usuario.DoesNotExist) as exc:
            return Response(
                {'error': f'Referencia inválida: {exc}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # ── Parsear horas ─────────────────────────────────────────────────────
        try:
            hora_inicio_str = data.get('hora_inicio')
            hora_fin_str    = data.get('hora_fin')

            if hora_inicio_str:
                hora_inicio = time.fromisoformat(str(hora_inicio_str))
            elif horario_instancia:
                hora_inicio = horario_instancia.hora_inicio
            else:
                hora_inicio = None

            if hora_fin_str:
                hora_fin = time.fromisoformat(str(hora_fin_str))
            elif horario_instancia:
                hora_fin = horario_instancia.hora_fin
            else:
                hora_fin = None
        except (ValueError, TypeError):
            return Response(
                {'error': 'Formato de hora inválido. Use HH:MM o HH:MM:SS.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # ── Ejecutar validaciones (sin guardar) ───────────────────────────────
        resultado = evaluar_horario(
            asignatura  = asignatura,
            salon       = salon,
            gestor      = gestor,
            dia         = dia,
            hora_inicio = hora_inicio,
            hora_fin    = hora_fin,
            semestre    = semestre,
            excluir_id  = horario_id,
            instancia   = horario_instancia,
        )

        return Response({
            'valido':       len(resultado['errores']) == 0,
            'conflictos':   resultado['errores'],       # restricciones duras
            'advertencias': resultado['advertencias'],  # restricciones blandas
            'resumen':      resultado['resumen'],       # horas asignadas / carga del gestor
        }, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────
# MATRÍCULAS
# ─────────────────────────────────────────────

class MatriculaViewSet(AdminCRUDMixin, viewsets.ModelViewSet):
    queryset = Matricula.objects.all().order_by('id')
    serializer_class = MatriculaSerializer
    permission_classes = [MatriculaPermission]
    modelo_auditoria = 'Matricula'

    def create(self, request, *args, **kwargs):
        estudiante_id  = request.data.get('estudiante')
        asignatura_id  = request.data.get('asignatura')
        semestre       = request.data.get('semestre')

        if Matricula.objects.filter(
            estudiante_id=estudiante_id, semestre=semestre
        ).count() >= 8:
            return Response(
                {"error": "No puedes matricularte en más de 8 asignaturas por semestre."},
                status=status.HTTP_400_BAD_REQUEST
            )
        if Matricula.objects.filter(
            estudiante_id=estudiante_id, asignatura_id=asignatura_id
        ).exists():
            return Response(
                {"error": "Ya estás matriculado en esta asignatura."},
                status=status.HTTP_400_BAD_REQUEST
            )
        return super().create(request, *args, **kwargs)


# ─────────────────────────────────────────────
# NOTIFICACIONES
# ─────────────────────────────────────────────

class NotificacionViewSet(viewsets.ModelViewSet):
    queryset = Notificacion.objects.all()
    serializer_class = NotificacionSerializer
    permission_classes = [AllowAny]

    @action(detail=False, methods=['post'])
    def enviar_masiva(self, request):
        asignatura_id = request.data.get('asignatura')
        estudiantes   = Matricula.objects.filter(
            asignatura_id=asignatura_id
        ).values_list('estudiante', flat=True)

        notificacion = Notificacion.objects.create(
            titulo=request.data.get('titulo'),
            mensaje=request.data.get('mensaje'),
            tipo='ASI',
            emisor_id=request.data.get('emisor'),
            asignatura_id=asignatura_id,
        )
        for estudiante_id in estudiantes:
            NotificacionUsuario.objects.create(
                notificacion=notificacion,
                usuario_id=estudiante_id,
            )
        return Response({"status": "Notificación enviada"}, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────
# HORARIO DE ESTUDIANTE (solo lectura)
# ─────────────────────────────────────────────

class EstudianteHorarioViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = HorarioSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        estudiante_id = self.request.query_params.get('estudiante')
        if not estudiante_id:
            return Horario.objects.none()
        asignaturas = Matricula.objects.filter(
            estudiante_id=estudiante_id
        ).values_list('asignatura', flat=True)
        return Horario.objects.filter(asignatura_id__in=asignaturas)


# ─────────────────────────────────────────────
# CONFIGURACIÓN DE USUARIO
# ─────────────────────────────────────────────

class ConfiguracionUsuarioViewSet(viewsets.ModelViewSet):
    queryset = ConfiguracionUsuario.objects.all()
    serializer_class = ConfiguracionUsuarioSerializer
    permission_classes = [AllowAny]

    @action(detail=False, methods=['post'])
    def toggle_tema(self, request):
        usuario_id = request.data.get('usuario')
        config, _ = ConfiguracionUsuario.objects.get_or_create(usuario_id=usuario_id)
        config.tema_oscuro = not config.tema_oscuro
        config.save()
        return Response({"tema_oscuro": config.tema_oscuro})


# ─────────────────────────────────────────────
# BUSCADOR
# ─────────────────────────────────────────────

class BuscadorViewSet(viewsets.ViewSet):
    permission_classes = [AllowAny]

    @action(detail=False, methods=['get'])
    def buscar_asignaturas(self, request):
        query = request.query_params.get('q', '')
        asignaturas = Asignatura.objects.filter(
            Q(nombre__icontains=query) | Q(codigo__icontains=query)
        )
        return Response(AsignaturaSerializer(asignaturas, many=True).data)


# ─────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([AllowAny])
def login_view(request):
    email    = request.data.get('username')
    password = request.data.get('password')

    try:
        user_obj = Usuario.objects.get(email=email)
    except Usuario.DoesNotExist:
        return Response({'error': 'Usuario no existe'}, status=400)

    user = authenticate(username=user_obj.username, password=password)
    if user is not None:
        return Response({
            'id':       user.id,
            'username': user.username,
            'email':    user.email,
            'rol':      user.rol,
        })
    return Response({'error': 'Credenciales incorrectas'}, status=400)


# ─────────────────────────────────────────────────────────────────────────────
# AUDITORÍA: HISTORIAL, ESTADÍSTICAS Y ÚLTIMOS 5 CAMBIOS
# ─────────────────────────────────────────────────────────────────────────────
#
# Acceso por rol:
#   TI  → ve TODOS los registros.
#   CO  → ve solo los registros de objetos relacionados con su programa
#          (implementación: filtra por modelo='Horario' y objetos de su programa).
#   Otros roles → sin acceso (403).
#
# Endpoints generados:
#   GET  /api/auditoria/              → historial paginado (limit/offset)
#   GET  /api/auditoria/ultimos/      → últimos 5 registros
#   GET  /api/auditoria/estadisticas/ → datos para gráficos
#
# ─────────────────────────────────────────────────────────────────────────────

from rest_framework.pagination import LimitOffsetPagination
from django.db.models import Count
from django.db.models.functions import TruncWeek, TruncDay


# ── Paginación configurable vía query params ?limit=20&offset=0 ──────────────
class AuditoriaPagination(LimitOffsetPagination):
    default_limit = 20
    max_limit     = 100


# ── Permiso: solo TI y CO acceden a auditoría ────────────────────────────────
class IsTIorCO(permissions.BasePermission):
    """Solo permiten acceso a usuarios con rol TI o CO."""
    message = 'Solo los roles TI y CO pueden acceder al historial de auditoría.'

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and getattr(request.user, 'rol', None) in ('TI', 'CO')
        )


class AuditoriaViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet de solo lectura para el historial de auditoría del sistema.

    Roles permitidos:
      - TI  (Técnico de Soporte): acceso completo a todos los registros.
      - CO  (Coordinador de Programa): acceso restringido a los registros
            relacionados con sus propios horarios y asignaturas.

    Filtros disponibles (query params):
      ?modelo=Horario       filtra por tipo de modelo afectado
      ?accion=CREATE        filtra por acción (CREATE/UPDATE/DELETE/LOGIN/OTHER)
      ?usuario=<id>         filtra por ID de usuario que realizó la acción
    """

    serializer_class   = RegistroAuditoriaSerializer
    permission_classes = [IsTIorCO]
    pagination_class   = AuditoriaPagination

    def get_queryset(self):
        user = self.request.user
        qs   = RegistroAuditoria.objects.select_related('usuario').order_by('-fecha')

        # TI ve todo; CO ve solo lo relacionado con sus horarios/asignaturas
        if user.rol == 'CO':
            # Obtener IDs de horarios en programas coordinados por este usuario
            horario_ids = list(
                Horario.objects.filter(
                    asignatura__programa__coordinador=user
                ).values_list('id', flat=True)
            )
            asig_ids = list(
                Asignatura.objects.filter(
                    programa__coordinador=user
                ).values_list('id', flat=True)
            )
            qs = qs.filter(
                Q(modelo='Horario',    objeto_id__in=horario_ids) |
                Q(modelo='Asignatura', objeto_id__in=asig_ids)    |
                Q(usuario=user)
            )

        # Filtros opcionales de query params
        modelo  = self.request.query_params.get('modelo')
        accion  = self.request.query_params.get('accion')
        usuario = self.request.query_params.get('usuario')

        if modelo:
            qs = qs.filter(modelo__iexact=modelo)
        if accion:
            qs = qs.filter(accion__iexact=accion)
        if usuario:
            qs = qs.filter(usuario_id=usuario)

        return qs

    # ── GET /api/auditoria/ultimos/ ───────────────────────────────────────────
    @action(detail=False, methods=['get'], url_path='ultimos')
    def ultimos(self, request):
        """
        Devuelve los últimos 5 registros de auditoría.

        Endpoint rápido y ligero, sin paginación.
        Útil para un widget de "actividad reciente" en el dashboard.

        Respuesta (200):
        [
          {
            "id": 42,
            "fecha": "2026-10-22T22:00:00Z",
            "usuario": 3,
            "usuario_nombre": "Ana López",
            "accion": "CREATE",
            "accion_display": "Creación",
            "modelo": "Horario",
            "objeto_id": 17,
            "descripcion": "Horario creado: ...",
            "datos_antes": null,
            "datos_despues": {...},
            "ip": "192.168.1.10"
          },
          ...
        ]
        """
        qs      = self.get_queryset()[:5]
        serializer = RegistroAuditoriaSerializer(qs, many=True)
        return Response(serializer.data)

    # ── GET /api/auditoria/estadisticas/ ─────────────────────────────────────
    @action(detail=False, methods=['get'], url_path='estadisticas')
    def estadisticas(self, request):
        """
        Devuelve datos estadísticos sobre el historial de auditoría,
        listos para alimentar librerías de gráficos (Chart.js, Recharts, etc.).

        Soporta parámetro ?periodo=semana (predeterminado) o ?periodo=dia
        para granularidad temporal.

        Respuesta (200):
        {
          "cambios_por_periodo": [
            {"periodo": "2026-10-13", "total": 5},
            {"periodo": "2026-10-20", "total": 12}
          ],
          "cambios_por_accion": [
            {"accion": "CREATE", "accion_display": "Creación",    "total": 30},
            {"accion": "UPDATE", "accion_display": "Actualización", "total": 18},
            {"accion": "DELETE", "accion_display": "Eliminación", "total": 4}
          ],
          "cambios_por_modelo": [
            {"modelo": "Horario",    "total": 25},
            {"modelo": "Asignatura", "total": 12}
          ],
          "cambios_por_usuario": [
            {"usuario": 3, "usuario_nombre": "Ana López", "total": 20},
            {"usuario": 5, "usuario_nombre": "Carlos Pérez", "total": 8}
          ],
          "total_registros": 52
        }
        """
        qs      = self.get_queryset()
        periodo = request.query_params.get('periodo', 'semana').lower()

        # ── 1. Cambios por periodo (semana o día) ─────────────────────────────
        if periodo == 'dia':
            trunc_fn = TruncDay
        else:
            trunc_fn = TruncWeek

        cambios_por_periodo = (
            qs
            .annotate(periodo=trunc_fn('fecha'))
            .values('periodo')
            .annotate(total=Count('id'))
            .order_by('periodo')
        )
        datos_periodo = [
            {
                'periodo': item['periodo'].strftime('%Y-%m-%d') if item['periodo'] else None,
                'total':   item['total'],
            }
            for item in cambios_por_periodo
        ]

        # ── 2. Cambios por tipo de acción ─────────────────────────────────────
        accion_map = dict(RegistroAuditoria.ACCIONES)
        cambios_por_accion = (
            qs
            .values('accion')
            .annotate(total=Count('id'))
            .order_by('-total')
        )
        datos_accion = [
            {
                'accion':         item['accion'],
                'accion_display': accion_map.get(item['accion'], item['accion']),
                'total':          item['total'],
            }
            for item in cambios_por_accion
        ]

        # ── 3. Cambios por modelo afectado ────────────────────────────────────
        cambios_por_modelo = (
            qs
            .values('modelo')
            .annotate(total=Count('id'))
            .order_by('-total')
        )
        datos_modelo = list(cambios_por_modelo)

        # ── 4. Cambios por usuario (top 10) ───────────────────────────────────
        cambios_por_usuario = (
            qs
            .filter(usuario__isnull=False)
            .values('usuario', 'usuario__first_name', 'usuario__last_name', 'usuario__username')
            .annotate(total=Count('id'))
            .order_by('-total')[:10]
        )
        datos_usuario = []
        for item in cambios_por_usuario:
            nombre = f"{item['usuario__first_name']} {item['usuario__last_name']}".strip()
            datos_usuario.append({
                'usuario':        item['usuario'],
                'usuario_nombre': nombre if nombre else item['usuario__username'],
                'total':          item['total'],
            })

        # ── 5. Cambios por rol de usuario ─────────────────────────────────────
        rol_map = dict(Usuario.ROLES)
        cambios_por_rol = (
            qs
            .filter(usuario__isnull=False)
            .values('usuario__rol')
            .annotate(total=Count('id'))
            .order_by('-total')
        )
        datos_rol = [
            {
                'rol':         item['usuario__rol'],
                'rol_display': rol_map.get(item['usuario__rol'], item['usuario__rol']),
                'total':       item['total'],
            }
            for item in cambios_por_rol
        ]

        # ── 6. Estado actual de los horarios (distribución por día) ───────────
        horarios_qs = Horario.objects.all()
        if request.user.rol == 'CO':
            horarios_qs = horarios_qs.filter(asignatura__programa__coordinador=request.user)

        dias_labels = {
            'LUN': 'Lunes',
            'MAR': 'Martes',
            'MIE': 'Miércoles',
            'JUE': 'Jueves',
            'VIE': 'Viernes',
        }
        horarios_por_dia = (
            horarios_qs
            .values('dia')
            .annotate(total=Count('id'))
        )
        dia_counts = {item['dia']: item['total'] for item in horarios_por_dia}
        datos_horarios_dia = [
            {
                'dia':         dia_cod,
                'dia_display': dias_labels[dia_cod],
                'total':       dia_counts.get(dia_cod, 0),
            }
            for dia_cod in ['LUN', 'MAR', 'MIE', 'JUE', 'VIE']
        ]

        estado_horarios = {
            'total_horarios':   horarios_qs.count(),
            'horarios_por_dia': datos_horarios_dia,
        }

        return Response({
            'cambios_por_periodo': datos_periodo,
            'cambios_por_accion':  datos_accion,
            'cambios_por_modelo':  datos_modelo,
            'cambios_por_usuario': datos_usuario,
            'cambios_por_rol':     datos_rol,
            'estado_horarios':     estado_horarios,
            'total_registros':     qs.count(),
        })
