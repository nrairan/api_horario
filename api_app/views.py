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
    NotificacionUsuario, ConfiguracionUsuario
)
from .serializers import (
    UsuarioSerializer, ProgramaSerializer, AsignaturaSerializer,
    SalonSerializer, HorarioSerializer, MatriculaSerializer,
    NotificacionSerializer, NotificacionUsuarioSerializer,
    ConfiguracionUsuarioSerializer
)
from django.contrib.auth import authenticate


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

class UsuarioViewSet(viewsets.ModelViewSet):
    serializer_class = UsuarioSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = Usuario.objects.all()
        rol = self.request.query_params.get('rol')
        if rol:
            queryset = queryset.filter(rol=rol)
        return queryset


# ─────────────────────────────────────────────
# PROGRAMAS
# ─────────────────────────────────────────────

class ProgramaViewSet(viewsets.ModelViewSet):
    queryset = Programa.objects.all()
    serializer_class = ProgramaSerializer
    permission_classes = [AllowAny]


# ─────────────────────────────────────────────
# ASIGNATURAS
# ─────────────────────────────────────────────

class AsignaturaViewSet(viewsets.ModelViewSet):
    serializer_class = AsignaturaSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = Asignatura.objects.all()
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

class SalonViewSet(viewsets.ModelViewSet):
    queryset = Salon.objects.all()
    serializer_class = SalonSerializer
    permission_classes = [AllowAny]


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
        return super().create(request, *args, **kwargs)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        """
        Actualiza un horario con las mismas validaciones que create().
        El serializer excluye el horario actual de las consultas de conflictos
        para que no colisione consigo mismo.
        """
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        """PATCH con validaciones completas (igual que update)."""
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)

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
        resultado = {dia: [] for dia in ['LUN', 'MAR', 'MIE', 'JUE', 'VIE']}
        for h in horarios:
            resultado[h.dia].append({
                "id":            h.id,
                "asignatura":    h.asignatura.nombre,
                "asignatura_id": h.asignatura.id,
                "gestor":        f"{h.gestor.first_name} {h.gestor.last_name}".strip() or h.gestor.username,
                "gestor_id":     h.gestor.id,
                "salon":         h.salon.codigo if h.salon else None,
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
        from api_app.services.validacion_horarios import validar_horario

        data = request.data

        # ── Resolver referencias a objetos desde IDs ──────────────────────────
        try:
            asignatura_id = data.get('asignatura')
            salon_id      = data.get('salon')
            gestor_id     = data.get('gestor')
            dia           = data.get('dia', '')
            semestre      = data.get('semestre', '')
            horario_id    = data.get('horario_id')  # para excluir al editar

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
        conflictos = validar_horario(
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
            'valido':      len(conflictos) == 0,
            'conflictos':  conflictos,
        }, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────
# MATRÍCULAS
# ─────────────────────────────────────────────

class MatriculaViewSet(viewsets.ModelViewSet):
    queryset = Matricula.objects.all()
    serializer_class = MatriculaSerializer
    permission_classes = [AllowAny]

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
