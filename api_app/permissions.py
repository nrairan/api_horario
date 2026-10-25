# api_app/permissions.py
from rest_framework import permissions

class IsCoordinator(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.rol == 'CO'

class IsGestor(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.rol == 'GC'

class IsEstudiante(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.rol == 'ES'

class IsTIOrReadOnly(permissions.BasePermission):
    """Lectura abierta (como hasta ahora); escritura solo para el rol TI autenticado."""
    message = "Solo el rol TI puede crear, modificar o eliminar este recurso."

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        u = request.user
        return bool(
            u and u.is_authenticated
            and (getattr(u, "rol", None) == "TI" or u.is_superuser)
        )


class MatriculaPermission(permissions.BasePermission):
    """
    Lectura abierta. Escritura: TI y CO sin restricción; un estudiante solo puede
    crear o eliminar SUS propias matriculas. Gestores: sin escritura.
    """
    message = "No tienes permiso para modificar esta matricula."

    def _rol(self, request):
        u = request.user
        if not (u and u.is_authenticated):
            return None
        return "TI" if u.is_superuser else getattr(u, "rol", None)

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        rol = self._rol(request)
        if rol in ("TI", "CO"):
            return True
        if rol == "ES":
            if request.method == "POST":
                return str(request.data.get("estudiante")) == str(request.user.pk)
            return request.method == "DELETE"
        return False

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS or self._rol(request) in ("TI", "CO"):
            return True
        return obj.estudiante_id == request.user.pk


class AutenticadoLecturaTIEscritura(permissions.BasePermission):
    """Lectura para cualquier usuario autenticado; escritura solo para TI."""
    message = "Solo el rol TI puede crear, modificar o eliminar este recurso."

    def has_permission(self, request, view):
        u = request.user
        if not (u and u.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(getattr(u, "rol", None) == "TI" or u.is_superuser)


class HorarioPermission(permissions.BasePermission):
    """
    Horarios: lectura para cualquier usuario autenticado (el queryset limita al CO a su
    programa); escritura y validacion solo para CO y TI. La pertenencia de la asignatura
    al programa del CO se comprueba en el serializer.
    """
    message = "Solo los roles CO y TI pueden modificar horarios."

    def has_permission(self, request, view):
        u = request.user
        if not (u and u.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(getattr(u, "rol", None) in ("CO", "TI") or u.is_superuser)
