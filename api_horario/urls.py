# api_horario/api_horario/urls.py

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    # Ruta para el panel de administración de Django
    path('admin/', admin.site.urls),

    # Incluye todas las rutas definidas en api_app/urls.py bajo el prefijo 'api/'
    # Esto significa que cualquier URL definida en api_app/urls.py,
    # por ejemplo, 'usuarios/', será accesible como 'api/usuarios/'.
    path('api/', include('api_app.urls')),
]