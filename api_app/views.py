# api_app/views.py

from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny
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
        dia      = self.request.query_params.get('dia')
        gestor   = self.request.query_params.get('gestor')
        asignatura = self.request.query_params.get('asignatura')
        if dia:
            queryset = queryset.filter(dia=dia)
        if gestor:
            queryset = queryset.filter(gestor_id=gestor)
        if asignatura:
            queryset = queryset.filter(asignatura_id=asignatura)
        return queryset

    def create(self, request, *args, **kwargs):
        hora_inicio_str = request.data.get('hora_inicio', '')
        hora_fin_str    = request.data.get('hora_fin', '')

        try:
            h_inicio = time.fromisoformat(hora_inicio_str)
            h_fin    = time.fromisoformat(hora_fin_str)

            minutos_inicio = h_inicio.hour * 60 + h_inicio.minute
            minutos_fin    = h_fin.hour * 60 + h_fin.minute
            duracion       = minutos_fin - minutos_inicio

            if duracion <= 0:
                return Response(
                    {"error": "La hora de fin debe ser mayor a la de inicio."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if not (120 <= duracion <= 180):
                return Response(
                    {"error": "Las clases deben durar entre 2 y 3 horas."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if h_inicio < time(7, 0) or h_fin > time(18, 0):
                return Response(
                    {"error": "Las clases deben ser entre 7:00 a.m. y 6:00 p.m."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except (ValueError, AttributeError):
            return Response(
                {"error": "Formato de hora inválido. Use HH:MM:SS"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validación: gestor no puede tener más de 4 clases el mismo día
        gestor_id = request.data.get('gestor')
        dia       = request.data.get('dia')
        if gestor_id and dia:
            if Horario.objects.filter(gestor_id=gestor_id, dia=dia).count() >= 4:
                return Response(
                    {"error": "Un gestor no puede tener más de 4 clases el mismo día."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Validación: cruce de horario del gestor ese día
        if gestor_id and dia:
            cruces = Horario.objects.filter(gestor_id=gestor_id, dia=dia)
            for h in cruces:
                ini_ex = h.hora_inicio.hour * 60 + h.hora_inicio.minute
                fin_ex = h.hora_fin.hour    * 60 + h.hora_fin.minute
                ini_nu = h_inicio.hour * 60 + h_inicio.minute
                fin_nu = h_fin.hour    * 60 + h_fin.minute
                if ini_nu < fin_ex and fin_nu > ini_ex:
                    return Response(
                        {"error": f"Cruce de horario con bloque existente {h.hora_inicio}-{h.hora_fin}"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

        return super().create(request, *args, **kwargs)

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
                "id":           h.id,
                "asignatura":   h.asignatura.nombre,
                "asignatura_id": h.asignatura.id,
                "gestor":       f"{h.gestor.first_name} {h.gestor.last_name}".strip() or h.gestor.username,
                "gestor_id":    h.gestor.id,
                "salon":        h.salon.codigo if h.salon else None,
                "hora_inicio":  str(h.hora_inicio),
                "hora_fin":     str(h.hora_fin),
            })
        return Response(resultado)


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