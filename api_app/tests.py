"""
api_app/tests.py
=================
Pruebas automatizadas para el sistema de validación de horarios de PGC_MCS.

Cobertura:
  1.  Crear un horario válido.
  2.  Validación obligatoria del salón (ausente, nulo, disponibilidad).
  3.  Rechazar dos clases que ocupen el mismo salón simultáneamente o con solapamiento parcial.
  4.  Permitir clases consecutivas sin solapamiento.
  5.  Rechazar dos clases simultáneas del mismo gestor en salones diferentes.
  6.  Rechazar un horario que supere el máximo diario de horas.
  7.  Rechazar más clases diarias que el límite permitido.
  8.  Rechazar una clase con duración o jornada inválida.
  9.  Permitir una actualización válida del mismo horario excluyendo la propia instancia.
  10. Detectar conflictos con otros horarios durante una actualización.
  11. Excluir el propio horario al editarlo (sin falsos positivos).
  12. Comportamiento con semestres: registros antiguos de otros semestres no generan conflicto.
  13. Validación especial de CAI (horarios fijos predefinidos y restricción de edición arbitraria).
  14. Verificar respuestas del endpoint POST /api/horarios/validar/ (incluyendo horario_id).
  15. Verificar que un horario inválido nunca queda guardado.
"""

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from datetime import time

from .models import Usuario, Programa, Asignatura, Salon, Horario
from .services.validacion_horarios import (
    validar_horario,
    validar_duracion_y_jornada,
    validar_salon_disponible,
    validar_gestor_disponible,
    validar_limite_clases_dia,
    validar_limite_horas_dia,
    es_asignatura_cai,
    validar_cai,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixture base compartida
# ─────────────────────────────────────────────────────────────────────────────

class HorarioTestBase(APITestCase):
    def setUp(self):
        # Coordinador
        self.coordinador = Usuario.objects.create_user(
            username='coord1',
            email='coord1@ucundinamarca.edu.co',
            password='Coord123',
            rol='CO',
        )

        # Gestores del Conocimiento
        self.gestor1 = Usuario.objects.create_user(
            username='gestor1',
            email='gestor1@ucundinamarca.edu.co',
            password='Gestor123',
            rol='GC',
            first_name='Carlos',
            last_name='Pérez',
        )
        self.gestor2 = Usuario.objects.create_user(
            username='gestor2',
            email='gestor2@ucundinamarca.edu.co',
            password='Gestor234',
            rol='GC',
            first_name='Ana',
            last_name='López',
        )

        # Programa académico
        self.programa = Programa.objects.create(
            nombre='Ingeniería de Sistemas',
            codigo='ISIST',
            coordinador=self.coordinador,
        )

        # Asignaturas normales
        self.asignatura1 = Asignatura.objects.create(
            codigo='IS101',
            nombre='Programación I',
            programa=self.programa,
            creditos=3,
        )
        self.asignatura2 = Asignatura.objects.create(
            codigo='IS102',
            nombre='Bases de Datos',
            programa=self.programa,
            creditos=3,
        )

        # Asignatura CAI (núcleo temático con horario predefinido)
        self.asignatura_cai = Asignatura.objects.create(
            codigo='CAI101',
            nombre='Campo de Aprendizaje Institucional I',
            programa=self.programa,
            creditos=2,
        )

        # Salones
        self.salon1 = Salon.objects.create(
            codigo='A101',
            capacidad=30,
            edificio='Bloque A',
        )
        self.salon2 = Salon.objects.create(
            codigo='B201',
            capacidad=25,
            edificio='Bloque B',
        )

        self.client = APIClient()

    def crear_horario_db(self, asignatura=None, salon=None, gestor=None,
                         dia='LUN', hora_inicio=time(7, 0), hora_fin=time(9, 0),
                         semestre='1'):
        return Horario.objects.create(
            asignatura  = asignatura  or self.asignatura1,
            salon       = salon       or self.salon1,
            gestor      = gestor      or self.gestor1,
            dia         = dia,
            hora_inicio = hora_inicio,
            hora_fin    = hora_fin,
            semestre    = semestre,
        )

    def payload(self, **kwargs):
        base = {
            'asignatura':  self.asignatura1.pk,
            'salon':       self.salon1.pk,
            'gestor':      self.gestor1.pk,
            'dia':         'LUN',
            'hora_inicio': '07:00:00',
            'hora_fin':    '09:00:00',
            'semestre':    '1',
        }
        base.update(kwargs)
        return base


# ─────────────────────────────────────────────────────────────────────────────
# 1. Crear un horario válido
# ─────────────────────────────────────────────────────────────────────────────

class TestCrearHorarioValido(HorarioTestBase):
    def test_crear_horario_valido(self):
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(Horario.objects.count(), 1)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Validación estricta y obligatoria del salón
# ─────────────────────────────────────────────────────────────────────────────

class TestSalonObligatorio(HorarioTestBase):
    def test_crear_sin_salon_falla(self):
        """El campo salon es obligatorio: si no se envía, debe responder 400."""
        data = self.payload()
        del data['salon']
        url = reverse('horario-list')
        resp = self.client.post(url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('salon', resp.data)

    def test_crear_con_salon_nulo_falla(self):
        """El campo salon no puede ser nulo."""
        data = self.payload(salon=None)
        url = reverse('horario-list')
        resp = self.client.post(url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_crear_con_salon_inexistente_falla(self):
        """Salón inexistente debe ser rechazado."""
        data = self.payload(salon=99999)
        url = reverse('horario-list')
        resp = self.client.post(url, data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ─────────────────────────────────────────────────────────────────────────────
# 3 & 4. Disponibilidad del salón (solapamientos y consecutivas)
# ─────────────────────────────────────────────────────────────────────────────

class TestSalonOcupado(HorarioTestBase):
    def test_mismo_salon_mismo_horario(self):
        self.crear_horario_db()
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            gestor=self.gestor2.pk,
            asignatura=self.asignatura2.pk,
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Horario.objects.count(), 1)

    def test_mismo_salon_solapamiento_parcial(self):
        self.crear_horario_db(hora_inicio=time(8, 0), hora_fin=time(10, 0))
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            gestor=self.gestor2.pk,
            asignatura=self.asignatura2.pk,
            hora_inicio='09:00:00',
            hora_fin='11:00:00',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Horario.objects.count(), 1)

    def test_clases_consecutivas_permitidas(self):
        self.crear_horario_db(hora_inicio=time(7, 0), hora_fin=time(9, 0))
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            gestor=self.gestor2.pk,
            asignatura=self.asignatura2.pk,
            hora_inicio='09:00:00',
            hora_fin='11:00:00',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(Horario.objects.count(), 2)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Disponibilidad del Gestor
# ─────────────────────────────────────────────────────────────────────────────

class TestGestorOcupado(HorarioTestBase):
    def test_gestor_dos_clases_simultaneas(self):
        self.crear_horario_db(salon=self.salon1)
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            salon=self.salon2.pk,
            asignatura=self.asignatura2.pk,
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Horario.objects.count(), 1)


# ─────────────────────────────────────────────────────────────────────────────
# 6 & 7. Límites diarios de horas y clases
# ─────────────────────────────────────────────────────────────────────────────

class TestLimiteHorasDia(HorarioTestBase):
    def test_supera_maximo_horas_dia(self):
        franjas = [
            (time(7, 0),  time(9, 0)),
            (time(9, 0),  time(11, 0)),
            (time(11, 0), time(13, 0)),
            (time(13, 0), time(15, 0)),
        ]
        salones = [self.salon1, self.salon2]
        asignaturas = [self.asignatura1, self.asignatura2]
        for i, (ini, fin) in enumerate(franjas):
            Horario.objects.create(
                asignatura  = asignaturas[i % 2],
                salon       = salones[i % 2],
                gestor      = self.gestor1,
                dia         = 'MAR',
                hora_inicio = ini,
                hora_fin    = fin,
                semestre    = '1',
            )

        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            dia='MAR',
            hora_inicio='15:00:00',
            hora_fin='17:00:00',
            salon=self.salon1.pk,
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class TestLimiteClasesDia(HorarioTestBase):
    def test_supera_maximo_clases_dia(self):
        from django.conf import settings
        max_clases = settings.HORARIO_CONFIG['MAX_CLASES_DIA']

        salones_extra = []
        for i in range(max_clases):
            s = Salon.objects.create(
                codigo=f'TEST{i}',
                capacidad=20,
                edificio='Test',
            )
            salones_extra.append(s)

        hora_base = 7
        for i in range(max_clases):
            Horario.objects.create(
                asignatura  = self.asignatura1,
                salon       = salones_extra[i],
                gestor      = self.gestor2,
                dia         = 'MIE',
                hora_inicio = time(hora_base + i * 2, 0),
                hora_fin    = time(hora_base + i * 2 + 2, 0),
                semestre    = '1',
            )

        salon_extra2 = Salon.objects.create(
            codigo='EXTRA',
            capacidad=20,
            edificio='Test',
        )
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            gestor=self.gestor2.pk,
            dia='MIE',
            salon=salon_extra2.pk,
            hora_inicio=f'{hora_base + max_clases * 2:02d}:00:00',
            hora_fin=f'{hora_base + max_clases * 2 + 2:02d}:00:00',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Duración y jornada
# ─────────────────────────────────────────────────────────────────────────────

class TestDuracionJornada(HorarioTestBase):
    def test_duracion_menor_que_minimo(self):
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            hora_inicio='07:00:00',
            hora_fin='08:00:00',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duracion_mayor_que_maximo(self):
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            hora_inicio='07:00:00',
            hora_fin='11:00:00',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_hora_fin_antes_de_jornada_fin(self):
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            hora_inicio='16:00:00',
            hora_fin='18:30:00',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_hora_inicio_antes_de_jornada_inicio(self):
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            hora_inicio='06:00:00',
            hora_fin='08:00:00',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_hora_fin_menor_que_inicio(self):
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            hora_inicio='10:00:00',
            hora_fin='08:00:00',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_formato_hora_invalido(self):
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            hora_inicio='no-es-hora',
            hora_fin='09:00:00',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ─────────────────────────────────────────────────────────────────────────────
# 9, 10 & 11. Exclusión de instancia actual al actualizar (sin falsos positivos)
# ─────────────────────────────────────────────────────────────────────────────

class TestActualizacionHorario(HorarioTestBase):
    def setUp(self):
        super().setUp()
        self.horario = self.crear_horario_db()

    def test_actualizacion_misma_franja_mismo_horario(self):
        """Editar el mismo registro con la misma franja no debe generar conflicto consigo mismo."""
        url = reverse('horario-detail', args=[self.horario.pk])
        resp = self.client.put(url, self.payload(
            hora_inicio='07:00:00',
            hora_fin='09:00:00',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

    def test_actualizacion_cambio_salon_disponible(self):
        """Actualizar cambiando a otro salón disponible debe funcionar."""
        url = reverse('horario-detail', args=[self.horario.pk])
        resp = self.client.put(url, self.payload(
            salon=self.salon2.pk,
            hora_inicio='07:00:00',
            hora_fin='09:00:00',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.horario.refresh_from_db()
        self.assertEqual(self.horario.salon, self.salon2)

    def test_actualizacion_parcial_patch_valida(self):
        """PATCH parcial excluye el ID propio y no genera falsos conflictos."""
        url = reverse('horario-detail', args=[self.horario.pk])
        resp = self.client.patch(url, {'semestre': '2'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.horario.refresh_from_db()
        self.assertEqual(self.horario.semestre, '2')

    def test_actualizacion_detecta_conflicto_con_otro_horario(self):
        """Si al actualizar choca con OTRO horario distinto, debe rechazarse con 400."""
        otro = self.crear_horario_db(
            hora_inicio=time(9, 0),
            hora_fin=time(11, 0),
            gestor=self.gestor1,
            salon=self.salon1,
        )
        url = reverse('horario-detail', args=[self.horario.pk])
        resp = self.client.put(url, self.payload(
            hora_inicio='09:00:00',
            hora_fin='11:00:00',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


# ─────────────────────────────────────────────────────────────────────────────
# 12. Semestres independientes: registros antiguos no generan conflicto
# ─────────────────────────────────────────────────────────────────────────────

class TestSemestres(HorarioTestBase):
    def test_mismo_salon_diferentes_semestres_no_choca(self):
        """
        Un horario de un semestre anterior (ej. '2024-1') no debe bloquear la
        programación de un nuevo semestre (ej. '2024-2') en el mismo salón y hora.
        """
        self.crear_horario_db(semestre='2024-1')
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            gestor=self.gestor2.pk,
            asignatura=self.asignatura2.pk,
            semestre='2024-2',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(Horario.objects.count(), 2)

    def test_mismo_gestor_diferentes_semestres_no_choca(self):
        """El gestor puede tener asignada la misma franja en semestres/periodos distintos."""
        self.crear_horario_db(semestre='2024-1')
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            salon=self.salon2.pk,
            semestre='2024-2',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)


# ─────────────────────────────────────────────────────────────────────────────
# 13. Validación especial de CAI (horarios fijos y restricción de cambios)
# ─────────────────────────────────────────────────────────────────────────────

class TestCaiValidacion(HorarioTestBase):
    def test_es_asignatura_cai_detecta_por_codigo_y_nombre(self):
        self.assertTrue(es_asignatura_cai(self.asignatura_cai))
        asig_cai2 = Asignatura.objects.create(
            codigo='MEDIT01',
            nombre='Campo de Aprendizaje Comunicativo',
            programa=self.programa,
            creditos=2,
        )
        self.assertTrue(es_asignatura_cai(asig_cai2))
        self.assertFalse(es_asignatura_cai(self.asignatura1))

    @override_settings(HORARIO_CONFIG={
        'MAX_CLASES_DIA': 4,
        'MAX_HORAS_DIA': 8,
        'DURACION_MIN_H': 2,
        'DURACION_MAX_H': 3,
        'JORNADA_INICIO': 7,
        'JORNADA_FIN': 18,
        'CAI_ASIGNATURAS': ['CAI101'],
        'CAI_HORARIOS_PREDEFINIDOS': {
            'CAI101': {'dia': 'VIE', 'hora_inicio': '08:00:00', 'hora_fin': '11:00:00'}
        }
    })
    def test_cai_rechaza_horario_distinto_a_predefinido(self):
        """Un CAI con franja fija predefinida debe rechazar asignaciones en otro día u hora."""
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            asignatura=self.asignatura_cai.pk,
            dia='LUN',
            hora_inicio='07:00:00',
            hora_fin='09:00:00',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('conflictos', resp.data)
        tipos = [c['tipo'] for c in resp.data['conflictos']]
        self.assertIn('CAI_HORARIO_FIJO', tipos)

    @override_settings(HORARIO_CONFIG={
        'MAX_CLASES_DIA': 4,
        'MAX_HORAS_DIA': 8,
        'DURACION_MIN_H': 2,
        'DURACION_MAX_H': 3,
        'JORNADA_INICIO': 7,
        'JORNADA_FIN': 18,
        'CAI_ASIGNATURAS': ['CAI101'],
        'CAI_HORARIOS_PREDEFINIDOS': {
            'CAI101': {'dia': 'VIE', 'hora_inicio': '08:00:00', 'hora_fin': '11:00:00'}
        }
    })
    def test_cai_permite_horario_predefinido_exacto(self):
        """Un CAI asignado con su franja predefinida exacta debe guardarse exitosamente."""
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            asignatura=self.asignatura_cai.pk,
            dia='VIE',
            hora_inicio='08:00:00',
            hora_fin='11:00:00',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_cai_restringe_modificacion_arbitraria_al_editar(self):
        """Una asignatura CAI con horario ya guardado no permite cambios arbitrarios en su franja fija."""
        horario_cai = self.crear_horario_db(
            asignatura=self.asignatura_cai,
            dia='JUE',
            hora_inicio=time(8, 0),
            hora_fin=time(11, 0),
        )
        url = reverse('horario-detail', args=[horario_cai.pk])
        resp = self.client.put(url, self.payload(
            asignatura=self.asignatura_cai.pk,
            dia='VIE',  # intento de cambiar día
            hora_inicio='08:00:00',
            hora_fin='11:00:00',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        tipos = [c['tipo'] for c in resp.data['conflictos']]
        self.assertIn('CAI_EDICION_NO_PERMITIDA', tipos)


# ─────────────────────────────────────────────────────────────────────────────
# 14. Endpoint POST /api/horarios/validar/
# ─────────────────────────────────────────────────────────────────────────────

class TestEndpointValidar(HorarioTestBase):
    def _url_validar(self):
        return reverse('horario-validar')

    def test_validar_horario_valido(self):
        resp = self.client.post(self._url_validar(), {
            'asignatura':  self.asignatura1.pk,
            'salon':       self.salon1.pk,
            'gestor':      self.gestor1.pk,
            'dia':         'JUE',
            'hora_inicio': '07:00:00',
            'hora_fin':    '09:00:00',
            'semestre':    '1',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['valido'])
        self.assertEqual(resp.data['conflictos'], [])

    def test_validar_horario_con_conflicto_salon(self):
        self.crear_horario_db()
        resp = self.client.post(self._url_validar(), {
            'asignatura':  self.asignatura2.pk,
            'salon':       self.salon1.pk,
            'gestor':      self.gestor2.pk,
            'dia':         'LUN',
            'hora_inicio': '07:00:00',
            'hora_fin':    '09:00:00',
            'semestre':    '1',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['valido'])
        tipos = [c['tipo'] for c in resp.data['conflictos']]
        self.assertIn('SALON_OCUPADO', tipos)

    def test_validar_con_horario_id_excluye_propio(self):
        """Al pasar horario_id en /validar/, se excluye el propio horario de la comprobación."""
        h = self.crear_horario_db()
        resp = self.client.post(self._url_validar(), {
            'asignatura':  self.asignatura1.pk,
            'salon':       self.salon1.pk,
            'gestor':      self.gestor1.pk,
            'dia':         'LUN',
            'hora_inicio': '07:00:00',
            'hora_fin':    '09:00:00',
            'semestre':    '1',
            'horario_id':  h.pk,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data['valido'])

    def test_validar_salon_requerido(self):
        """Si falta el salón en /validar/, debe indicar el conflicto SALON_REQUERIDO."""
        resp = self.client.post(self._url_validar(), {
            'asignatura':  self.asignatura1.pk,
            'gestor':      self.gestor1.pk,
            'dia':         'LUN',
            'hora_inicio': '07:00:00',
            'hora_fin':    '09:00:00',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['valido'])
        tipos = [c['tipo'] for c in resp.data['conflictos']]
        self.assertIn('SALON_REQUERIDO', tipos)

    def test_validar_detecta_conflicto_gestor(self):
        self.crear_horario_db(salon=self.salon1)
        resp = self.client.post(self._url_validar(), {
            'asignatura':  self.asignatura2.pk,
            'salon':       self.salon2.pk,
            'gestor':      self.gestor1.pk,
            'dia':         'LUN',
            'hora_inicio': '07:00:00',
            'hora_fin':    '09:00:00',
            'semestre':    '1',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertFalse(resp.data['valido'])
        tipos = [c['tipo'] for c in resp.data['conflictos']]
        self.assertIn('GESTOR_OCUPADO', tipos)


# ─────────────────────────────────────────────────────────────────────────────
# 15. Horario inválido nunca persiste
# ─────────────────────────────────────────────────────────────────────────────

class TestHorarioInvalidoNoSeGuarda(HorarioTestBase):
    def test_horario_invalido_no_persiste(self):
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            hora_inicio='07:00:00',
            hora_fin='08:00:00',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Horario.objects.count(), 0)

    def test_salon_ocupado_no_persiste(self):
        self.crear_horario_db()
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(
            gestor=self.gestor2.pk,
            asignatura=self.asignatura2.pk,
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Horario.objects.count(), 1)


# ─────────────────────────────────────────────────────────────────────────────
# Tests directos del servicio
# ─────────────────────────────────────────────────────────────────────────────

class TestServicioValidacion(HorarioTestBase):
    def test_solapamiento_completo(self):
        self.crear_horario_db(hora_inicio=time(7, 0), hora_fin=time(9, 0))
        conflictos = validar_salon_disponible(
            self.salon1, 'LUN', time(7, 0), time(9, 0), semestre='1'
        )
        self.assertTrue(any(c['tipo'] == 'SALON_OCUPADO' for c in conflictos))

    def test_solapamiento_parcial(self):
        self.crear_horario_db(hora_inicio=time(8, 0), hora_fin=time(10, 0))
        conflictos = validar_salon_disponible(
            self.salon1, 'LUN', time(7, 0), time(9, 0), semestre='1'
        )
        self.assertTrue(any(c['tipo'] == 'SALON_OCUPADO' for c in conflictos))

    def test_consecutivas_no_solapan(self):
        self.crear_horario_db(hora_inicio=time(7, 0), hora_fin=time(9, 0))
        conflictos = validar_salon_disponible(
            self.salon1, 'LUN', time(9, 0), time(11, 0), semestre='1'
        )
        self.assertEqual(conflictos, [])

    def test_excluir_propio_horario(self):
        h = self.crear_horario_db(hora_inicio=time(7, 0), hora_fin=time(9, 0))
        conflictos = validar_salon_disponible(
            self.salon1, 'LUN', time(7, 0), time(9, 0), excluir_id=h.pk, semestre='1'
        )
        self.assertEqual(conflictos, [])

    def test_registros_distintos_semestres_no_colisionan(self):
        self.crear_horario_db(hora_inicio=time(7, 0), hora_fin=time(9, 0), semestre='2024-1')
        conflictos = validar_salon_disponible(
            self.salon1, 'LUN', time(7, 0), time(9, 0), semestre='2024-2'
        )
        self.assertEqual(conflictos, [])
