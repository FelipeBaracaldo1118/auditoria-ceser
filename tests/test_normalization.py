"""Pruebas de normalizacion de ordenes y montos."""

from decimal import Decimal

import pandas as pd
import pytest

from app.normalization.money import normalizar_monto, sumar
from app.normalization.orders import normalizar_orden


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("12788905", "12788905"),
        (12788905, "12788905"),
        ("12788905 ", "12788905"),
        ("12788905.0", "12788905"),
        ("012788905", "12788905"),
        (12788905.0, "12788905"),
        ("  1017550  ", "1017550"),
    ],
)
def test_formatos_equivalentes_dan_la_misma_orden(entrada, esperado):
    r = normalizar_orden(entrada)
    assert r.valida
    assert r.normalizada == esperado


def test_conserva_el_valor_original():
    r = normalizar_orden(" 012788905 ")
    assert r.original == "012788905"
    assert r.normalizada == "12788905"


def test_sufijo_se_separa():
    r = normalizar_orden("1362246-R")
    assert r.valida
    assert r.normalizada == "1362246"
    assert r.sufijo == "R"
    assert "sufijo" in r.motivo


@pytest.mark.parametrize("basura", ["REPARACION RELOJ GIOVANNI", "?", "IPHONE JOOAO", "", None, "   "])
def test_texto_libre_no_es_orden(basura):
    r = normalizar_orden(basura)
    assert not r.valida
    assert r.motivo


def test_longitud_fuera_de_rango_se_rechaza_con_motivo():
    r = normalizar_orden("123")
    assert not r.valida
    assert "digitos" in r.motivo


def test_nan_de_pandas_es_vacio():
    assert not normalizar_orden(float("nan")).valida
    assert not normalizar_orden(pd.NA).valida


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        (300000, Decimal("300000")),
        ("$1.000.000", Decimal("1000000")),
        ("1.000.000,50", Decimal("1000000.50")),
        ("1,000,000.50", Decimal("1000000.50")),
        ("59655.74", Decimal("59655.74")),
        ("(1.000)", Decimal("-1000")),
        (0, Decimal("0")),
    ],
)
def test_montos(entrada, esperado):
    assert normalizar_monto(entrada).valor == esperado


def test_cero_no_es_lo_mismo_que_vacio():
    cero, vacio = normalizar_monto(0), normalizar_monto(None)
    assert cero.hay_dato and cero.valor == 0
    assert not vacio.hay_dato and vacio.valor is None


def test_error_de_formula_se_reporta():
    r = normalizar_monto("#VALUE!")
    assert not r.hay_dato
    assert "error de formula" in r.motivo


def test_suma_ignora_faltantes_pero_no_los_convierte_en_cero():
    total, aportaron = sumar([normalizar_monto(100), normalizar_monto(None), normalizar_monto(50)])
    assert total == Decimal("150") and aportaron == 2
    assert sumar([normalizar_monto(None)]) == (None, 0)
