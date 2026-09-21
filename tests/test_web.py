"""Interfaz de revision: acceso, filtros y marcado de estados."""

import dataclasses

import pytest
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from app.config.settings import get_settings
from app.database import auditoria as base
from app.web.app import crear_app
from tests.test_auditoria_db import _caso_real

CLAVE = "clave-de-prueba-larga"


@pytest.fixture
def app(tmp_path):
    url = f"sqlite:///{tmp_path / 'auditoria.db'}"
    motor = base.motor(url)
    base.crear_esquema(motor)
    base.guardar_corrida(motor, _caso_real(), cobertura=("2025-10", "2026-08"))
    settings = dataclasses.replace(
        get_settings(), audit_db_url=url, web_secret_key="secreto-de-prueba",
        web_usuarios={"gerente": generate_password_hash(CLAVE)})
    aplicacion = crear_app(settings)
    aplicacion.config["TESTING"] = True
    aplicacion.motor_de_prueba = motor
    return aplicacion


@pytest.fixture
def cliente(app):
    return app.test_client()


def _muchas_ordenes(motor, cuantas=120):
    """Una corrida grande, para probar la paginacion."""
    from decimal import Decimal
    from app.reconciliation.audit import ResultadoAuditoria, _fotografiar
    from app.reconciliation.calculations import calcular
    from app.reconciliation.matching import conciliar
    from tests.test_matching import _ase, _c, _rep
    repuestos = [_rep(str(1020000 + i), 300000, hoja=" HAROLD H.T", fecha="2026-05-01",
                      total_cobrado=392989, reconocido=300000, mano_obra=92989,
                      factura=str(90000 + i)) for i in range(cuantas)]
    aseguradoras = [_ase(str(1020000 + i), 514957, partes=300000, mano_obra=92989,
                         iva=74668, transporte=47300, periodo="2026-05") for i in range(cuantas)]
    fotos = []
    for orden in conciliar(_c(repuestos), _c(aseguradoras)):
        a = orden.aseguradora
        fin = calcular(orden.costo_repuestos, orden.valor_aseguradora,
                       valor_repuestos_reconocido=a.valor_repuestos if a else None,
                       valor_mano_obra=a.valor_mano_obra if a else None)
        fotos.append(_fotografiar(orden, fin, "2026-09-21T06:00:00"))
    return ResultadoAuditoria("2026-09-21T06:00:00", fotos, _c(repuestos), _c(aseguradoras), [], 0.1)


def _entrar(cliente, usuario="gerente", clave=CLAVE):
    return cliente.post("/entrar", data={"usuario": usuario, "clave": clave})


def test_sin_sesion_no_se_ve_nada(cliente):
    for ruta in ("/", "/ordenes", "/historial"):
        r = cliente.get(ruta)
        assert r.status_code == 302 and "/entrar" in r.headers["Location"]


def test_contrasena_incorrecta_no_deja_entrar(cliente):
    assert _entrar(cliente, clave="otra-cosa").status_code == 401
    assert cliente.get("/").status_code == 302


def test_usuario_inexistente_no_deja_entrar(cliente):
    assert _entrar(cliente, usuario="nadie").status_code == 401


def test_el_panel_muestra_las_cifras_de_la_ultima_corrida(cliente):
    _entrar(cliente)
    html = cliente.get("/").get_data(as_text=True)
    assert "Lo gastado contra lo recibido" in html
    assert "$735.000" in html          # gastado de las dos ordenes con contraparte
    assert "2025-10" in html           # cobertura de la corrida


def test_las_ordenes_se_filtran_por_veredicto(cliente):
    _entrar(cliente)
    html = cliente.get("/ordenes?veredicto=facturado_por_debajo&estado=todas").get_data(as_text=True)
    assert "1017583" in html and "1017550" not in html


def test_la_busqueda_encuentra_por_factura(cliente):
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas&buscar=92616").get_data(as_text=True)
    assert "1017550" in html and "1017583" not in html


def test_marcar_una_orden_la_saca_de_pendientes_y_queda_en_el_historial(cliente, app):
    _entrar(cliente)
    r = cliente.post("/revision", data={"orden": "1017583", "estado": "verificada",
                                        "nota": "hablado con el proveedor"})
    assert r.status_code == 302
    with app.motor_de_prueba.connect() as con:
        estado = con.execute(select(base.revisiones.c.estado)
                             .where(base.revisiones.c.orden_ceser == "1017583")).scalar()
        evento = con.execute(select(base.historial_revision)).mappings().first()
    assert estado == "verificada"
    assert evento["por"] == "gerente" and evento["nota"] == "hablado con el proveedor"
    assert "1017583" not in cliente.get("/ordenes?estado=pendiente").get_data(as_text=True)
    assert "1017583" in cliente.get("/ordenes?estado=verificada").get_data(as_text=True)


def test_no_se_puede_marcar_sin_sesion(cliente, app):
    r = cliente.post("/revision", data={"orden": "1017583", "estado": "verificada"})
    assert r.status_code == 302 and "/entrar" in r.headers["Location"]
    with app.motor_de_prueba.connect() as con:
        assert con.execute(select(base.revisiones)).first() is None


def test_un_estado_inventado_se_rechaza(cliente):
    _entrar(cliente)
    assert cliente.post("/revision", data={"orden": "1017583", "estado": "aprobada"}).status_code == 400


def test_el_historial_muestra_quien_y_cuando(cliente):
    _entrar(cliente)
    cliente.post("/revision", data={"orden": "1017550", "estado": "en_gestion"})
    html = cliente.get("/historial").get_data(as_text=True)
    assert "1017550" in html and "En gestión" in html and "gerente" in html


def test_salud_responde_sin_sesion(cliente):
    r = cliente.get("/salud")
    assert r.status_code == 200 and r.json["ultima_corrida"]


# --- Paginacion: una corrida trae cerca de 1.800 ordenes -------------------
def test_la_lista_se_pagina_y_no_pinta_todo(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _muchas_ordenes(app.motor_de_prueba, 120))
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas").get_data(as_text=True)
    assert html.count('name="orden"') == 50          # una pagina, no las 120
    assert "de <b>120</b> órdenes" in html
    assert "Página <b>1</b> de 3" in html


def test_la_segunda_pagina_trae_ordenes_distintas(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _muchas_ordenes(app.motor_de_prueba, 120))
    _entrar(cliente)
    import re
    def ordenes_de(pagina):
        html = cliente.get(f"/ordenes?estado=todas&pagina={pagina}").get_data(as_text=True)
        return set(re.findall(r'name="orden" value="(\d+)"', html))
    primera, segunda = ordenes_de(1), ordenes_de(2)
    assert len(primera) == 50 and len(segunda) == 50
    assert not (primera & segunda)


def test_el_paginador_conserva_los_filtros(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _muchas_ordenes(app.motor_de_prueba, 120))
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas&veredicto=cuadra").get_data(as_text=True)
    assert "veredicto=cuadra" in html and "pagina=2" in html


def test_una_pagina_fuera_de_rango_no_rompe(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _muchas_ordenes(app.motor_de_prueba, 60))
    _entrar(cliente)
    for pagina in ("999", "0", "-3", "abc"):
        assert cliente.get(f"/ordenes?estado=todas&pagina={pagina}").status_code == 200


# --- Poder ver la contraseña al escribirla --------------------------------
def test_la_pantalla_de_ingreso_permite_ver_la_clave(cliente):
    html = cliente.get("/entrar").get_data(as_text=True)
    assert "Mostrar la contraseña" in html
    assert "type=\'text\' : \'password\'" in html or "'text' : 'password'" in html


# --- Filtrar la tabla por fechas ------------------------------------------
def _corrida_con_fechas(motor):
    from app.reconciliation.audit import ResultadoAuditoria, _fotografiar
    from app.reconciliation.calculations import calcular
    from app.reconciliation.matching import conciliar
    from tests.test_matching import _ase, _c, _rep
    fechas = {"1030001": "2026-01-15", "1030002": "2026-04-20", "1030003": "2026-08-30"}
    repuestos = [_rep(o, 300000, hoja=" HAROLD H.T", fecha=f, total_cobrado=392989,
                      reconocido=300000, mano_obra=92989) for o, f in fechas.items()]
    aseguradoras = [_ase(o, 514957, partes=300000, mano_obra=92989, iva=74668,
                         transporte=47300, periodo=f[:7]) for o, f in fechas.items()]
    fotos = []
    for orden in conciliar(_c(repuestos), _c(aseguradoras)):
        a = orden.aseguradora
        fin = calcular(orden.costo_repuestos, orden.valor_aseguradora,
                       valor_repuestos_reconocido=a.valor_repuestos if a else None)
        fotos.append(_fotografiar(orden, fin, "2026-09-21T06:00:00"))
    return ResultadoAuditoria("2026-09-21T06:00:00", fotos, _c(repuestos), _c(aseguradoras), [], 0.1)


def test_la_tabla_se_filtra_por_rango_de_fechas(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _corrida_con_fechas(app.motor_de_prueba))
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas&desde=2026-03-01&hasta=2026-06-30").get_data(as_text=True)
    assert "1030002" in html
    assert "1030001" not in html and "1030003" not in html


def test_solo_desde_o_solo_hasta_tambien_funciona(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _corrida_con_fechas(app.motor_de_prueba))
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas&desde=2026-04-01").get_data(as_text=True)
    assert "1030002" in html and "1030003" in html and "1030001" not in html
    html = cliente.get("/ordenes?estado=todas&hasta=2026-02-01").get_data(as_text=True)
    assert "1030001" in html and "1030002" not in html


def test_el_rango_se_conserva_al_cambiar_de_filtro_o_pagina(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _muchas_ordenes(app.motor_de_prueba, 120))
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas&desde=2026-01-01&hasta=2026-12-31").get_data(as_text=True)
    assert "desde=2026-01-01" in html and "hasta=2026-12-31" in html
    assert "Limpiar" in html


def test_la_corrida_informa_su_rango_de_fechas(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _corrida_con_fechas(app.motor_de_prueba))
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas").get_data(as_text=True)
    assert "2026-01-15" in html and "2026-08-30" in html


# --- Ordenar por fecha en los dos sentidos --------------------------------
def _fechas_en_pantalla(cliente, consulta):
    import re
    html = cliente.get(consulta).get_data(as_text=True)
    return re.findall(r'<td class="l dim">(\d{4}-\d{2}-\d{2})</td>', html)


def test_por_defecto_manda_lo_mas_reciente(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _corrida_con_fechas(app.motor_de_prueba))
    _entrar(cliente)
    fechas = _fechas_en_pantalla(cliente, "/ordenes?estado=todas")
    assert fechas == sorted(fechas, reverse=True)
    assert fechas[0] == "2026-08-30"


def test_se_puede_invertir_el_orden(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _corrida_con_fechas(app.motor_de_prueba))
    _entrar(cliente)
    fechas = _fechas_en_pantalla(cliente, "/ordenes?estado=todas&dir=asc")
    assert fechas == sorted(fechas)
    assert fechas[0] == "2026-01-15"


def test_el_encabezado_ofrece_el_orden_contrario(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _corrida_con_fechas(app.motor_de_prueba))
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas").get_data(as_text=True)
    assert "dir=asc" in html and "▼" in html
    html = cliente.get("/ordenes?estado=todas&dir=asc").get_data(as_text=True)
    assert "dir=desc" in html and "▲" in html


def test_el_orden_se_conserva_al_paginar(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _muchas_ordenes(app.motor_de_prueba, 120))
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas&dir=asc").get_data(as_text=True)
    assert "dir=asc" in html and "pagina=2" in html


# --- Desde el panel se llega a las ordenes sin contraparte del mes ---------
def _corrida_mixta(motor):
    """Dos meses, cada uno con una orden cruzada y una sin contraparte."""
    from app.reconciliation.audit import ResultadoAuditoria, _fotografiar
    from app.reconciliation.calculations import calcular
    from app.reconciliation.matching import conciliar
    from tests.test_matching import _ase, _c, _rep
    repuestos = [
        _rep("1040001", 300000, hoja=" HAROLD H.T", fecha="2026-04-10", total_cobrado=392989,
             reconocido=300000, mano_obra=92989),
        _rep("1040002", 250000, hoja=" HAROLD H.T", fecha="2026-04-22", total_cobrado=342989),
        _rep("1050001", 300000, hoja=" HAROLD H.T", fecha="2026-05-11", total_cobrado=392989,
             reconocido=300000, mano_obra=92989),
        _rep("1050002", 180000, hoja=" HAROLD H.T", fecha="2026-05-27", total_cobrado=272989),
    ]
    aseguradoras = [
        _ase("1040001", 514957, partes=300000, mano_obra=92989, iva=74668, transporte=47300, periodo="2026-04"),
        _ase("1050001", 514957, partes=300000, mano_obra=92989, iva=74668, transporte=47300, periodo="2026-05"),
    ]
    fotos = []
    for orden in conciliar(_c(repuestos), _c(aseguradoras)):
        a = orden.aseguradora
        fin = calcular(orden.costo_repuestos, orden.valor_aseguradora,
                       valor_repuestos_reconocido=a.valor_repuestos if a else None,
                       valor_mano_obra=a.valor_mano_obra if a else None,
                       valor_repuestos_con_iva=a.valor_repuestos_con_iva if a else None,
                       valor_mano_obra_con_iva=a.valor_mano_obra_con_iva if a else None)
        fotos.append(_fotografiar(orden, fin, "2026-09-21T06:00:00"))
    return ResultadoAuditoria("2026-09-21T06:00:00", fotos, _c(repuestos), _c(aseguradoras), [], 0.1)


def test_el_panel_muestra_los_meses_con_nombre_y_enlazados(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _corrida_mixta(app.motor_de_prueba))
    _entrar(cliente)
    html = cliente.get("/").get_data(as_text=True)
    assert "abril 2026" in html and "mayo 2026" in html
    assert "veredicto=sin_contraparte" in html
    assert "desde=2026-04-01" in html and "hasta=2026-04-30" in html


def test_al_pulsar_un_mes_se_ven_solo_sus_ordenes_sin_contraparte(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _corrida_mixta(app.motor_de_prueba))
    _entrar(cliente)
    html = cliente.get("/ordenes?veredicto=sin_contraparte&estado=todas&hoja=HAROLD+H.T"
                       "&desde=2026-04-01&hasta=2026-04-30").get_data(as_text=True)
    assert "1040002" in html                      # la de abril sin contraparte
    assert "1050002" not in html                  # la de mayo no
    assert "1040001" not in html                  # la cruzada tampoco


def test_el_filtro_de_hoja_se_puede_quitar(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _corrida_mixta(app.motor_de_prueba))
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas&hoja=HAROLD+H.T").get_data(as_text=True)
    assert "Hoja: HAROLD H.T" in html and "Limpiar" in html


def test_una_hoja_inexistente_no_devuelve_nada(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _corrida_mixta(app.motor_de_prueba))
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas&hoja=INEXISTENTE").get_data(as_text=True)
    assert "Ninguna orden coincide" in html


# --- La lista abre en lo que hay que revisar, no en todo -------------------
def test_por_defecto_solo_se_ven_las_ordenes_con_algo_que_revisar(cliente, app):
    """De 1.787 ordenes reales solo 257 tienen situaciones: abrir con todas entierra el trabajo."""
    base.guardar_corrida(app.motor_de_prueba, _corrida_mixta(app.motor_de_prueba))
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas").get_data(as_text=True)
    assert "1040002" in html          # sin contraparte: la auditoria la marco
    assert "1040001" not in html      # cuadra y no tiene nada que revisar


def test_se_pueden_ver_todas_si_se_pide(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _corrida_mixta(app.motor_de_prueba))
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas&atencion=todas").get_data(as_text=True)
    assert "1040001" in html and "1040002" in html


def test_los_dos_recuentos_se_muestran(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _corrida_mixta(app.motor_de_prueba))
    _entrar(cliente)
    html = cliente.get("/ordenes").get_data(as_text=True)
    assert "Para revisar · 2" in html and "Todas · 4" in html


def test_el_detalle_viene_plegado_y_no_pesa_la_pagina(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _corrida_mixta(app.motor_de_prueba))
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas").get_data(as_text=True)
    assert '<tr class="detalle" hidden>' in html
    assert "Pulsa una fila para ver su detalle" in html


# --- Utilidad a la vista y enlaces al Excel -------------------------------
def test_la_tabla_muestra_la_utilidad_de_cada_orden(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _caso_real())
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas&atencion=todas").get_data(as_text=True)
    assert "<th>Utilidad</th>" in html
    assert "$57.989" in html        # 392.989 cobrados menos 335.000 de costo


def test_el_detalle_enlaza_a_los_dos_excel_con_su_ubicacion(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _caso_real())
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas&atencion=todas").get_data(as_text=True)
    assert "docs.google.com/spreadsheets" in html
    assert "HAROLD H.T fila" in html and "FALABELLA fila" in html


def test_la_interfaz_pone_al_dia_el_esquema_al_arrancar(tmp_path):
    """Desplegar codigo nuevo antes de la auditoria del dia no puede tumbar la pantalla."""
    import dataclasses
    from sqlalchemy import text
    from app.config.settings import get_settings
    from app.web.app import crear_app
    from tests.test_auditoria_db import _caso_real

    url = f"sqlite:///{tmp_path / 'vieja.db'}"
    motor = base.motor(url)
    base.crear_esquema(motor)
    base.guardar_corrida(motor, _caso_real())
    with motor.begin() as con:      # una base creada por una version anterior
        con.execute(text("ALTER TABLE ordenes DROP COLUMN ubicacion_en_repuestos"))

    app = crear_app(dataclasses.replace(
        get_settings(), audit_db_url=url, web_secret_key="s",
        web_usuarios={"g": generate_password_hash(CLAVE)}))
    app.config["TESTING"] = True
    cliente = app.test_client()
    cliente.post("/entrar", data={"usuario": "g", "clave": CLAVE})
    assert cliente.get("/ordenes").status_code == 200


# --- Enlace a la hoja y la fila exactas del Excel --------------------------
def test_se_enlaza_a_la_pestana_y_la_fila_cuando_se_conoce_el_gid():
    from app.web.app import enlaces_a_la_hoja
    url = "https://docs.google.com/spreadsheets/d/ABC/edit"
    e, = enlaces_a_la_hoja(url, " HAROLD H.T fila 1806", {" HAROLD H.T": 991})
    assert e["url"] == url + "?gid=991#gid=991&range=A1806"
    assert e["texto"] == "HAROLD H.T fila 1806" or "1806" in e["texto"]
    assert e["exacto"]


def test_sin_gid_el_enlace_cae_al_archivo_completo():
    from app.web.app import enlaces_a_la_hoja
    url = "https://docs.google.com/spreadsheets/d/ABC/edit"
    e, = enlaces_a_la_hoja(url, "HAROLD H.T fila 1806", {})
    assert e["url"] == url and not e["exacto"]


def test_una_orden_en_varias_hojas_da_varios_enlaces():
    from app.web.app import enlaces_a_la_hoja
    enlaces = enlaces_a_la_hoja("https://x/edit", "ACOPIO fila 37; FLAMINGO fila 90",
                                {"ACOPIO": 1, "FLAMINGO": 2})
    assert [e["texto"] for e in enlaces] == ["ACOPIO fila 37", "FLAMINGO fila 90"]


def test_sin_archivo_configurado_no_hay_enlaces():
    from app.web.app import enlaces_a_la_hoja
    assert enlaces_a_la_hoja(None, "HAROLD H.T fila 5", {"HAROLD H.T": 1}) == []


def test_el_detalle_muestra_los_enlaces_al_excel(cliente, app):
    base.guardar_corrida(app.motor_de_prueba, _caso_real())
    _entrar(cliente)
    html = cliente.get("/ordenes?estado=todas&atencion=todas").get_data(as_text=True)
    assert "Ver en el Excel" in html
    assert "HAROLD H.T fila" in html and "FALABELLA fila" in html


def test_los_gid_configurados_mandan_sobre_los_de_la_corrida(tmp_path):
    """Se anotan a mano justamente cuando la API no los entrega."""
    import dataclasses
    from app.config.settings import get_settings
    from app.web.app import crear_app
    from tests.test_auditoria_db import _caso_real

    url = f"sqlite:///{tmp_path / 'g.db'}"
    motor = base.motor(url); base.crear_esquema(motor)
    base.guardar_corrida(motor, _caso_real(), gids={"repuestos": {"HAROLD H.T": 1}})

    app = crear_app(dataclasses.replace(
        get_settings(), audit_db_url=url, web_secret_key="s",
        web_usuarios={"g": generate_password_hash(CLAVE)},
        gids_hojas={"repuestos": {"HAROLD H.T": 999}}))
    app.config["TESTING"] = True
    cliente = app.test_client()
    cliente.post("/entrar", data={"usuario": "g", "clave": CLAVE})
    html = cliente.get("/ordenes?estado=todas&atencion=todas").get_data(as_text=True)
    assert "gid=999" in html and "gid=1&" not in html
