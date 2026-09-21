"""Base de auditoria: lo que la interfaz del servidor va a consultar."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.database import auditoria as base
from app.ingestion.adapters import Consolidado
from app.reconciliation.audit import COLUMNAS_ORDEN, ResultadoAuditoria, _fotografiar
from app.reconciliation.calculations import calcular
from app.reconciliation.matching import conciliar
from app.rules.situations import evaluar  # noqa: F401  (registra las reglas)
from tests.test_matching import _ase, _c, _rep


def _resultado(repuestos, aseguradoras, fecha="2026-09-13T14:00:00"):
    fotos = []
    for orden in conciliar(_c(repuestos), _c(aseguradoras)):
        a = orden.aseguradora
        fin = calcular(orden.costo_repuestos, orden.valor_aseguradora,
                       valor_repuestos_reconocido=a.valor_repuestos if a else None,
                       valor_mano_obra=a.valor_mano_obra if a else None,
                       valor_repuestos_con_iva=a.valor_repuestos_con_iva if a else None,
                       valor_mano_obra_con_iva=a.valor_mano_obra_con_iva if a else None)
        fotos.append(_fotografiar(orden, fin, fecha))
    return ResultadoAuditoria(fecha, fotos, _c(repuestos), _c(aseguradoras), [], 0.1)


def _caso_real():
    """1017550 cuadra con IVA y fletes; 1017583 facturada por debajo; 1017386 sin contraparte."""
    return _resultado(
        [_rep("1017550", 335000, hoja=" HAROLD H.T", fecha="2026-01-15", total_cobrado=392989,
              reconocido=300000, mano_obra=92989, factura="92616"),
         _rep("1017583", 400000, hoja=" HAROLD H.T", fecha="2026-01-30", total_cobrado=542989,
              reconocido=450000, mano_obra=92989, factura="92810"),
         _rep("1017386", 275000, hoja=" HAROLD H.T", fecha="2026-04-07", total_cobrado=592989,
              factura="92465")],
        [_ase("1017550", 514957, partes=300000, mano_obra=92989, iva=74668, transporte=47300, periodo="2026-01"),
         _ase("1017583", 681557, partes=440000, mano_obra=92989, iva=101268, transporte=47300, periodo="2026-01")],
    )


@pytest.fixture
def engine(tmp_path):
    e = base.motor(f"sqlite:///{tmp_path / 'auditoria.db'}")
    base.crear_esquema(e)
    return e


def test_el_esquema_se_puede_crear_dos_veces(engine):
    base.crear_esquema(engine)
    assert set(base.metadata.tables) == {"corridas", "ordenes", "situaciones", "revisiones", "historial_revision"}


def test_la_tabla_de_ordenes_tiene_las_mismas_metricas_que_el_csv():
    en_csv = {n for n, _ in COLUMNAS_ORDEN} - {"fecha_analisis"}
    assert en_csv <= set(base.ordenes.c.keys())


def test_las_columnas_numericas_solo_reciben_montos():
    """Si una columna nueva de texto no se declara como tal, esta prueba lo detecta."""
    numericas = {n for n in base.ordenes.c.keys() if isinstance(base.ordenes.c[n].type, base.Numeric)}
    for f in _caso_real().fotografias:
        for nombre, extraer in COLUMNAS_ORDEN:
            if nombre in numericas:
                assert isinstance(extraer(f), (Decimal, type(None))), (nombre, extraer(f))


def test_guardar_corrida_completa(engine):
    corrida = base.guardar_corrida(engine, _caso_real(), cobertura=("2025-10", "2026-08"))
    with engine.connect() as con:
        filas = {r["orden_ceser"]: r for r in con.execute(select(base.ordenes)).mappings()}
        tipos = set(con.execute(select(base.situaciones.c.tipo)).scalars())
    assert set(filas) == {"1017550", "1017583", "1017386"}
    assert filas["1017550"]["veredicto_del_cruce"] == "cuadra"
    assert filas["1017583"]["veredicto_del_cruce"] == "facturado_por_debajo"
    assert filas["1017386"]["veredicto_del_cruce"] == "sin_contraparte"
    assert filas["1017550"]["residuo_del_cruce"] == Decimal("0.00")
    assert filas["1017550"]["factura_proveedor"] == "92616"
    assert "BILLED_BELOW_CHARGED" in tipos
    assert base.ultima_corrida(engine)["cobertura_desde"] == "2025-10"
    assert corrida == base.ultima_corrida(engine)["id"]


def test_una_corrida_que_falla_no_deja_nada_a_medias(engine):
    resultado = _caso_real()
    resultado.fotografias.append(resultado.fotografias[0])   # orden duplicada: viola la clave
    with pytest.raises(IntegrityError):
        base.guardar_corrida(engine, resultado)
    with engine.connect() as con:
        assert con.execute(select(func.count()).select_from(base.corridas)).scalar() == 0
        assert con.execute(select(func.count()).select_from(base.ordenes)).scalar() == 0


def test_lo_revisado_sigue_revisado_en_la_corrida_siguiente(engine):
    primera = base.guardar_corrida(engine, _caso_real())
    assert base.registrar_revision(engine, "1017583", "verificada", por="gerente", corrida_id=primera)
    base.guardar_corrida(engine, _caso_real())
    with engine.connect() as con:
        estado = con.execute(select(base.revisiones.c.estado)
                             .where(base.revisiones.c.orden_ceser == "1017583")).scalar()
    assert estado == "verificada"


def test_cada_cambio_de_estado_queda_en_el_historial(engine):
    base.registrar_revision(engine, "1017583", "en_gestion", por="gerente")
    base.registrar_revision(engine, "1017583", "verificada", por="gerente", nota="ya se llamo a Harold")
    assert base.registrar_revision(engine, "1017583", "verificada") is False   # sin cambio, sin registro
    with engine.connect() as con:
        pasos = con.execute(select(base.historial_revision.c.estado_anterior, base.historial_revision.c.estado_nuevo)
                            .order_by(base.historial_revision.c.id)).all()
    assert [tuple(p) for p in pasos] == [("pendiente", "en_gestion"), ("en_gestion", "verificada")]


def test_estado_de_revision_desconocido_se_rechaza(engine):
    with pytest.raises(ValueError, match="desconocido"):
        base.registrar_revision(engine, "1017583", "aprobada")


def test_dinero_por_mes_con_la_misma_metrica_que_la_vista(engine):
    corrida = base.guardar_corrida(engine, _caso_real())
    meses = {m["mes"]: m for m in base.dinero_por_mes(engine, corrida)}
    enero = meses["2026-01"]
    assert enero["ordenes"] == 2
    assert enero["gastado"] == Decimal("735000")
    assert enero["facturado"] == Decimal("1196514")
    assert enero["ingreso_neto"] == Decimal("1196514") - Decimal("175936") - Decimal("94600")
    assert enero["utilidad"] == enero["ingreso_neto"] - Decimal("735000")
    abril = meses["2026-04"]
    assert abril["ordenes"] == 0 and abril["ordenes_sin_contraparte"] == 1
    assert abril["gastado_sin_contraparte"] == Decimal("275000")
