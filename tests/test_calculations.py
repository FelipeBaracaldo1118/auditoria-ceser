"""Casos controlados de los calculos financieros (seccion 49 de la especificacion)."""

from decimal import Decimal

from app.reconciliation.calculations import calcular, margen, saldo, utilidad


def test_caso_utilidad_50_por_ciento():
    r = calcular(costo_repuestos=Decimal("500000"), valor_aseguradora=Decimal("1000000"))
    assert r.utilidad_esperada == Decimal("500000")
    assert r.margen_esperado == Decimal("50.00")


def test_caso_margen_10_por_ciento():
    r = calcular(costo_repuestos=Decimal("900000"), valor_aseguradora=Decimal("1000000"))
    assert r.utilidad_esperada == Decimal("100000")
    assert r.margen_esperado == Decimal("10.00")


def test_costo_superior_al_valor_da_utilidad_negativa():
    r = calcular(costo_repuestos=Decimal("850000"), valor_aseguradora=Decimal("800000"))
    assert r.utilidad_esperada == Decimal("-50000")
    assert r.margen_esperado < 0


def test_sin_datos_no_se_inventan_ceros():
    r = calcular(costo_repuestos=None, valor_aseguradora=Decimal("900000"))
    assert r.utilidad_esperada is None and r.margen_esperado is None


def test_valor_cero_no_produce_division_por_cero():
    assert margen(Decimal("100"), Decimal("0")) is None


def test_saldo_y_utilidad_segun_pagos():
    r = calcular(
        costo_repuestos=Decimal("820000"),
        valor_aseguradora=Decimal("900000"),
        total_pagado=Decimal("500000"),
    )
    assert r.saldo == Decimal("400000")
    assert r.utilidad_segun_pagos == Decimal("-320000")


def test_margen_sobre_repuestos_usa_la_porcion_de_partes():
    r = calcular(
        costo_repuestos=Decimal("335000"),
        valor_aseguradora=Decimal("514956.91"),
        valor_repuestos_reconocido=Decimal("300000"),
    )
    assert r.margen_esperado > 0
    assert r.margen_sobre_repuestos == Decimal("-11.67")


def test_sin_valor_de_repuestos_no_hay_margen_de_servicio():
    """Con la mano de obra sola, el margen del servicio no significa nada."""
    r = calcular(
        costo_repuestos=Decimal("275000"),
        valor_aseguradora=None,
        valor_repuestos_reconocido=None,
        valor_mano_obra=Decimal("92989"),
    )
    assert r.base_servicio is None
    assert r.margen_servicio is None


def test_los_tres_niveles_de_utilidad():
    """Caso real 1017907: repuesto 350.000, cobrado 575.000, mano de obra 97.731."""
    r = calcular(
        costo_repuestos=Decimal("350000"),
        valor_aseguradora=Decimal("853149.89"),
        valor_repuestos_reconocido=Decimal("575000"),
        valor_mano_obra=Decimal("97731"),
    )
    assert r.utilidad_sobre_repuestos == Decimal("225000")
    assert r.margen_sobre_repuestos == Decimal("39.13")
    assert r.base_servicio == Decimal("672731")
    assert r.utilidad_servicio == Decimal("322731")
    assert r.margen_servicio == Decimal("47.97")
    # el margen sobre el total facturado queda inflado por IVA y fletes
    assert r.margen_esperado == Decimal("58.98")
