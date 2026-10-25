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

from .models import Usuario, Programa, Asignatura, Salon, Horario, RegistroAuditoria
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

        # Los endpoints de horarios exigen JWT: por defecto se actúa como el coordinador
        # del programa (cada prueba puede cambiar de usuario con force_authenticate).
        self.client = APIClient()
        self.client.force_authenticate(user=self.coordinador)

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
            hora_fin='07:30:00',
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
    """
    El campo `semestre` es el semestre de la malla (1-9), no el periodo académico.
    Un salón o un gestor es único: NO puede estar en dos semestres a la vez.
    """
    def _tipos(self, resp):
        return [c['tipo'] for c in resp.data['conflictos']]

    def test_mismo_salon_diferentes_semestres_choca(self):
        self.crear_horario_db(semestre='1')
        resp = self.client.post(reverse('horario-list'), self.payload(
            gestor=self.gestor2.pk,
            asignatura=self.asignatura2.pk,
            semestre='2',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('SALON_OCUPADO', self._tipos(resp))
        self.assertEqual(Horario.objects.count(), 1)

    def test_mismo_gestor_diferentes_semestres_choca(self):
        self.crear_horario_db(semestre='1')
        resp = self.client.post(reverse('horario-list'), self.payload(
            salon=self.salon2.pk,
            asignatura=self.asignatura2.pk,
            semestre='2',
        ), format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('GESTOR_OCUPADO', self._tipos(resp))


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
            hora_fin='07:30:00',
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

    def test_registros_distintos_semestres_si_colisionan(self):
        self.crear_horario_db(hora_inicio=time(7, 0), hora_fin=time(9, 0), semestre='1')
        conflictos = validar_salon_disponible(
            self.salon1, 'LUN', time(7, 0), time(9, 0), semestre='2'
        )
        self.assertTrue(any(c['tipo'] == 'SALON_OCUPADO' for c in conflictos))


# ─────────────────────────────────────────────────────────────────────────────
# Pruebas de Auditoría y Estadísticas
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditoriaEndpoints(HorarioTestBase):
    def setUp(self):
        super().setUp()
        self.usuario_ti = Usuario.objects.create_user(
            username='tecnico_ti',
            email='ti@ucundinamarca.edu.co',
            password='Password123',
            rol='TI',
            first_name='Laura',
            last_name='Gómez',
        )
        self.estudiante = Usuario.objects.create_user(
            username='estudiante1',
            email='est1@ucundinamarca.edu.co',
            password='Password123',
            rol='ES',
        )
        self.coordinador2 = Usuario.objects.create_user(
            username='coord2',
            email='coord2@ucundinamarca.edu.co',
            password='Coord456',
            rol='CO',
        )
        self.programa2 = Programa.objects.create(
            nombre='Ingeniería Ambiental',
            codigo='IAMB',
            coordinador=self.coordinador2,
        )
        self.asignatura_prog2 = Asignatura.objects.create(
            codigo='AMB101',
            nombre='Ecología',
            programa=self.programa2,
            creditos=3,
        )

    def test_auditoria_acceso_no_autenticado_denegado(self):
        self.client.force_authenticate(user=None)
        resp = self.client.get('/api/auditoria/')
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_auditoria_acceso_rol_no_permitido_denegado(self):
        self.client.force_authenticate(user=self.estudiante)
        resp = self.client.get('/api/auditoria/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.gestor1)
        resp = self.client.get('/api/auditoria/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_auditoria_ti_ve_todos_los_registros(self):
        RegistroAuditoria.objects.create(
            usuario=self.coordinador,
            accion='CREATE',
            modelo='Horario',
            objeto_id=1,
            descripcion='Horario 1',
        )
        RegistroAuditoria.objects.create(
            usuario=self.coordinador2,
            accion='CREATE',
            modelo='Horario',
            objeto_id=2,
            descripcion='Horario 2',
        )

        self.client.force_authenticate(user=self.usuario_ti)
        resp = self.client.get('/api/auditoria/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 2)

    def test_auditoria_coordinador_ve_solo_su_programa(self):
        h1 = self.crear_horario_db(asignatura=self.asignatura1)
        h2 = self.crear_horario_db(asignatura=self.asignatura_prog2, hora_inicio=time(9, 0), hora_fin=time(11, 0))

        RegistroAuditoria.objects.create(
            usuario=self.coordinador,
            accion='CREATE',
            modelo='Horario',
            objeto_id=h1.id,
            descripcion='Horario de Prog 1',
        )
        RegistroAuditoria.objects.create(
            usuario=self.coordinador2,
            accion='CREATE',
            modelo='Horario',
            objeto_id=h2.id,
            descripcion='Horario de Prog 2',
        )

        self.client.force_authenticate(user=self.coordinador)
        resp = self.client.get('/api/auditoria/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['objeto_id'], h1.id)

    def test_auditoria_paginacion(self):
        self.client.force_authenticate(user=self.usuario_ti)
        for i in range(15):
            RegistroAuditoria.objects.create(
                usuario=self.usuario_ti,
                accion='CREATE',
                modelo='Horario',
                objeto_id=i + 1,
                descripcion=f'Registro {i}',
            )

        resp = self.client.get('/api/auditoria/?limit=5&offset=5')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['count'], 15)
        self.assertEqual(len(resp.data['results']), 5)
        self.assertIsNotNone(resp.data['next'])
        self.assertIsNotNone(resp.data['previous'])

    def test_auditoria_ultimos_5_cambios(self):
        self.client.force_authenticate(user=self.usuario_ti)
        for i in range(10):
            RegistroAuditoria.objects.create(
                usuario=self.usuario_ti,
                accion='CREATE',
                modelo='Horario',
                objeto_id=i + 1,
                descripcion=f'Registro {i}',
            )

        resp = self.client.get('/api/auditoria/ultimos/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsInstance(resp.data, list)
        self.assertEqual(len(resp.data), 5)
        self.assertEqual(resp.data[0]['objeto_id'], 10)

    def test_auditoria_estadisticas_formato_graficos(self):
        self.client.force_authenticate(user=self.usuario_ti)
        RegistroAuditoria.objects.create(
            usuario=self.coordinador,
            accion='CREATE',
            modelo='Horario',
            objeto_id=1,
        )
        RegistroAuditoria.objects.create(
            usuario=self.usuario_ti,
            accion='UPDATE',
            modelo='Horario',
            objeto_id=1,
        )

        resp = self.client.get('/api/auditoria/estadisticas/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data
        self.assertIn('cambios_por_periodo', data)
        self.assertIn('cambios_por_accion', data)
        self.assertIn('cambios_por_modelo', data)
        self.assertIn('cambios_por_usuario', data)
        self.assertIn('cambios_por_rol', data)
        self.assertIn('estado_horarios', data)
        self.assertIn('total_registros', data)
        self.assertEqual(data['total_registros'], 2)

    def test_auditoria_estadisticas_filtro_periodo(self):
        self.client.force_authenticate(user=self.usuario_ti)
        RegistroAuditoria.objects.create(
            usuario=self.coordinador,
            accion='CREATE',
            modelo='Horario',
            objeto_id=1,
        )

        resp_dia = self.client.get('/api/auditoria/estadisticas/?periodo=dia')
        self.assertEqual(resp_dia.status_code, status.HTTP_200_OK)
        self.assertTrue(len(resp_dia.data['cambios_por_periodo']) >= 1)

        resp_sem = self.client.get('/api/auditoria/estadisticas/?periodo=semana')
        self.assertEqual(resp_sem.status_code, status.HTTP_200_OK)
        self.assertTrue(len(resp_sem.data['cambios_por_periodo']) >= 1)

    def test_ciclo_vida_horario_registra_auditoria(self):
        self.client.force_authenticate(user=self.coordinador)

        # 1. CREATE
        url = reverse('horario-list')
        resp = self.client.post(url, self.payload(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        horario_id = resp.data['id']

        log_create = RegistroAuditoria.objects.filter(
            modelo='Horario', objeto_id=horario_id, accion='CREATE'
        ).first()
        self.assertIsNotNone(log_create)
        self.assertEqual(log_create.usuario, self.coordinador)

        # 2. UPDATE
        url_detail = reverse('horario-detail', kwargs={'pk': horario_id})
        resp = self.client.put(url_detail, self.payload(salon=self.salon2.pk), format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        log_update = RegistroAuditoria.objects.filter(
            modelo='Horario', objeto_id=horario_id, accion='UPDATE'
        ).first()
        self.assertIsNotNone(log_update)
        self.assertIsNotNone(log_update.datos_antes)
        self.assertIsNotNone(log_update.datos_despues)

        # 3. DELETE
        resp = self.client.delete(url_detail)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)

        log_delete = RegistroAuditoria.objects.filter(
            modelo='Horario', objeto_id=horario_id, accion='DELETE'
        ).first()
        self.assertIsNotNone(log_delete)
        self.assertEqual(log_delete.objeto_id, horario_id)


# ─────────────────────────────────────────────────────────────────────────────
# 16. Restricciones del documento (grupo, horas, tipo de aula, 40 h, etc.)
# ─────────────────────────────────────────────────────────────────────────────

from .models import Matricula


class TestRestriccionesDocumento(HorarioTestBase):
    def _post(self, **kw):
        return self.client.post(reverse('horario-list'), self.payload(**kw), format='json')

    def _tipos(self, resp):
        return [c['tipo'] for c in resp.data['conflictos']]

    # -- cruce de GRUPO ------------------------------------------------------
    def test_cruce_de_grupo_mismo_semestre_y_programa(self):
        self.crear_horario_db()                       # asig1, gestor1, salon1, LUN 7-9, sem 1
        resp = self._post(asignatura=self.asignatura2.pk, gestor=self.gestor2.pk,
                          salon=self.salon2.pk)       # recursos distintos, mismo grupo
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self._tipos(resp), ['GRUPO_OCUPADO'])

    def test_otro_semestre_con_recursos_distintos_no_choca(self):
        self.crear_horario_db()
        resp = self._post(asignatura=self.asignatura2.pk, gestor=self.gestor2.pk,
                          salon=self.salon2.pk, semestre='2')
        self.assertEqual(resp.status_code, 201, getattr(resp, 'data', None))

    # -- horas por semana de la asignatura ----------------------------------
    def test_no_supera_horas_semana_de_la_asignatura(self):
        self.asignatura1.horas_semana = 4
        self.asignatura1.save()
        self.assertEqual(self._post().status_code, 201)                       # 2 h
        self.assertEqual(self._post(dia='MAR').status_code, 201)              # 4 h
        resp = self._post(dia='MIE')                                          # 6 h > 4
        self.assertEqual(resp.status_code, 400)
        self.assertIn('HORAS_ASIGNATURA_EXCEDIDAS', self._tipos(resp))

    def test_editar_bloque_no_cuenta_dos_veces_sus_horas(self):
        self.asignatura1.horas_semana = 2
        self.asignatura1.save()
        h = self.crear_horario_db()
        resp = self.client.put(reverse('horario-detail', args=[h.pk]),
                               self.payload(dia='MAR'), format='json')
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))

    def test_horas_semana_cero_no_bloquea_pero_advierte(self):
        resp = self.client.post(reverse('horario-validar'), self.payload(), format='json')
        self.assertTrue(resp.data['valido'])
        self.assertIn('HORAS_NO_DEFINIDAS', [a['tipo'] for a in resp.data['advertencias']])

    # -- tipo de aula --------------------------------------------------------
    def test_nucleo_de_computo_exige_sala_de_computo(self):
        self.asignatura1.requiere_sala_computo = True
        self.asignatura1.save()
        resp = self._post()                                   # salon1 es AULA
        self.assertEqual(resp.status_code, 400)
        self.assertIn('TIPO_AULA_INCORRECTO', self._tipos(resp))
        sala = Salon.objects.create(codigo='SC1', capacidad=30, edificio='G', tipo='COMP')
        self.assertEqual(self._post(salon=sala.pk).status_code, 201)

    # -- capacidad y tamaño de grupo ----------------------------------------
    def _matricular(self, n, asignatura=None):
        asignatura = asignatura or self.asignatura1
        for i in range(n):
            est = Usuario.objects.create_user(
                username=f'est{asignatura.pk}_{i}', password='Abc12345', rol='ES',
                email=f'e{asignatura.pk}_{i}@ucundinamarca.edu.co')
            Matricula.objects.create(estudiante=est, asignatura=asignatura, semestre='1')

    def test_capacidad_insuficiente(self):
        self._matricular(26)
        resp = self._post(salon=self.salon2.pk)               # capacidad 25
        self.assertEqual(resp.status_code, 400)
        self.assertIn('CAPACIDAD_INSUFICIENTE', self._tipos(resp))

    def test_grupo_fuera_de_8_a_35_solo_advierte(self):
        self._matricular(5)
        resp = self.client.post(reverse('horario-validar'), self.payload(), format='json')
        self.assertTrue(resp.data['valido'])
        self.assertIn('GRUPO_BAJO_MINIMO', [a['tipo'] for a in resp.data['advertencias']])

    # -- 40 h semanales por gestor ------------------------------------------
    def test_limite_40_horas_semanales_por_gestor(self):
        # 5 días x 8 h = 40 h para gestor1 (varios núcleos/semestres para no chocar)
        for i, dia in enumerate(['LUN', 'MAR', 'MIE', 'JUE', 'VIE']):
            for j, (ini, fin) in enumerate([(7, 10), (10, 13), (14, 16)]):   # 3+3+2 = 8 h
                asig = Asignatura.objects.create(codigo=f'X{i}{j}', nombre=f'N{i}{j}',
                                                 programa=self.programa, creditos=2)
                self.crear_horario_db(asignatura=asig, dia=dia, semestre=str(j + 3),
                                      hora_inicio=time(ini, 0), hora_fin=time(fin, 0),
                                      salon=Salon.objects.create(codigo=f'S{i}{j}', capacidad=30,
                                                                 edificio='Z'))
        resp = self._post(dia='LUN', hora_inicio='16:00:00', hora_fin='18:00:00',
                          asignatura=self.asignatura2.pk, semestre='1')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('LIMITE_HORAS_SEMANA', self._tipos(resp))

    # -- días y semestre ------------------------------------------------------
    def test_sabado_solo_para_cai(self):
        resp = self._post(dia='SAB')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('DIA_NO_PERMITIDO', self._tipos(resp))

    def test_semestre_debe_coincidir_con_el_del_nucleo(self):
        self.asignatura1.semestre = '3'
        self.asignatura1.save()
        resp = self._post(semestre='1')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('SEMESTRE_NO_COINCIDE', self._tipos(resp))
        self.assertEqual(self._post(semestre='3').status_code, 201)

    # -- restricciones blandas ----------------------------------------------
    def test_advierte_almuerzo_y_ventana_sin_bloquear(self):
        self.crear_horario_db(hora_inicio=time(7, 0), hora_fin=time(9, 0))
        resp = self.client.post(reverse('horario-validar'), self.payload(
            asignatura=self.asignatura2.pk, gestor=self.gestor2.pk, salon=self.salon2.pk,
            hora_inicio='12:00:00', hora_fin='14:00:00'), format='json')
        self.assertTrue(resp.data['valido'], resp.data)
        tipos = [a['tipo'] for a in resp.data['advertencias']]
        self.assertIn('ALMUERZO_INVADIDO', tipos)
        self.assertIn('VENTANA_HORARIA', tipos)

    def test_validar_acepta_excluir_id_y_devuelve_resumen(self):
        h = self.crear_horario_db()
        resp = self.client.post(reverse('horario-validar'),
                                self.payload(excluir_id=h.pk), format='json')
        self.assertTrue(resp.data['valido'], resp.data)
        self.assertIn('horas_semana_gestor', resp.data['resumen'])

    # -- asignatura: horas obligatorias al crear ----------------------------
    def test_asignatura_exige_horas_semana_al_crear(self):
        ti = Usuario.objects.create_user(username='ti_x', email='tix@ucundinamarca.edu.co',
                                         password='Abc12345', rol='TI')
        self.client.force_authenticate(user=ti)
        base = {'codigo': 'N1', 'nombre': 'Nuevo', 'programa': self.programa.pk,
                'creditos': 3, 'semestre': '1', 'gestores': []}
        self.assertEqual(self.client.post(reverse('asignatura-list'), base, format='json').status_code, 400)
        base['horas_semana'] = 4
        self.assertEqual(self.client.post(reverse('asignatura-list'), base, format='json').status_code, 201)


# ─────────────────────────────────────────────────────────────────────────────
# 17. CRUD administrativo (rol TI) y permisos de escritura
# ─────────────────────────────────────────────────────────────────────────────

class TestCRUDAdministrativo(HorarioTestBase):
    def setUp(self):
        super().setUp()
        self.ti = Usuario.objects.create_user(
            username='ti1', email='ti1@ucundinamarca.edu.co', password='Admin123', rol='TI')
        self.est = Usuario.objects.create_user(
            username='est1', email='est1@ucundinamarca.edu.co', password='Estud123', rol='ES')
        self.est2 = Usuario.objects.create_user(
            username='est2', email='est2@ucundinamarca.edu.co', password='Estud123', rol='ES')

    def como(self, user):
        self.client.force_authenticate(user=user)

    def usuario_payload(self, **kw):
        d = {'username': 'nuevo', 'email': 'nuevo@ucundinamarca.edu.co',
             'password': 'Clave123', 'rol': 'ES'}
        d.update(kw)
        return d

    # ── Permisos ────────────────────────────────────────────────────────────
    def test_lectura_abierta_y_escritura_exige_autenticacion(self):
        self.client.force_authenticate(user=None)
        # Programas y asignaturas (con alcance por programa) exigen sesión también para leer.
        for nombre in ('programa', 'asignatura'):
            self.assertEqual(self.client.get(reverse(f'{nombre}-list')).status_code, 401, nombre)
        for nombre in ('usuario', 'programa', 'salon', 'asignatura', 'matricula'):
            if nombre not in ('programa', 'asignatura'):
                self.assertEqual(self.client.get(reverse(f'{nombre}-list')).status_code, 200, nombre)
            self.assertEqual(self.client.post(reverse(f'{nombre}-list'), {}, format='json').status_code,
                             401, nombre)

    def test_roles_no_ti_no_pueden_escribir_entidades_administrativas(self):
        cuerpos = {
            'usuario': self.usuario_payload(),
            'programa': {'nombre': 'X', 'codigo': 'XX1', 'coordinador': self.coordinador.pk},
            'salon': {'codigo': 'Z1', 'capacidad': 20, 'edificio': 'G'},
            'asignatura': {'codigo': 'Z9', 'nombre': 'Z', 'programa': self.programa.pk,
                           'creditos': 2, 'horas_semana': 2},
        }
        for user in (self.coordinador, self.gestor1, self.est):
            self.como(user)
            for nombre, cuerpo in cuerpos.items():
                self.assertEqual(self.client.post(reverse(f'{nombre}-list'), cuerpo,
                                                  format='json').status_code, 403, (user.rol, nombre))
            self.assertEqual(self.client.delete(
                reverse('salon-detail', args=[self.salon2.pk])).status_code, 403)
        self.assertTrue(Salon.objects.filter(pk=self.salon2.pk).exists())

    # ── Usuarios ────────────────────────────────────────────────────────────
    def test_usuario_crud_completo(self):
        self.como(self.ti)
        r = self.client.post(reverse('usuario-list'), self.usuario_payload(), format='json')
        self.assertEqual(r.status_code, 201, r.data)
        uid = r.data['id']
        self.assertNotIn('password', r.data)
        u = Usuario.objects.get(pk=uid)
        self.assertNotEqual(u.password, 'Clave123')
        self.assertTrue(u.check_password('Clave123'))

        self.assertEqual(self.client.get(reverse('usuario-detail', args=[uid])).data['username'], 'nuevo')
        self.assertEqual(self.client.get(reverse('usuario-list') + '?rol=ES').status_code, 200)

        # PATCH sin contraseña conserva la actual
        r = self.client.patch(reverse('usuario-detail', args=[uid]),
                              {'first_name': 'Ana'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        u.refresh_from_db()
        self.assertEqual(u.first_name, 'Ana')
        self.assertTrue(u.check_password('Clave123'))

        # PUT sin contraseña también funciona
        r = self.client.put(reverse('usuario-detail', args=[uid]),
                            {'username': 'nuevo', 'email': 'nuevo@ucundinamarca.edu.co', 'rol': 'ES'},
                            format='json')
        self.assertEqual(r.status_code, 200, r.data)

        # Cambio de contraseña: queda con hash
        r = self.client.patch(reverse('usuario-detail', args=[uid]),
                              {'password': 'Otra1234'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        u.refresh_from_db()
        self.assertTrue(u.check_password('Otra1234'))
        self.assertNotEqual(u.password, 'Otra1234')

        self.assertEqual(self.client.delete(reverse('usuario-detail', args=[uid])).status_code, 204)
        self.assertFalse(Usuario.objects.filter(pk=uid).exists())

    def test_usuario_validaciones(self):
        self.como(self.ti)
        url = reverse('usuario-list')
        r = self.client.post(url, self.usuario_payload(password=''), format='json')
        self.assertEqual(r.status_code, 400)
        r = self.client.post(url, {k: v for k, v in self.usuario_payload().items() if k != 'password'},
                             format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('password', r.data)
        r = self.client.post(url, self.usuario_payload(email='x@gmail.com'), format='json')
        self.assertEqual(r.status_code, 400)
        # username y correo duplicados
        r = self.client.post(url, self.usuario_payload(username='gestor1'), format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('username', r.data)
        r = self.client.post(url, self.usuario_payload(email='gestor1@ucundinamarca.edu.co'), format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('email', r.data)
        # editar sin cambiar el propio correo no es duplicado
        r = self.client.patch(reverse('usuario-detail', args=[self.gestor1.pk]),
                              {'email': 'gestor1@ucundinamarca.edu.co'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)

    def test_usuario_con_dependencias_no_se_elimina_ni_cambia_de_rol(self):
        self.como(self.ti)
        self.crear_horario_db()                       # gestor1 con horario
        r = self.client.delete(reverse('usuario-detail', args=[self.gestor1.pk]))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['codigo'], 'ELIMINACION_BLOQUEADA')
        self.assertEqual(r.data['dependencias']['horarios_como_gestor'], 1)
        self.assertTrue(Horario.objects.exists())     # nada se borró en cascada

        r = self.client.patch(reverse('usuario-detail', args=[self.gestor1.pk]),
                              {'rol': 'ES'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('rol', r.data)

        # coordinador de un programa
        r = self.client.delete(reverse('usuario-detail', args=[self.coordinador.pk]))
        self.assertEqual(r.status_code, 409)
        self.assertIn('programas_coordinados', r.data['dependencias'])

    def test_ti_no_puede_eliminarse_a_si_mismo(self):
        self.como(self.ti)
        r = self.client.delete(reverse('usuario-detail', args=[self.ti.pk]))
        self.assertEqual(r.status_code, 409)
        self.assertTrue(Usuario.objects.filter(pk=self.ti.pk).exists())

    # ── Programas ───────────────────────────────────────────────────────────
    def test_programa_crud_y_validaciones(self):
        self.como(self.ti)
        coord2 = Usuario.objects.create_user(username='coord9', email='c9@ucundinamarca.edu.co',
                                             password='Coord123', rol='CO')
        body = {'nombre': 'Ing. Ambiental', 'codigo': 'IAMB', 'coordinador': coord2.pk}
        r = self.client.post(reverse('programa-list'), body, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        pid = r.data['id']
        # código duplicado
        self.assertEqual(self.client.post(reverse('programa-list'), body, format='json').status_code, 400)
        # el coordinador debe tener rol CO
        r = self.client.post(reverse('programa-list'),
                             {'nombre': 'P', 'codigo': 'P2', 'coordinador': self.gestor1.pk}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('coordinador', r.data)
        # coordinador inexistente
        r = self.client.post(reverse('programa-list'),
                             {'nombre': 'P', 'codigo': 'P3', 'coordinador': 99999}, format='json')
        self.assertEqual(r.status_code, 400)
        # actualizar
        r = self.client.patch(reverse('programa-detail', args=[pid]), {'nombre': 'Ambiental'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(reverse('programa-detail', args=[pid])).data['nombre'], 'Ambiental')
        # eliminar (sin dependencias)
        self.assertEqual(self.client.delete(reverse('programa-detail', args=[pid])).status_code, 204)

    def test_programa_con_asignaturas_no_se_elimina(self):
        self.como(self.ti)
        r = self.client.delete(reverse('programa-detail', args=[self.programa.pk]))
        self.assertEqual(r.status_code, 409)
        self.assertIn('asignaturas', r.data['dependencias'])
        self.assertTrue(Asignatura.objects.filter(pk=self.asignatura1.pk).exists())

    # ── Salones ─────────────────────────────────────────────────────────────
    def test_salon_crud_y_validaciones(self):
        self.como(self.ti)
        body = {'codigo': 'Z-101', 'capacidad': 30, 'edificio': 'Z', 'tipo': 'COMP'}
        r = self.client.post(reverse('salon-list'), body, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        sid = r.data['id']
        self.assertEqual(r.data['tipo'], 'COMP')
        self.assertEqual(self.client.post(reverse('salon-list'), body, format='json').status_code, 400)
        self.assertEqual(self.client.post(reverse('salon-list'),
                         {**body, 'codigo': 'Z-102', 'capacidad': 5}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(reverse('salon-detail', args=[sid]),
                         {'capacidad': 40}, format='json').status_code, 200)
        self.assertEqual(self.client.delete(reverse('salon-detail', args=[sid])).status_code, 204)

    def test_salon_con_horarios_no_se_elimina_ni_queda_incompatible(self):
        self.como(self.ti)
        self.crear_horario_db()                       # salon1
        r = self.client.delete(reverse('salon-detail', args=[self.salon1.pk]))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['dependencias']['horarios'], 1)
        # bajar la capacidad por debajo de los matriculados del núcleo programado
        for i in range(12):
            e = Usuario.objects.create_user(username=f'm{i}', email=f'm{i}@ucundinamarca.edu.co',
                                            password='Abc12345', rol='ES')
            Matricula.objects.create(estudiante=e, asignatura=self.asignatura1, semestre='1')
        r = self.client.patch(reverse('salon-detail', args=[self.salon1.pk]),
                              {'capacidad': 10}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('salon', r.data)

    # ── Asignaturas (núcleos temáticos) ─────────────────────────────────────
    def test_asignatura_crud_y_validaciones(self):
        self.como(self.ti)
        body = {'codigo': 'N-1', 'nombre': 'Nuevo núcleo', 'programa': self.programa.pk,
                'creditos': 3, 'horas_semana': 4, 'semestre': '2', 'gestores': [self.gestor1.pk]}
        r = self.client.post(reverse('asignatura-list'), body, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        aid = r.data['id']
        self.assertEqual(self.client.post(reverse('asignatura-list'), body, format='json').status_code, 400)
        # programa inexistente / gestor que no es GC
        self.assertEqual(self.client.post(reverse('asignatura-list'),
                         {**body, 'codigo': 'N-2', 'programa': 99999}, format='json').status_code, 400)
        self.assertEqual(self.client.post(reverse('asignatura-list'),
                         {**body, 'codigo': 'N-3', 'gestores': [self.est.pk]}, format='json').status_code, 400)
        # PUT sin horas_semana (núcleos antiguos) sigue siendo válido
        put = {k: v for k, v in body.items() if k != 'horas_semana'}
        put['nombre'] = 'Renombrado'
        r = self.client.put(reverse('asignatura-detail', args=[aid]), put, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.client.delete(reverse('asignatura-detail', args=[aid])).status_code, 204)

    def test_asignatura_con_horarios_o_matriculas_no_se_elimina(self):
        self.como(self.ti)
        self.crear_horario_db()
        r = self.client.delete(reverse('asignatura-detail', args=[self.asignatura1.pk]))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['dependencias']['horarios'], 1)
        Matricula.objects.create(estudiante=self.est, asignatura=self.asignatura2, semestre='1')
        r = self.client.delete(reverse('asignatura-detail', args=[self.asignatura2.pk]))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data['dependencias']['matriculas'], 1)

    # ── Matrículas ──────────────────────────────────────────────────────────
    def test_matricula_crud_ti(self):
        self.como(self.ti)
        body = {'estudiante': self.est.pk, 'asignatura': self.asignatura1.pk, 'semestre': '1'}
        r = self.client.post(reverse('matricula-list'), body, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        mid = r.data['id']
        self.assertEqual(self.client.post(reverse('matricula-list'), body, format='json').status_code, 400)
        # PUT con los mismos datos no se rechaza por "duplicarse consigo misma"
        self.assertEqual(self.client.put(reverse('matricula-detail', args=[mid]),
                                         body, format='json').status_code, 200)
        r = self.client.patch(reverse('matricula-detail', args=[mid]),
                              {'asignatura': self.asignatura2.pk}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        # el estudiante debe tener rol ES
        r = self.client.post(reverse('matricula-list'),
                             {**body, 'estudiante': self.gestor1.pk}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.delete(reverse('matricula-detail', args=[mid])).status_code, 204)

    def test_matricula_permisos_por_rol(self):
        body = {'estudiante': self.est.pk, 'asignatura': self.asignatura1.pk, 'semestre': '1'}
        self.como(self.est)
        self.assertEqual(self.client.post(reverse('matricula-list'), body, format='json').status_code, 201)
        # no puede matricular a otro estudiante
        self.assertEqual(self.client.post(reverse('matricula-list'),
                         {**body, 'estudiante': self.est2.pk}, format='json').status_code, 403)
        m_otro = Matricula.objects.create(estudiante=self.est2, asignatura=self.asignatura2, semestre='1')
        self.assertEqual(self.client.delete(reverse('matricula-detail', args=[m_otro.pk])).status_code, 403)
        self.assertEqual(self.client.patch(reverse('matricula-detail', args=[m_otro.pk]),
                         {'semestre': '2'}, format='json').status_code, 403)
        propia = Matricula.objects.get(estudiante=self.est)
        self.assertEqual(self.client.delete(reverse('matricula-detail', args=[propia.pk])).status_code, 204)
        # gestor: sin escritura; coordinador: sí
        self.como(self.gestor1)
        self.assertEqual(self.client.post(reverse('matricula-list'), body, format='json').status_code, 403)
        self.como(self.coordinador)
        self.assertEqual(self.client.post(reverse('matricula-list'), body, format='json').status_code, 201)

    # ── Auditoría ───────────────────────────────────────────────────────────
    def test_crud_queda_registrado_en_auditoria(self):
        from .models import RegistroAuditoria
        self.como(self.ti)
        r = self.client.post(reverse('salon-list'),
                             {'codigo': 'AU-1', 'capacidad': 20, 'edificio': 'A'}, format='json')
        sid = r.data['id']
        self.client.patch(reverse('salon-detail', args=[sid]), {'capacidad': 25}, format='json')
        self.client.delete(reverse('salon-detail', args=[sid]))
        acciones = list(RegistroAuditoria.objects.filter(modelo='Salon', objeto_id=sid)
                        .order_by('id').values_list('accion', flat=True))
        self.assertEqual(acciones, ['CREATE', 'UPDATE', 'DELETE'])
        self.assertEqual(RegistroAuditoria.objects.filter(modelo='Salon').first().usuario, self.ti)
