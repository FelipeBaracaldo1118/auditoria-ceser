"""Pruebas de lectura de libros y deteccion de encabezado."""

from pathlib import Path

import pandas as pd
import pytest

from app.ingestion.workbook import detectar_fila_encabezado, leer_libro, validar_cantidad_hojas


def _escribir_libro(ruta: Path, hojas: dict[str, list[list]]) -> Path:
    with pd.ExcelWriter(ruta, engine="openpyxl") as writer:
        for nombre, filas in hojas.items():
            pd.DataFrame(filas).to_excel(writer, sheet_name=nombre, index=False, header=False)
    return ruta


def test_encabezado_en_primera_fila(tmp_path):
    ruta = _escribir_libro(
        tmp_path / "l.xlsx",
        {"Hoja1": [["Orden", "Repuesto", "Valor"], ["10582", "Pantalla", 300000]]},
    )
    libro = leer_libro(ruta)
    hoja = libro.hojas[0]
    assert hoja.fila_encabezado == 0
    assert list(hoja.datos.columns) == ["Orden", "Repuesto", "Valor"]
    assert len(hoja.datos) == 1


def test_encabezado_desplazado_por_titulo(tmp_path):
    ruta = _escribir_libro(
        tmp_path / "l.xlsx",
        {
            "Hoja1": [
                ["REPORTE DE REPUESTOS 2026", None, None],
                [None, None, None],
                ["Orden", "Repuesto", "Valor"],
                ["10582", "Pantalla", 300000],
                ["10582", "Bateria", 100000],
            ]
        },
    )
    hoja = leer_libro(ruta).hojas[0]
    assert hoja.fila_encabezado == 2
    assert list(hoja.datos.columns) == ["Orden", "Repuesto", "Valor"]
    assert len(hoja.datos) == 2


def test_columnas_duplicadas_y_sin_nombre(tmp_path):
    ruta = _escribir_libro(
        tmp_path / "l.xlsx",
        {"Hoja1": [["Orden", "Valor", "Valor", None], ["10582", 1, 2, 3]]},
    )
    hoja = leer_libro(ruta).hojas[0]
    assert list(hoja.datos.columns) == ["Orden", "Valor", "Valor__1", "__sin_nombre_3"]


def test_valores_se_leen_sin_conversion(tmp_path):
    """Un codigo con ceros a la izquierda debe conservarse como estaba."""
    ruta = _escribir_libro(
        tmp_path / "l.xlsx",
        {"Hoja1": [["Orden"], ["010582"], ["10582 "], [10582]]},
    )
    hoja = leer_libro(ruta).hojas[0]
    valores = list(hoja.datos["Orden"])
    assert valores[0] == "010582"
    assert valores[1] == "10582 "
    assert valores[2] == 10582


def test_validacion_cantidad_hojas(tmp_path):
    ruta = _escribir_libro(
        tmp_path / "l.xlsx",
        {"A": [["Orden"], ["1"]], "B": [["Orden"], ["2"]]},
    )
    libro = leer_libro(ruta)
    assert validar_cantidad_hojas(libro, 2)["cumple"] is True
    fallo = validar_cantidad_hojas(libro, 4)
    assert fallo["cumple"] is False
    assert "4" in fallo["mensaje"] and "2" in fallo["mensaje"]


def test_hoja_vacia_no_rompe(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.title = "Vacia"
    ruta = tmp_path / "vacio.xlsx"
    wb.save(ruta)
    hoja = leer_libro(ruta).hojas[0]
    assert hoja.datos.empty


def test_detectar_fila_encabezado_sin_datos():
    assert detectar_fila_encabezado(pd.DataFrame()) is None
