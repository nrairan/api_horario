"""Eliminación de horarios: DELETE /api/horarios/{id}/.

Cubre permisos por rol, alcance del coordinador, integridad de los datos
relacionados y que la franja y el salón queden libres tras eliminar.
"""
from datetime import time

from rest_framework.test import APITestCase

from .models import (
    Usuario, Programa, Asignatura, Salon, Horario, RegistroAuditoria,
)

URL = '/api/horarios/'


class EliminarHorarioBase(APITestCase):
    def setUp(self):
        mk = lambda u, rol: Usuario.objects.create_user(
            username=u, email=f'{u}@ucundinamarca.edu.co', password='Abc12345', rol=rol)
        self.ti = mk('ti', 'TI')
        self.co1, self.co2, self.co_sin = mk('co1', 'CO'), mk('co2', 'CO'), mk('cosin', 'CO')
        self.gc1, self.gc2 = mk('gc1', 'GC'), mk('gc2', 'GC')
        self.es = mk('es', 'ES')

        self.p1 = Programa.objects.create(nombre='Sistemas', codigo='IS', coordinador=self.co1)
        self.p2 = Programa.objects.create(nombre='Ambiental', codigo='IA', coordinador=self.co2)
        self.a1 = Asignatura.objects.create(codigo='IS101', nombre='Programación I', programa=self.p1, creditos=3)
        self.a1b = Asignatura.objects.create(codigo='IS102', nombre='Bases de Datos', programa=self.p1, creditos=3)
        self.a2 = Asignatura.objects.create(codigo='IA201', nombre='Hidrología', programa=self.p2, creditos=3)
        self.s1 = Salon.objects.create(codigo='A101', capacidad=30, edificio='A')
        self.s2 = Salon.objects.create(codigo='B201', capacidad=30, edificio='B')

        # h1 y h1b son del programa 1; h2 es del programa 2.
        self.h1 = self.horario(self.a1, self.s1, self.gc1, 'LUN')
        self.h1b = self.horario(self.a1b, self.s2, self.gc2, 'MIE')
        self.h2 = self.horario(self.a2, self.s2, self.gc2, 'MAR')

    def horario(self, asig, salon, gestor, dia, ini=time(7), fin=time(9), sem='1'):
        return Horario.objects.create(asignatura=asig, salon=salon, gestor=gestor, dia=dia,
                                      hora_inicio=ini, hora_fin=fin, semestre=sem)

    def como(self, user):
        self.client.force_authenticate(user=user)

    def url(self, h):
        return f'{URL}{h.pk}/'

    def ids(self, resp):
        data = resp.data['results'] if isinstance(resp.data, dict) and 'results' in resp.data else resp.data
        return sorted(x['id'] for x in data)

    def payload(self, **kw):
        d = {'asignatura': self.a1b.pk, 'salon': self.s1.pk, 'gestor': self.gc1.pk,
             'dia': 'LUN', 'hora_inicio': '07:00:00', 'hora_fin': '09:00:00', 'semestre': '1'}
        d.update(kw)
        return d


class TestEliminarComoTI(EliminarHorarioBase):
    def test_1_ti_elimina_un_horario(self):
        self.como(self.ti)
        r = self.client.delete(self.url(self.h1))
        self.assertEqual(r.status_code, 204)
        self.assertFalse(r.content)
        self.assertFalse(Horario.objects.filter(pk=self.h1.pk).exists())

    def test_2_el_horario_eliminado_deja_de_aparecer_en_el_listado(self):
        self.como(self.ti)
        self.assertIn(self.h1.pk, self.ids(self.client.get(URL)))
        self.client.delete(self.url(self.h1))
        antes = self.ids(self.client.get(URL))
        self.assertNotIn(self.h1.pk, antes)
        self.assertEqual(antes, sorted([self.h1b.pk, self.h2.pk]))
        # Tampoco en la vista por semestre.
        r = self.client.get(f'{URL}por_semestre/?semestre=1')
        vistos = [h['id'] for dia in r.data.values() for h in dia]
        self.assertNotIn(self.h1.pk, vistos)

    def test_3_consultar_o_repetir_sobre_el_horario_eliminado_da_404(self):
        self.como(self.ti)
        self.client.delete(self.url(self.h1))
        self.assertEqual(self.client.get(self.url(self.h1)).status_code, 404)
        self.assertEqual(self.client.patch(self.url(self.h1), {'dia': 'JUE'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(self.url(self.h1)).status_code, 404)

    def test_eliminar_un_id_inexistente_da_404(self):
        self.como(self.ti)
        self.assertEqual(self.client.delete(f'{URL}999999/').status_code, 404)

    def test_la_eliminacion_queda_en_la_auditoria(self):
        self.como(self.ti)
        self.client.delete(self.url(self.h1))
        log = RegistroAuditoria.objects.get(modelo='Horario', accion='DELETE', objeto_id=self.h1.pk)
        self.assertEqual(log.usuario, self.ti)
        self.assertIsNotNone(log.datos_antes)


class TestEliminarComoCoordinador(EliminarHorarioBase):
    def test_4_coordinador_elimina_un_horario_de_su_programa(self):
        self.como(self.co1)
        self.assertEqual(self.client.delete(self.url(self.h1)).status_code, 204)
        self.assertFalse(Horario.objects.filter(pk=self.h1.pk).exists())
        # Su otro horario y el del otro programa siguen intactos.
        self.assertTrue(Horario.objects.filter(pk=self.h1b.pk).exists())
        self.assertTrue(Horario.objects.filter(pk=self.h2.pk).exists())

    def test_5_coordinador_no_elimina_un_horario_de_otro_programa(self):
        self.como(self.co1)
        self.assertEqual(self.client.delete(self.url(self.h2)).status_code, 404)
        # Parámetros de consulta no amplían el alcance.
        for qs in ('?programa=2', f'?asignatura={self.a2.pk}', f'?gestor={self.gc2.pk}'):
            self.assertEqual(self.client.delete(f'{self.url(self.h2)}{qs}').status_code, 404, qs)
        self.assertTrue(Horario.objects.filter(pk=self.h2.pk).exists())
        # Y a la inversa.
        self.como(self.co2)
        self.assertEqual(self.client.delete(self.url(self.h1)).status_code, 404)
        self.assertTrue(Horario.objects.filter(pk=self.h1.pk).exists())

    def test_coordinador_sin_programa_recibe_respuesta_controlada(self):
        self.como(self.co_sin)
        self.assertEqual(self.client.delete(self.url(self.h1)).status_code, 404)
        self.assertEqual(Horario.objects.count(), 3)


class TestEliminarRolesSinPermiso(EliminarHorarioBase):
    def test_6_gestor_y_estudiante_reciben_403(self):
        for user in (self.gc1, self.es):
            self.como(user)
            r = self.client.delete(self.url(self.h1))
            self.assertEqual(r.status_code, 403, user.rol)
        self.assertEqual(Horario.objects.count(), 3)

    def test_6_sin_autenticar_recibe_401(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.delete(self.url(self.h1)).status_code, 401)
        self.assertTrue(Horario.objects.filter(pk=self.h1.pk).exists())


class TestIntegridadAlEliminar(EliminarHorarioBase):
    def test_7_no_elimina_asignatura_salon_gestor_programa_ni_otros_horarios(self):
        antes = {
            'asignaturas': Asignatura.objects.count(), 'salones': Salon.objects.count(),
            'usuarios': Usuario.objects.count(), 'programas': Programa.objects.count(),
        }
        otros = list(Horario.objects.exclude(pk=self.h1.pk).values(
            'id', 'asignatura_id', 'salon_id', 'gestor_id', 'dia', 'hora_inicio', 'hora_fin', 'semestre'))
        self.como(self.ti)
        self.assertEqual(self.client.delete(self.url(self.h1)).status_code, 204)

        self.assertEqual(Asignatura.objects.count(), antes['asignaturas'])
        self.assertEqual(Salon.objects.count(), antes['salones'])
        self.assertEqual(Usuario.objects.count(), antes['usuarios'])
        self.assertEqual(Programa.objects.count(), antes['programas'])
        for obj in (self.a1, self.s1, self.gc1, self.p1):
            self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists(), obj)
        despues = list(Horario.objects.values(
            'id', 'asignatura_id', 'salon_id', 'gestor_id', 'dia', 'hora_inicio', 'hora_fin', 'semestre'))
        self.assertEqual(sorted(despues, key=lambda x: x['id']), sorted(otros, key=lambda x: x['id']))

    def test_eliminar_libera_la_franja_el_salon_y_el_gestor(self):
        self.como(self.co1)
        # Con h1 vigente, la misma franja (salón s1 y gestor gc1) está ocupada.
        r = self.client.post(URL, self.payload(), format='json')
        self.assertEqual(r.status_code, 400, getattr(r, 'data', None))
        self.assertEqual(Horario.objects.count(), 3)

        self.assertEqual(self.client.delete(self.url(self.h1)).status_code, 204)

        r = self.client.post(URL, self.payload(), format='json')
        self.assertEqual(r.status_code, 201, getattr(r, 'data', None))

    def test_8_crear_y_editar_siguen_funcionando(self):
        self.como(self.co1)
        self.assertEqual(self.client.delete(self.url(self.h1b)).status_code, 204)
        r = self.client.post(URL, self.payload(dia='JUE'), format='json')
        self.assertEqual(r.status_code, 201, getattr(r, 'data', None))
        nuevo = r.data['id']
        r = self.client.patch(f'{URL}{nuevo}/', {'dia': 'VIE'}, format='json')
        self.assertEqual(r.status_code, 200, getattr(r, 'data', None))
        self.assertEqual(Horario.objects.get(pk=nuevo).dia, 'VIE')
        # Un coordinador sigue sin poder crear con una asignatura ajena.
        r = self.client.post(URL, self.payload(asignatura=self.a2.pk, dia='SAB'), format='json')
        self.assertEqual(r.status_code, 403)