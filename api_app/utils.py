"""
api_app/utils.py
=================
Funciones de utilidad compartidas por toda la aplicación.

Función principal: registrar_auditoria()
  Crea un RegistroAuditoria de forma segura (sin lanzar excepciones
  que interrumpan el flujo principal) cuando se produce un cambio en
  cualquier modelo del sistema.

Uso típico en un ViewSet:

    from api_app.utils import registrar_auditoria

    class HorarioViewSet(viewsets.ModelViewSet):

        @transaction.atomic
        def create(self, request, *args, **kwargs):
            response = super().create(request, *args, **kwargs)
            registrar_auditoria(
                request=request,
                accion='CREATE',
                modelo='Horario',
                objeto_id=response.data.get('id'),
                descripcion=f"Horario creado: {response.data}",
                datos_despues=response.data,
            )
            return response
"""

import logging
from django.db import connection

logger = logging.getLogger(__name__)


def get_client_ip(request) -> str | None:
    """Extrae la IP real del cliente considerando proxies."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def registrar_auditoria(
    request,
    accion: str,
    modelo: str,
    objeto_id=None,
    descripcion: str = '',
    datos_antes=None,
    datos_despues=None,
):
    """
    Crea un RegistroAuditoria de forma no bloqueante.

    Si la tabla todavía no existe (ej. antes de ejecutar makemigrations),
    o si ocurre cualquier error, lo registra en el log y continúa.

    Parámetros
    ----------
    request       : HttpRequest de Django (para extraer usuario e IP)
    accion        : 'CREATE' | 'UPDATE' | 'DELETE' | 'LOGIN' | 'OTHER'
    modelo        : nombre del modelo afectado (ej. 'Horario')
    objeto_id     : PK del objeto afectado (opcional)
    descripcion   : texto legible del cambio
    datos_antes   : dict/list serializable como JSON (estado previo)
    datos_despues : dict/list serializable como JSON (estado posterior)
    """
    try:
        # Import diferido para evitar importaciones circulares al arrancar
        from api_app.models import RegistroAuditoria

        usuario = None
        if hasattr(request, 'user') and request.user and request.user.is_authenticated:
            usuario = request.user

        ip = get_client_ip(request)

        RegistroAuditoria.objects.create(
            usuario       = usuario,
            accion        = accion,
            modelo        = modelo,
            objeto_id     = objeto_id,
            descripcion   = descripcion,
            datos_antes   = datos_antes,
            datos_despues = datos_despues,
            ip            = ip,
        )
    except Exception as exc:
        # Nunca interrumpir el flujo principal por un fallo de auditoría
        logger.warning("registrar_auditoria falló [%s/%s]: %s", modelo, objeto_id, exc)
