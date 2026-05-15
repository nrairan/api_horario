"""
chatbot_view.py — Vista Django para el chatbot AsistUC
======================================================
Añade este archivo en: api_horario/api_horario/api_app/chatbot_view.py

Actúa como proxy entre Flutter y Google Gemini AI.
La API Key queda segura en el servidor, nunca expuesta al cliente.

Modelos utilizados: Horario, Asignatura, Programa, Salon, Usuario
Endpoint: POST /api/chatbot/
"""

import json
import os
import requests

from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status

from .models import Horario, Asignatura, Programa, Salon, Usuario


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyDZyeTIprDhHeZ27csKdcLhoizrNzs5A6Y")
GEMINI_MODEL   = "gemini-flash-latest"   # mismo modelo que el cURL que funciona
GEMINI_URL     = (
    f"https://generativelanguage.googleapis.com/v1beta/models"
    f"/{GEMINI_MODEL}:generateContent"
)


# ─────────────────────────────────────────────────────────────────────────────
# BASE DE CONOCIMIENTO DINÁMICA (consultada en cada request)
# ─────────────────────────────────────────────────────────────────────────────

def build_dynamic_context() -> str:
    """
    Consulta la BD en tiempo real para enriquecer el contexto del chatbot.
    Solo metadatos ligeros — no se exponen datos sensibles de usuarios.
    """
    try:
        programas   = list(Programa.objects.values("nombre", "codigo"))
        asignaturas = list(Asignatura.objects.values("nombre", "codigo", "creditos", "semestre", "programa__nombre"))
        salones     = list(Salon.objects.values("codigo", "capacidad", "edificio"))
        gestores    = list(
            Usuario.objects
            .filter(rol="GC")
            .values("first_name", "last_name", "username")
        )

        prog_txt = "\n".join(
            f"  • {p['nombre']} (código: {p['codigo']})" for p in programas
        ) or "  (sin datos)"

        asig_txt = "\n".join(
            f"  • [{a['codigo']}] {a['nombre']} — {a['creditos']} créditos, "
            f"semestre {a['semestre'] or '?'}, programa: {a['programa__nombre']}"
            for a in asignaturas[:60]          # límite para no saturar el prompt
        ) or "  (sin datos)"

        salon_txt = "\n".join(
            f"  • Salón {s['codigo']} — cap. {s['capacidad']} — edificio {s['edificio']}"
            for s in salones
        ) or "  (sin datos)"

        gestor_txt = "\n".join(
            f"  • {g['first_name']} {g['last_name']} (@{g['username']})"
            for g in gestores
        ) or "  (sin datos)"

        return f"""
=== DATOS ACTUALES DEL SISTEMA (en tiempo real) ===

PROGRAMAS REGISTRADOS:
{prog_txt}

ASIGNATURAS / NÚCLEOS TEMÁTICOS:
{asig_txt}

SALONES:
{salon_txt}

GESTORES DEL CONOCIMIENTO (Docentes/Instructores):
{gestor_txt}
"""
    except Exception as e:
        return f"\n(No se pudo cargar contexto dinámico: {e})\n"


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT ESTÁTICO
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_STATIC = """
Eres AsistUC, el asistente virtual oficial del sistema PGC_MCS (Plataforma de
Gestión de Clases y Módulos para Control de Sesiones) de la Universidad de
Cundinamarca — Seccional Ubaté.

IDIOMA:
- Detecta automáticamente el idioma del usuario (español o inglés).
- Si el usuario escribe en español, responde en español.
- Si el usuario escribe en inglés, responde en inglés.
- Para cualquier otro idioma, responde en inglés.

REGLAS ABSOLUTAS:
1. SOLO respondes sobre los siguientes temas:
   a) Sistema PGC_MCS: horarios académicos, asignaturas, programas, docentes/gestores, salones.
   b) Persona Transhumana de la Universidad de Cundinamarca.
2. Si te preguntan sobre otro tema, declinas educadamente y rediriges.
3. Responde de forma clara, concisa y profesional.
4. No inventas datos; si no tienes la información, lo dices.
5. Eres amable y orientado a ayudar a estudiantes, docentes y coordinadores.

CONTEXTO DEL SISTEMA PGC_MCS:
- Roles del sistema:
  • CO (Coordinador de Programa): gestiona el programa, aprueba horarios.
  • GC (Gestor del Conocimiento): docente/instructor que dicta clases.
  • ES (Estudiante): consulta horarios y materias matriculadas.
  • TI (Técnico de Soporte): soporte técnico del sistema.
- Los horarios tienen: asignatura, salón, gestor, día, hora_inicio, hora_fin, semestre.
- Los días disponibles son: Lunes, Martes, Miércoles, Jueves, Viernes.
- La API usa JWT para autenticación (endpoint /api/token/).
- Endpoints principales:
  GET  /api/horarios/                              → lista todos los horarios
  GET  /api/programa/                              → lista programas
  GET  /api/asignaturas/                           → lista asignaturas (filtros: programa, semestre)
  GET  /api/salones/                               → lista salones
  GET  /api/usuarios/?rol=GC                       → lista gestores/docentes
  GET  /api/horarios-estudiante/?estudiante=<id>   → horario de un estudiante
  GET  /api/buscar-asignaturas/?q=<texto>          → búsqueda de asignaturas
  POST /api/chatbot/                               → este chatbot

=== PERSONA TRANSHUMANA — UNIVERSIDAD DE CUNDINAMARCA ===

La Persona Transhumana es la declaración filosófica y ética que define el perfil
del ser humano que la Universidad de Cundinamarca busca formar. Está compuesta
por doce principios fundamentales:

1. ORGANIZO y dirijo mi propia vida.
2. ENTIENDO y defiendo que la vida es la gran apuesta del siglo 21.
3. EDIFICO un estilo propio de vida que me permite ser feliz, amar, vivir bien
   y lograr mi desarrollo personal.
4. DETERMINO mi vida, aprendo de ella y asumo las buenas prácticas de mis
   semejantes, que contribuyen a mi desarrollo individual.
5. ME VALORO Y CUIDO mi salud corporal, mental, emocional, sentimental y espiritual.
6. VOY MÁS ALLÁ DE MÍ, cada día mejoro, reinvento y evoluciono, dejando de lado
   los intereses propios, tomando la posición del otro, los seres vivos y la naturaleza.
7. ME PERFECCIONO, antes y no necesariamente a través de la tecnología y la ciencia;
   además, lucho por mi felicidad, realizo mi plan de vida, sin desconocer a los demás
   y dando lo mejor como profesional, emprendedor, innovador y transformador de mi entorno.
8. VIVO Y CONTRIBUYO para que la comunidad esté en paz y armonía.
9. Soy LIBRE, AUTÓNOMO Y RESPONSABLE a través del diálogo y la construcción,
   como ideal regulativo; me dirijo, controlo y dicto mis propias leyes.
10. FORJO MI PERSONALIDAD, el carácter, la identidad, la autonomía y la responsabilidad.
11. REALIZO actos transformadores de mejora que permiten, entre distintas actuaciones,
    escoger la que me aporte y no me haga daño, ni a la naturaleza, la sociedad,
    la convivencia y la vida democrática.
12. EXPLORO y CULTIVO los sentimientos, emociones y comportamientos positivos,
    para promover mi felicidad.

CONTEXTO DE LA PERSONA TRANSHUMANA:
- Es un modelo filosófico propio de la Universidad de Cundinamarca que va más allá
  del transhumanismo tecnológico clásico: se centra en el desarrollo humano integral.
- Aplica a todos los miembros de la comunidad universitaria: estudiantes, docentes,
  administrativos y egresados.
- Sus pilares son: autonomía, responsabilidad, felicidad, desarrollo personal,
  respeto por la naturaleza y contribución a la paz social.
- Si alguien pregunta qué principio le aplica según su situación, puedes orientarlo
  con base en los 12 principios descritos.

Si la pregunta es en inglés, traduce los principios y el contexto al inglés en tu respuesta.
"""


def build_full_system_prompt() -> str:
    return SYSTEM_PROMPT_STATIC + build_dynamic_context()


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINT PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

@api_view(["POST"])
@permission_classes([AllowAny])
def chatbot_view(request):
    """
    POST /api/chatbot/

    Body JSON:
    {
      "message": "¿Cuál es el horario de Cálculo I?",
      "history": [                        ← opcional, para multi-turno
        {"role": "user",  "text": "Hola"},
        {"role": "model", "text": "Hola, ¿en qué te ayudo?"}
      ]
    }

    Response:
    {
      "reply": "...",
      "ok": true
    }
    """
    user_message = request.data.get("message", "").strip()
    history      = request.data.get("history", [])

    if not user_message:
        return Response(
            {"ok": False, "error": "El campo 'message' es requerido."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Construir historial en formato Gemini
    contents = []
    for turn in history[-20:]:           # máximo 20 turnos para no saturar
        role = turn.get("role", "user")
        text = turn.get("text", "")
        if role in ("user", "model") and text:
            contents.append({"role": role, "parts": [{"text": text}]})

    # Agregar el mensaje actual
    contents.append({"role": "user", "parts": [{"text": user_message}]})

    payload = {
        "system_instruction": {
            "parts": [{"text": build_full_system_prompt()}]
        },
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": 1024,
            "temperature":     0.3,
        },
    }

    try:
        resp = requests.post(
            GEMINI_URL,
            headers={
                "Content-Type": "application/json",
                "X-goog-api-key": GEMINI_API_KEY,   # igual que el cURL
            },
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data  = resp.json()
        reply = data["candidates"][0]["content"]["parts"][0]["text"]
        return Response({"ok": True, "reply": reply})

    except requests.exceptions.Timeout:
        return Response(
            {"ok": False, "error": "Tiempo de espera agotado con Gemini."},
            status=status.HTTP_504_GATEWAY_TIMEOUT,
        )
    except Exception as e:
        return Response(
            {"ok": False, "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )