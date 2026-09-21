"""Pruebas del perfilado: las columnas se marcan como candidatas, no como hechos."""

import pandas as pd

from app.exploration.profiling import (
    PerfilHoja,
    inconsistencias_entre_hojas,
    perfilar_columna,
    perfilar_hoja,
)
from app.ingestion.workbook import HojaCruda


def _hoja(datos: pd.DataFrame, nombre="Hoja1") -> HojaCruda:
    return HojaCruda(nombre=nombre, indice=0, crudo=datos, fila_encabezado=0, datos=datos)


def test_columna_de_orden_por_nombre_y_forma():
    serie = pd.Series(["10582", "10583", "AX-44921", "10585"])
    perfil = perfilar_columna(serie, "No. Orden", 0)
    assert perfil.candidata_orden is not None
    assert perfil.candidata_monetaria is None
    assert perfil.candidata_orden["formatos_detectados"]["con_letras"] == 1


def test_formatos_problematicos_de_orden_se_reportan():
    serie = pd.Series(["010582", "10582.0", "10582 ", "10582"])
    perfil = perfilar_columna(serie, "orden", 0)
    formatos = perfil.candidata_orden["formatos_detectados"]
    assert formatos["con_ceros_a_la_izquierda"] == 1
    assert formatos["con_sufijo_decimal"] == 1
    assert formatos["con_espacios_sobrantes"] == 1


def test_columna_monetaria():
    serie = pd.Series([300000, 100000, 50000, 1250000])
    perfil = perfilar_columna(serie, "Valor aseguradora", 0)
    assert perfil.candidata_monetaria is not None
    assert perfil.candidata_orden is None


def test_monetaria_como_texto_con_simbolos():
    serie = pd.Series(["$300.000", "$1.000.000", "$50.000"])
    perfil = perfilar_columna(serie, "costo", 0)
    assert perfil.candidata_monetaria is not None


def test_nulos_y_ceros_se_cuentan_distinto():
    serie = pd.Series([0, None, 0, 500])
    perfil = perfilar_columna(serie, "valor", 0)
    assert perfil.nulos == 1
    assert perfil.total == 4
    assert perfil.porcentaje_nulos == 25.0


def test_mezcla_de_tipos_se_observa():
    serie = pd.Series([1000, "mil", 2000])
    perfil = perfilar_columna(serie, "valor", 0)
    assert any("Mezcla de tipos" in o for o in perfil.observaciones)


def test_hoja_sin_columna_de_orden_genera_observacion():
    datos = pd.DataFrame({"descripcion": ["a", "b"], "comentario": ["x", "y"]})
    perfil = perfilar_hoja(_hoja(datos))
    assert any("candidata a numero de orden" in o for o in perfil.observaciones)


def test_inconsistencias_entre_hojas():
    h1 = perfilar_hoja(_hoja(pd.DataFrame({"Orden": ["1"], "Valor": [10]}), "A"))
    h2 = perfilar_hoja(_hoja(pd.DataFrame({"Orden": ["1"], "Prima": [10]}), "B"))
    hallazgos = inconsistencias_entre_hojas([h1, h2])
    texto = " ".join(hallazgos)
    assert "orden" in texto.lower()
    assert "prima" in texto.lower()
