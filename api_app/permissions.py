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