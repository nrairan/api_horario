# api_horario/api_horario/api_app/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)
from .views import (
    UsuarioViewSet,
    ProgramaViewSet,
    AsignaturaViewSet,
    SalonViewSet,
    HorarioViewSet,
    MatriculaViewSet,
    NotificacionViewSet,
    ConfiguracionUsuarioViewSet,
    EstudianteHorarioViewSet,
    BuscadorViewSet,
    AuditoriaViewSet,
    login_view,
)

from .chatbot_view import chatbot_view

router = DefaultRouter()

router.register(r'usuarios',            UsuarioViewSet,             basename='usuario')
router.register(r'matricula',           MatriculaViewSet,           basename='matricula')
router.register(r'programa',            ProgramaViewSet,            basename='programa')
router.register(r'asignaturas',         AsignaturaViewSet,          basename='asignatura')
router.register(r'salones',             SalonViewSet,               basename='salon')
router.register(r'horarios',            HorarioViewSet,             basename='horario')
router.register(r'notificaciones',      NotificacionViewSet,        basename='notificacion')
router.register(r'configuracion',       ConfiguracionUsuarioViewSet, basename='configuracion')
router.register(r'horarios-estudiante', EstudianteHorarioViewSet,   basename='estudiante-horario')

# ── Auditoría ─────────────────────────────────────────────────────────────────
# Genera automáticamente:
#   GET  /api/auditoria/              → historial paginado (limit/offset)
#   GET  /api/auditoria/ultimos/      → últimos 5 registros
#   GET  /api/auditoria/estadisticas/ → datos para gráficos
router.register(r'auditoria',           AuditoriaViewSet,           basename='auditoria')


urlpatterns = [
    # Router (incluye auditoria y todos los ViewSets anteriores)
    path('', include(router.urls)),

    # JWT
    path('token/',         TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(),    name='token_refresh'),

    # Rutas manuales
    path('buscar-asignaturas/', BuscadorViewSet.as_view({'get': 'buscar_asignaturas'}), name='buscar-asignaturas'),
    path('notificaciones/enviar_masiva/', NotificacionViewSet.as_view({'post': 'enviar_masiva'}), name='notificaciones-enviar-masiva'),

    # Login personalizado (autenticación por email)
    path('login/', login_view, name='login'),

    path('chatbot/', chatbot_view, name='chatbot'),
]
