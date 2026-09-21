"""Pruebas de exportacion del reporte exploratorio."""

import json

import pandas as pd

from app.drive.client import ArchivoDescargado
from app.exploration.profiling import perfilar_hoja
from app.exploration.report import a_markdown, construir_reporte, exportar
from app.ingestion.workbook import HojaCruda


def _archivo() -> ArchivoDescargado:
    return ArchivoDescargado(
        drive_file_id="abc123",
        nombre_archivo="Repuestos.xlsx",
        mime_type="xlsx",
        modified_time="2026-08-18T22:30:00-05:00",
        size_bytes=1024,
        md5_checksum_drive="deadbeef",
        sha256_local="a" * 64,
        fecha_descarga="2026-08-18T22:30:05-05:00",
        ruta_local="/tmp/Repuestos.xlsx",
        origen="drive",
    )


def _perfil():
    datos = pd.DataFrame({"Orden": ["10582", "10583"], "Costo": [300000, 100000]})
    return perfilar_hoja(HojaCruda("Hoja1", 0, datos, 0, datos))


def test_reporte_incluye_version_del_archivo():
    reporte = construir_reporte(
        "2026-08-18T22:30:00",
        [("Repuestos.xlsx", _archivo(), {"cumple": True, "mensaje": "ok", "hojas_esperadas": 1, "hojas_encontradas": 1}, [_perfil()])],
    )
    version = reporte["archivos"][0]["version_utilizada"]
    assert version["drive_file_id"] == "abc123"
    assert version["modified_time"] == "2026-08-18T22:30:00-05:00"
    assert version["sha256_local"]


def test_exportar_genera_tres_archivos(tmp_path):
    reporte = construir_reporte(
        "2026-08-18T22:30:00",
        [("Repuestos.xlsx", _archivo(), {"cumple": False, "mensaje": "Se esperaban 4 hojas y se encontraron 1.", "hojas_esperadas": 4, "hojas_encontradas": 1}, [_perfil()])],
    )
    rutas = exportar(reporte, tmp_path, sello="prueba")
    assert set(rutas) == {"json", "markdown", "csv"}
    for ruta in rutas.values():
        assert ruta.exists() and ruta.stat().st_size > 0

    cargado = json.loads(rutas["json"].read_text(encoding="utf-8"))
    assert cargado["archivos"][0]["validacion_hojas"]["cumple"] is False

    md = rutas["markdown"].read_text(encoding="utf-8")
    assert "REVISAR" in md
    assert "Orden" in md


def test_markdown_no_afirma_significado_de_columnas():
    reporte = construir_reporte(
        "2026-08-18T22:30:00",
        [("Repuestos.xlsx", _archivo(), {"cumple": True, "mensaje": "ok", "hojas_esperadas": 1, "hojas_encontradas": 1}, [_perfil()])],
    )
    md = a_markdown(reporte)
    assert "candidata" in md.lower()


# --- Del navegador se copia la URL, no el File ID --------------------------
def test_id_de_drive_acepta_la_url_pegada():
    from app.config.settings import id_de_drive
    assert id_de_drive(
        "https://docs.google.com/spreadsheets/d/1lJZaBcDeFgHiJkLmNoPqRsTuVwXyZ012/edit#gid=0"
    ) == "1lJZaBcDeFgHiJkLmNoPqRsTuVwXyZ012"
    assert id_de_drive(
        "https://drive.google.com/file/d/17MdIwJzAbCdEfGhIjKlMnOpQrStUvWx/view?usp=sharing"
    ) == "17MdIwJzAbCdEfGhIjKlMnOpQrStUvWx"
    assert id_de_drive("https://drive.google.com/open?id=1lJZaBcDeFgHiJkLmNoPqRsTuVwXyZ012") \
        == "1lJZaBcDeFgHiJkLmNoPqRsTuVwXyZ012"


def test_id_de_drive_deja_pasar_el_id_suelto():
    from app.config.settings import id_de_drive
    assert id_de_drive("1lJZaBcDeFgHiJkLmNoPqRsTuVwXyZ012") == "1lJZaBcDeFgHiJkLmNoPqRsTuVwXyZ012"
    assert id_de_drive(None) is None


def test_una_url_sin_id_reconocible_falla_con_mensaje_claro():
    import pytest
    from app.config.settings import ConfigError, id_de_drive
    with pytest.raises(ConfigError, match="File ID"):
        id_de_drive("https://docs.google.com/spreadsheets/algo-raro")


# --- Cada orden se consulta en la pagina que le corresponde ----------------
def _ajustes(**cambios):
    import dataclasses
    from app.config.settings import get_settings
    return dataclasses.replace(get_settings(), **cambios)


def test_las_ordenes_con_prefijo_van_a_la_otra_pagina():
    """Confirmado por el area (21/09/2026): 119, 125 y 127 se consultan aparte."""
    s = _ajustes(url_orden_tech="https://sistema/seguro.php?orden={orden}",
                 url_orden_tech_alterna="https://sistema/loc_orden.php?orden={orden}",
                 prefijos_orden_alterna=("119", "125", "127"))
    assert s.url_de_orden("11953441") == "https://sistema/loc_orden.php?orden=11953441"
    assert s.url_de_orden("12789116") == "https://sistema/loc_orden.php?orden=12789116"
    assert s.url_de_orden("12551000") == "https://sistema/loc_orden.php?orden=12551000"
    # las demas siguen yendo a la consulta de seguros
    assert s.url_de_orden("1017550") == "https://sistema/seguro.php?orden=1017550"
    assert s.url_de_orden("1364658") == "https://sistema/seguro.php?orden=1364658"


def test_sin_pagina_alterna_configurada_todo_va_a_la_de_siempre():
    s = _ajustes(url_orden_tech="https://sistema/seguro.php?orden={orden}",
                 url_orden_tech_alterna=None, prefijos_orden_alterna=())
    assert s.url_de_orden("11953441") == "https://sistema/seguro.php?orden=11953441"


def test_una_orden_vacia_no_produce_enlace():
    assert _ajustes(url_orden_tech="https://sistema/x?o={orden}").url_de_orden("") is None
