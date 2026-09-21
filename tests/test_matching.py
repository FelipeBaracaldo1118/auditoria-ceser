"""Pruebas de la jerarquia de relacion entre ordenes (seccion 10 y 11)."""

from decimal import Decimal

import pandas as pd

from app.ingestion.adapters import Consolidado, LineaAseguradora, LineaRepuesto
from app.normalization.money import normalizar_monto
from app.normalization.orders import normalizar_orden
from app.reconciliation.matching import conciliar


def _rep(orden, costo, hoja="HOJA REP", fecha=None, reconocido=None,
         mano_obra=None, total_cobrado=None, factura=None):
    return LineaRepuesto(
        hoja_origen=hoja, fila=2, orden=normalizar_orden(orden), costo=normalizar_monto(costo),
        fecha=pd.Timestamp(fecha) if fecha else None, orden_externa=None,
        valor_reconocido=normalizar_monto(reconocido) if reconocido is not None else None,
        valor_mano_obra=normalizar_monto(mano_obra) if mano_obra is not None else None,
        total_cobrado=normalizar_monto(total_cobrado) if total_cobrado is not None else None,
        factura=factura,
    )


def _ase(orden, valor, hoja="FALABELLA", caso=None, concepto="reparacion", periodo="2026-05",
         partes=None, mano_obra=None, transporte=None, iva=None):
    return LineaAseguradora(
        hoja_origen=hoja, fila=2, orden=normalizar_orden(orden), valor=normalizar_monto(valor),
        orden_aseguradora=caso, concepto=concepto, periodo=periodo,
        valor_repuestos=normalizar_monto(partes) if partes is not None else None,
        valor_mano_obra=normalizar_monto(mano_obra) if mano_obra is not None else None,
        valor_transporte=normalizar_monto(transporte) if transporte is not None else None,
        valor_iva=normalizar_monto(iva) if iva is not None else None,
    )


def _c(lineas):
    return Consolidado(lineas=lineas, hojas_incluidas=["x"])


def test_caso_a_coincidencia_directa():
    r = conciliar(_c([_rep("1017550", 335000)]), _c([_ase("1017550", 514956, caso="60817")]))
    assert len(r) == 1
    assert r[0].tipo_coincidencia == "directa"
    assert r[0].costo_repuestos == Decimal("335000")
    assert r[0].aseguradora.ordenes_aseguradora == ["60817"]


def test_caso_b_relacion_a_traves_de_tech():
    """La orden CESER no esta en aseguradoras, pero TECH indica la orden asociada."""
    repuestos = _c([_rep("10001234", 200000)])
    aseguradoras = _c([_ase("18924985", 500000)])

    r = conciliar(repuestos, aseguradoras, buscar_en_tech=lambda o: "18924985" if o == "10001234" else None)
    assert r[0].tipo_coincidencia == "via_tech"
    assert r[0].orden_aseguradora_via_tech == "18924985"
    assert r[0].valor_aseguradora == Decimal("500000")


def test_caso_c_ni_directa_ni_por_tech():
    r = conciliar(_c([_rep("10001234", 200000)]), _c([_ase("18924985", 500000)]),
                  buscar_en_tech=lambda o: "99999999")
    tipos = {c.orden_ceser: c.tipo_coincidencia for c in r}
    assert tipos["10001234"] == "sin_aseguradora"
    assert tipos["18924985"] == "sin_repuestos"


def test_varias_lineas_de_repuestos_se_suman_por_orden():
    r = conciliar(_c([_rep("1017550", 300000), _rep("1017550", 100000), _rep("1017550", 50000)]), _c([]))
    assert r[0].costo_repuestos == Decimal("450000")
    assert len(r[0].repuestos.lineas) == 3


def test_una_linea_sin_costo_no_se_cuenta_como_cero():
    r = conciliar(_c([_rep("1017550", 300000), _rep("1017550", None)]), _c([]))
    assert r[0].costo_repuestos == Decimal("300000")
    assert r[0].repuestos.lineas_sin_costo == 1


def test_valor_de_varias_hojas_se_suma_y_se_desglosa():
    r = conciliar(
        _c([_rep("12710231", 100000)]),
        _c([_ase("12710231", 194360, hoja="FLAMINGO"),
            _ase("12710231", 70000, hoja="ACOPIO", concepto="transporte")]),
    )
    assert r[0].valor_aseguradora == Decimal("264360")
    assert r[0].aseguradora.valor_por_concepto["transporte"] == Decimal("70000")
    assert r[0].aseguradora.hojas == ["ACOPIO", "FLAMINGO"]


def test_formatos_distintos_de_la_misma_orden_cruzan():
    r = conciliar(_c([_rep("01017550", 100000)]), _c([_ase("1017550.0", 500000)]))
    assert len(r) == 1 and r[0].tipo_coincidencia == "directa"
