"""Alcance por programa del rol CO: programas, asignaturas, horarios y recursos compartidos."""
from datetime import time

from django.urls import reverse
from rest_framework.test import APITestCase

from .models import Usuario, Programa, Asignatura, Salon, Horario


class AlcanceCOBase(APITestCase):
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
        self.a2 = Asignatura.objects.create(codigo='IA201', nombre='Hidrología Secreta', programa=self.p2, creditos=3)

        self.s1 = Salon.objects.create(codigo='A101', capacidad=30, edificio='A')
        self.s2 = Salon.objects.create(codigo='B201', capacidad=30, edificio='B')

        # Horarios existentes: uno por programa (el de P2 usa el salón s2 y el gestor gc2)
        self.h1 = self.horario(self.a1, self.s1, self.gc1, 'LUN')
        self.h2 = self.horario(self.a2, self.s2, self.gc2, 'MAR')

    def horario(self, asig, salon, gestor, dia, ini=time(7), fin=time(9), sem='1'):
        return Horario.objects.create(asignatura=asig, salon=salon, gestor=gestor, dia=dia,
                                      hora_inicio=ini, hora_fin=fin, semestre=sem)

    def como(self, user):
        self.client.force_authenticate(user=user)

    def payload(self, **kw):
        d = {'asignatura': self.a1b.pk, 'salon': self.s1.pk, 'gestor': self.gc1.pk,
             'dia': 'MIE', 'hora_inicio': '07:00:00', 'hora_fin': '09:00:00', 'semestre': '1'}
        d.update(kw)
        return d

    def ids(self, resp):
        data = resp.data['results'] if isinstance(resp.data, dict) and 'results' in resp.data else resp.data
        return sorted(x['id'] for x in data)


class TestConsultasDelCoordinador(AlcanceCOBase):
    def test_1_coordinador_consulta_solo_su_programa(self):
        self.como(self.co1)
        self.assertEqual(self.ids(self.client.get('/api/programa/')), [self.p1.pk])

    def test_2_no_puede_consultar_otro_programa_por_id_ni_parametros(self):
        self.como(self.co1)
        self.assertEqual(self.client.get(f'/api/programa/{self.p2.pk}/').status_code, 404)
        # un parámetro que intente cambiar el alcance se ignora
        r = self.client.get(f'/api/programa/?coordinador={self.co2.pk}&id={self.p2.pk}')
        self.assertEqual(self.ids(r), [self.p1.pk])

    def test_3_solo_ve_asignaturas_de_su_programa(self):
        self.como(self.co1)
        self.assertEqual(self.ids(self.client.get('/api/asignaturas/')), sorted([self.a1.pk, self.a1b.pk]))
        # ?programa=<ajeno> no amplía el alcance
        self.assertEqual(self.ids(self.client.get(f'/api/asignaturas/?programa={self.p2.pk}')), [])
        self.assertEqual(self.client.get(f'/api/asignaturas/{self.a2.pk}/').status_code, 404)
        r = self.client.get('/api/buscar-asignaturas/?q=IA')
        self.assertEqual([x['id'] for x in r.data], [])

    def test_4_solo_ve_horarios_de_su_programa(self):
        self.como(self.co1)
        self.assertEqual(self.ids(self.client.get('/api/horarios/')), [self.h1.pk])
        r = self.client.get('/api/horarios/por_semestre/?semestre=1')
        vistos = [c['id'] for dia in r.data.values() for c in dia]
        self.assertEqual(vistos, [self.h1.pk])

    def test_5_no_puede_consultar_ni_modificar_horario_ajeno_por_id(self):
        self.como(self.co1)
        url = f'/api/horarios/{self.h2.pk}/'
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.patch(url, {'dia': 'JUE'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertTrue(Horario.objects.filter(pk=self.h2.pk, dia='MAR').exists())

    def test_salones_compartidos_se_listan_completos(self):
        self.como(self.co1)
        r = self.client.get('/api/salones/')
        self.assertEqual(self.ids(r), sorted([self.s1.pk, self.s2.pk]))
        self.assertEqual(self.client.get(f'/api/salones/{self.s2.pk}/').status_code, 200)

    def test_otros_roles_autenticados_conservan_su_vista(self):
        for user in (self.ti, self.gc1, self.es):
            self.como(user)
            self.assertEqual(self.ids(self.client.get('/api/programa/')), sorted([self.p1.pk, self.p2.pk]), user.rol)
            self.assertEqual(len(self.ids(self.client.get('/api/asignaturas/'))), 3, user.rol)
            self.assertEqual(self.ids(self.client.get('/api/horarios/')), sorted([self.h1.pk, self.h2.pk]), user.rol)

    def test_12_ti_conserva_permisos_administrativos(self):
        self.como(self.ti)
        r = self.client.post('/api/asignaturas/', {
            'codigo': 'IA999', 'nombre': 'Nueva', 'programa': self.p2.pk, 'creditos': 2,
            'horas_semana': 2, 'semestre': '1', 'gestores': []}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(self.client.patch(f'/api/programa/{self.p2.pk}/', {'nombre': 'Ambiental II'},
                                           format='json').status_code, 200)
        # TI puede programar un horario de cualquier programa
        r = self.client.post('/api/horarios/', self.payload(asignatura=self.a2.pk, dia='JUE', salon=self.s1.pk,
                                                             gestor=self.gc1.pk), format='json')
        self.assertEqual(r.status_code, 201, getattr(r, 'data', None))
        self.assertEqual(self.client.delete(f'/api/horarios/{self.h2.pk}/').status_code, 204)

    def test_anonimo_no_accede_a_datos_con_alcance(self):
        for url in ('/api/programa/', '/api/asignaturas/', '/api/horarios/',
                    '/api/horarios/por_semestre/?semestre=1', '/api/buscar-asignaturas/?q=a'):
            self.assertEqual(self.client.get(url).status_code, 401, url)
        self.assertEqual(self.client.post('/api/horarios/', self.payload(), format='json').status_code, 401)
        self.assertEqual(self.client.post('/api/horarios/validar/', self.payload(), format='json').status_code, 401)
        # los salones son un recurso compartido: su lectura no cambia
        self.assertEqual(self.client.get('/api/salones/').status_code, 200)


class TestEscrituraDelCoordinador(AlcanceCOBase):
    def test_6_no_crea_ni_actualiza_horarios_con_asignaturas_ajenas(self):
        self.como(self.co1)
        r = self.client.post('/api/horarios/', self.payload(asignatura=self.a2.pk), format='json')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Horario.objects.filter(asignatura=self.a2).count(), 1)
        # tampoco moviendo un horario propio a una asignatura ajena (PATCH y PUT)
        r = self.client.patch(f'/api/horarios/{self.h1.pk}/', {'asignatura': self.a2.pk}, format='json')
        self.assertEqual(r.status_code, 403)
        r = self.client.put(f'/api/horarios/{self.h1.pk}/', self.payload(asignatura=self.a2.pk), format='json')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Horario.objects.get(pk=self.h1.pk).asignatura_id, self.a1.pk)
        # ni consultar disponibilidad con una asignatura ajena
        r = self.client.post('/api/horarios/validar/', self.payload(asignatura=self.a2.pk), format='json')
        self.assertEqual(r.status_code, 403)

    def test_coordinador_no_crea_ni_modifica_asignaturas_ni_programas(self):
        self.como(self.co1)
        body = {'codigo': 'X1', 'nombre': 'X', 'programa': self.p2.pk, 'creditos': 2, 'horas_semana': 2}
        self.assertEqual(self.client.post('/api/asignaturas/', body, format='json').status_code, 403)
        self.assertEqual(self.client.patch(f'/api/asignaturas/{self.a1.pk}/', {'nombre': 'Z'}, format='json').status_code, 403)
        self.assertEqual(self.client.patch(f'/api/programa/{self.p1.pk}/', {'nombre': 'Z'}, format='json').status_code, 403)
        self.assertEqual(self.client.delete(f'/api/asignaturas/{self.a1.pk}/').status_code, 403)

    def test_7_puede_usar_salon_compartido_si_esta_disponible(self):
        self.como(self.co1)
        # s2 lo usa P2 el martes; el lunes 9-11 está libre
        r = self.client.post('/api/horarios/', self.payload(salon=self.s2.pk, dia='LUN',
                                                             hora_inicio='09:00:00', hora_fin='11:00:00'), format='json')
        self.assertEqual(r.status_code, 201, getattr(r, 'data', None))

    def test_8_salon_ocupado_por_otro_programa_se_considera_ocupado(self):
        self.como(self.co1)
        body = self.payload(salon=self.s2.pk, dia='MAR')            # s2 está ocupado MAR 7-9 por P2
        r = self.client.post('/api/horarios/', body, format='json')
        self.assertEqual(r.status_code, 400)
        conflicto = r.data['conflictos'][0]
        self.assertEqual(conflicto['tipo'], 'SALON_OCUPADO')
        # sin filtrar datos privados del otro programa
        self.assertNotIn('Hidrología', conflicto['mensaje'])
        self.assertIn(conflicto['horario_id'], (None, 'None'))
        # lo mismo en la consulta previa
        r = self.client.post('/api/horarios/validar/', body, format='json')
        self.assertFalse(r.data['valido'])
        self.assertNotIn('Hidrología', r.data['conflictos'][0]['mensaje'])
        self.assertIsNone(r.data['conflictos'][0]['horario_id'])

    def test_9_puede_usar_gestor_de_otro_programa_si_cumple_restricciones(self):
        self.como(self.co1)
        # gc2 enseña en P2, pero el miércoles está libre
        r = self.client.post('/api/horarios/', self.payload(gestor=self.gc2.pk), format='json')
        self.assertEqual(r.status_code, 201, getattr(r, 'data', None))

    def test_10_no_puede_asignar_gestor_con_conflicto_horario(self):
        self.como(self.co1)
        body = self.payload(gestor=self.gc2.pk, dia='MAR', salon=self.s1.pk)   # gc2 ocupado MAR 7-9 por P2
        r = self.client.post('/api/horarios/', body, format='json')
        self.assertEqual(r.status_code, 400)
        conflicto = r.data['conflictos'][0]
        self.assertEqual(conflicto['tipo'], 'GESTOR_OCUPADO')
        self.assertNotIn('Hidrología', conflicto['mensaje'])
        self.assertEqual(Horario.objects.filter(asignatura=self.a1b).count(), 0)

    def test_conflicto_con_horario_propio_conserva_el_detalle(self):
        self.como(self.co1)
        # gc1 ya da clase LUN 7-9 de su propio programa: el detalle sí es visible
        r = self.client.post('/api/horarios/', self.payload(dia='LUN', salon=self.s2.pk), format='json')
        self.assertEqual(r.status_code, 400)
        gestor = [c for c in r.data['conflictos'] if c['tipo'] == 'GESTOR_OCUPADO'][0]
        self.assertIn('Programación I', gestor['mensaje'])

    def test_11_coordinador_sin_programa_recibe_respuesta_controlada(self):
        self.como(self.co_sin)
        for url in ('/api/programa/', '/api/asignaturas/', '/api/horarios/'):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertEqual(self.ids(r), [], url)
        self.assertEqual(self.client.get(f'/api/programa/{self.p1.pk}/').status_code, 404)
        r = self.client.post('/api/horarios/', self.payload(), format='json')
        self.assertEqual(r.status_code, 403)
        self.assertIn('programa asignado', str(r.data))

    def test_gestor_y_estudiante_no_modifican_horarios(self):
        for user in (self.gc1, self.es):
            self.como(user)
            self.assertEqual(self.client.post('/api/horarios/', self.payload(), format='json').status_code, 403, user.rol)
            self.assertEqual(self.client.delete(f'/api/horarios/{self.h1.pk}/').status_code, 403, user.rol)
