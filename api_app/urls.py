# api_horario/api_horario/api_app/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)
from .views import (
    # Importa todos los ViewSets que has definido en api_app/views.py
    UsuarioViewSet,
    ProgramaViewSet,
    AsignaturaViewSet,
    SalonViewSet,
    HorarioViewSet,
    MatriculaViewSet,
    NotificacionViewSet,
    ConfiguracionUsuarioViewSet,
    EstudianteHorarioViewSet, # Asumiendo que esta vista existe
    BuscadorViewSet,          # Asumiendo que esta vista existe
)

# Crea una instancia del DefaultRouter de Django REST Framework
# para generar automáticamente las URLs para tus ViewSets
router = DefaultRouter()

# Registra cada ViewSet con el router, especificando el prefijo de la URL
# y un nombre base (basename) para facilitar la generación de URLs inversas.
router.register(r'usuarios', UsuarioViewSet, basename='usuario')
router.register(r'matricula', MatriculaViewSet, basename='matricula')
router.register(r'programa', ProgramaViewSet, basename='programa')
router.register(r'asignaturas', AsignaturaViewSet, basename='asignatura')
router.register(r'salones', SalonViewSet, basename='salon')
router.register(r'horarios', HorarioViewSet, basename='horario')
router.register(r'notificaciones', NotificacionViewSet, basename='notificacion')
router.register(r'configuracion', ConfiguracionUsuarioViewSet, basename='configuracion')
router.register(r'horarios-estudiante', EstudianteHorarioViewSet, basename='estudiante-horario')


# Define la lista de patrones de URL para esta aplicación.
urlpatterns = [
    # Incluye las URLs generadas automáticamente por el router.
    # Estas URLs estarán bajo el prefijo 'api/' que definiste en el urls.py principal.
    # Ejemplos: /api/usuarios/, /api/programas/, etc.
    path('', include(router.urls)),

    # Rutas para la autenticación JWT (generación y refresco de tokens)
    # Estas rutas serán: /api/token/ y /api/token/refresh/
    path('token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    # Rutas para acciones personalizadas o ViewSets que no usan el router directamente.
    # Estas se definen explícitamente usando path().
    # Por ejemplo, /api/buscar-asignaturas/
    path('buscar-asignaturas/', BuscadorViewSet.as_view({'get': 'buscar_asignaturas'}), name='buscar-asignaturas'),
    # Ejemplo: /api/notificaciones/enviar_masiva/
    path('notificaciones/enviar_masiva/', NotificacionViewSet.as_view({'post': 'enviar_masiva'}), name='notificaciones-enviar-masiva'),
]