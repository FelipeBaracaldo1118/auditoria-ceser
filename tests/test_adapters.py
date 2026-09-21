"""Pruebas de los adaptadores por hoja hacia el modelo comun."""

import dataclasses
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from app.ingestion.adapters import (
    HOJAS_REPUESTOS,
    HojaFaltante,
    consolidar_aseguradoras,
    consolidar_repuestos,
)

# Las hojas de prueba tienen pocas filas: se prueba el mapeo de columnas sin el
# corte por fila, que tiene su propia prueba mas abajo.
ESPECS_SIN_CORTE = tuple(dataclasses.replace(e, fila_desde=None) for e in HOJAS_REPUESTOS)
from app.ingestion.workbook import leer_libro


def _libro(tmp_path: Path, nombre: str, hojas: dict[str, list[list]]):
    ruta = tmp_path / nombre
    with pd.ExcelWriter(ruta, engine="openpyxl") as w:
        for hoja, filas in hojas.items():
            pd.DataFrame(filas).to_excel(w, sheet_name=hoja, index=False, header=False)
    return leer_libro(ruta)


COLS_HAROLD = ["ITEM", "ORDEN", "MARCA", "MODELO", "REPARACION", "FECHA DE ENTREGA",
               "FECHA DE RECIBIDO", "SI", "NO", "FACT", "FECHA DE FACTURA", "VALOR",
               "FECHA DE PAGO", "VALOR REPUESTO ASEGURADORA", "MANO DE OBRA",
               "TOTAL COBRADO", "UTILIDAD", "%", "VALID", "PAGO", "OBSERVACIONES"]


def _fila_harold(orden, valor, reconocido=None, fecha="2026-05-12"):
    fila = [None] * len(COLS_HAROLD)
    fila[1], fila[10], fila[11], fila[13] = orden, pd.Timestamp(fecha), valor, reconocido
    return fila


@pytest.fixture
def libro_repuestos(tmp_path):
    # SAMSUNG: encabezados corridos, el costo con IVA vive en la posicion 16 sin titulo.
    cols_samsung = ["Orden", "Orden Fabricante", "Sucursal", "No Partes", "Marca", "Estado",
                    "pedido", None, "Nombre técnico", "Fecha de Pedido", "Modelo", "Serie",
                    "Servicio", "Cant Fal", "Cant Ent.", "Id Fab.", None, None, None]
    fila_samsung = [11953441, 4175060498, "DOMICILIOS", "DA81-06006B", "SAMSUNG", "PTE",
                    "OWB1", 1328654236, "TECNICO", pd.Timestamp("2026-02-13"), None, None,
                    "FUERA DE GARANTIA", 1, 25156, 1.19, 29935.64, None, None]
    return _libro(tmp_path, "rep.xlsx", {
        "HAROLD ORIGINAL": [COLS_HAROLD, _fila_harold(1017550, 335000, 300000)],
        " HAROLD H.T": [COLS_HAROLD, _fila_harold(1017550, 335000, 300000),
                        _fila_harold("REPARACION RELOJ", 50000)],
        "HAROLD NO EN BASE DATOS": [COLS_HAROLD, _fila_harold(12788905, 20000)],
        "SAMSUNG": [cols_samsung, fila_samsung],
        "SUPER WEGA": [["F. CTA COBRO", "FACT", "FECHA ODS", "ORDEN", "MODELO", "SERIE",
                        "MARCA", "REPARACION EFECTUADA", "COBRO PROVEE", "COBRO CESER",
                        "UTILIDAD", "X PAGAR", "ESTADO", "EGRESO", "ESTADO ABONOS"],
                       [None, 1198, pd.Timestamp("2026-04-01"), 11952878, None, None, None,
                        None, 260000, 969000, -709000, 0, "ENTREGDAO OK", None, None]],
    })


def test_hoja_de_captura_previa_se_excluye(libro_repuestos):
    c = consolidar_repuestos(libro_repuestos, ESPECS_SIN_CORTE)
    assert "HAROLD ORIGINAL" not in c.hojas_incluidas
    assert c.hojas_excluidas[0][0] == "HAROLD ORIGINAL"
    assert "duplicaria" in c.hojas_excluidas[0][1]
    # la orden aparece una sola vez pese a estar en las dos hojas
    assert sum(1 for l in c.lineas if l.orden.normalizada == "1017550") == 1


def test_costo_por_hoja(libro_repuestos):
    c = consolidar_repuestos(libro_repuestos, ESPECS_SIN_CORTE)
    costos = {l.hoja_origen: l.costo.valor for l in c.lineas}
    assert costos[" HAROLD H.T"] == Decimal("335000")          # VALOR
    assert costos["SUPER WEGA"] == Decimal("260000")           # COBRO PROVEE
    assert costos["SAMSUNG"] == Decimal("29935.64")            # columna 16, con IVA


def test_valor_reconocido_de_repuestos_se_conserva(libro_repuestos):
    c = consolidar_repuestos(libro_repuestos, ESPECS_SIN_CORTE)
    linea = next(l for l in c.lineas if l.hoja_origen == " HAROLD H.T")
    assert linea.valor_reconocido.valor == Decimal("300000")


def test_ordenes_invalidas_quedan_registradas_como_descarte(libro_repuestos):
    c = consolidar_repuestos(libro_repuestos, ESPECS_SIN_CORTE)
    assert any(d["orden_original"] == "REPARACION RELOJ" for d in c.descartes)
    assert all(l.orden.valida for l in c.lineas)


@pytest.fixture
def libro_aseguradoras(tmp_path):
    return _libro(tmp_path, "ase.xlsx", {
        "ACOPIO": [["MES FACTURADO", "FACT", "ORDEN", "CASO CW", "VALOR TOTAL"],
                   ["ENERO", 7022, 12710231, 57417, 70000]],
        "FALABELLA": [["MES", "FACT", "ORDEN", "CASO CW", "PARTES", "TOTAL"],
                      ["MAYO ", 7020, 1017550, 60817, 300000, 514956.91]],
        "FLAMINGO": [["MES", "FACT", "ORDEN", "CASO CW", "PARTES", "TOTAL"],
                     ["JUNIO", 7026, 12710231, 57417, None, 194360.09]],
        "VIDA TRANQUI": [["MES FACTURADO", "FACT", "NÚMERO DE CASO", "ORDEN",
                          "VALOR DE PARTES Y/O PIEZAS", "VALOR TOTAL DEL SERVICIO"],
                         ["MAYO", 7285, 60710, 12789226, 0, 59655.74]],
        "SURA": [["MES", "FACT", "Nro. de Certificado", "ORDEN", "Total $"],
                 ["ENERO", 6917, "ALS4000000195628", 18763411, 724242.05]],
        "MOK": [["MES", "FACT", "CASO", "ORDEN", "Suma de Total"],
                ["ABRIL", 7115, 20925974, 15292973, 100000]],
    })


def test_cada_hoja_de_aseguradora_aporta_su_valor(libro_aseguradoras):
    c = consolidar_aseguradoras(libro_aseguradoras)
    valores = {l.hoja_origen: l.valor.valor for l in c.lineas}
    assert valores["FALABELLA"] == Decimal("514956.91")
    assert valores["SURA"] == Decimal("724242.05")
    assert valores["MOK"] == Decimal("100000")
    assert valores["VIDA TRANQUI"] == Decimal("59655.74")
    assert len(c.hojas_incluidas) == 6


def test_acopio_se_marca_como_transporte(libro_aseguradoras):
    c = consolidar_aseguradoras(libro_aseguradoras)
    acopio = next(l for l in c.lineas if l.hoja_origen == "ACOPIO")
    assert acopio.concepto == "transporte"
    assert next(l for l in c.lineas if l.hoja_origen == "FALABELLA").concepto == "reparacion"


def test_el_mes_se_convierte_al_periodo_del_anio_confirmado(libro_aseguradoras):
    c = consolidar_aseguradoras(libro_aseguradoras)
    periodos = {l.hoja_origen: l.periodo for l in c.lineas}
    assert periodos["FALABELLA"] == "2026-05"   # "MAYO " con espacio sobrante
    assert periodos["ACOPIO"] == "2026-01"


def test_orden_de_la_aseguradora_se_conserva(libro_aseguradoras):
    c = consolidar_aseguradoras(libro_aseguradoras)
    assert next(l for l in c.lineas if l.hoja_origen == "SURA").orden_aseguradora == "ALS4000000195628"
    assert next(l for l in c.lineas if l.hoja_origen == "MOK").orden_aseguradora == "20925974"


def test_hoja_faltante_falla_con_mensaje_claro(tmp_path):
    libro = _libro(tmp_path, "incompleto.xlsx", {"OTRA COSA": [["A"], [1]]})
    with pytest.raises(HojaFaltante) as exc:
        consolidar_aseguradoras(libro)
    assert "ACOPIO" in str(exc.value)


def test_harold_ht_se_audita_desde_la_fila_acordada():
    """El area definio auditar HAROLD H.T desde la fila 1719 en adelante."""
    espec = next(e for e in HOJAS_REPUESTOS if e.hoja == " HAROLD H.T")
    assert espec.fila_desde == 1719


def test_el_corte_por_fila_descarta_lo_anterior(tmp_path):
    filas = [COLS_HAROLD] + [_fila_harold(1000000 + i, 1000) for i in range(10)]
    libro = _libro(tmp_path, "corte.xlsx", {" HAROLD H.T": filas})
    espec = dataclasses.replace(
        next(e for e in HOJAS_REPUESTOS if e.hoja == " HAROLD H.T"), fila_desde=8
    )
    c = consolidar_repuestos(libro, (espec,))
    # fila 8 de Excel = septima fila de datos: quedan 4 de 10
    assert len(c.lineas) == 4
    assert c.lineas[0].orden.normalizada == "1000006"


def test_hojas_que_se_resuelven_contra_tech():
    por_hoja = {e.hoja: e.espera_aseguradora for e in HOJAS_REPUESTOS}
    assert por_hoja["SAMSUNG"] is False
    assert por_hoja["SUPER WEGA"] is False
    assert por_hoja[" HAROLD H.T"] is True


def test_falabella_trata_partes_vacia_como_cero(libro_aseguradoras, tmp_path):
    """Definido por el area: en FALABELLA una celda vacia en PARTES = sin repuestos."""
    libro = _libro(tmp_path, "fal.xlsx", {
        "FALABELLA": [["MES", "FACT", "ORDEN", "CASO CW", "PARTES", "TOTAL"],
                      ["MAYO", 7020, 1017615, 60817, None, 74229.63]],
    })
    from app.ingestion.adapters import HOJAS_ASEGURADORAS
    espec = next(e for e in HOJAS_ASEGURADORAS if e.hoja == "FALABELLA")
    c = consolidar_aseguradoras(libro, (espec,))
    partes = c.lineas[0].valor_repuestos
    assert partes.hay_dato and partes.valor == Decimal("0")
    assert "definicion del area" in partes.motivo


def test_flamingo_trata_partes_vacia_como_cero(tmp_path):
    """La hoja misma suma PARTES vacia como cero en su SUBTOTAL."""
    libro = _libro(tmp_path, "fla.xlsx", {
        "FLAMINGO": [["MES", "FACT", "ORDEN", "CASO CW", "PARTES", "TOTAL"],
                     ["JUNIO", 7026, 12710231, 57417, None, 194360.09]],
    })
    from app.ingestion.adapters import HOJAS_ASEGURADORAS
    espec = next(e for e in HOJAS_ASEGURADORAS if e.hoja == "FLAMINGO")
    c = consolidar_aseguradoras(libro, (espec,))
    assert c.lineas[0].valor_repuestos.valor == Decimal("0")


def test_hojas_sin_desglose_de_repuestos_se_resuelven_con_tech():
    from app.ingestion.adapters import HOJAS_ASEGURADORAS
    por_hoja = {e.hoja: e.espera_repuestos for e in HOJAS_ASEGURADORAS}
    assert por_hoja["SURA"] is False and por_hoja["MOK"] is False
    assert por_hoja["FALABELLA"] is True


def test_hojas_donde_partes_vacia_significa_cero():
    """FALABELLA y VIDA TRANQUI por definicion del area; FLAMINGO ademas verificado
    con la aritmetica del archivo (SUBTOTAL = DIAGNOSTICO + MO + PARTES + IVA)."""
    from app.ingestion.adapters import HOJAS_ASEGURADORAS
    por_hoja = {e.hoja: e.repuestos_vacio_es_cero for e in HOJAS_ASEGURADORAS}
    assert por_hoja["FALABELLA"] and por_hoja["FLAMINGO"] and por_hoja["VIDA TRANQUI"]


# --- Columnas renombradas en los archivos -----------------------------------
def test_columna_se_encuentra_por_su_nombre_vigente_o_el_anterior():
    """VIDA TRANQUI renombro 'VALOR TOTAL DEL SERVICIO' a 'TOTAL' el 13/09/2026."""
    import pandas as pd
    from app.ingestion.adapters import _columna
    from app.ingestion.workbook import HojaCruda

    def hoja(columnas):
        h = HojaCruda.__new__(HojaCruda)
        h.nombre, h.datos = "VIDA TRANQUI", pd.DataFrame({c: [1] for c in columnas})
        return h

    alias = ("TOTAL", "VALOR TOTAL DEL SERVICIO")
    assert list(_columna(hoja(["TOTAL"]), alias)) == [1]
    assert list(_columna(hoja(["VALOR TOTAL DEL SERVICIO"]), alias)) == [1]


def test_columna_ausente_con_todos_sus_nombres_explica_cuales_busco():
    import pandas as pd
    import pytest
    from app.ingestion.adapters import HojaFaltante, _columna
    from app.ingestion.workbook import HojaCruda

    h = HojaCruda.__new__(HojaCruda)
    h.nombre, h.datos = "VIDA TRANQUI", pd.DataFrame({"OTRA": [1]})
    with pytest.raises(HojaFaltante, match="VALOR TOTAL DEL SERVICIO"):
        _columna(h, ("TOTAL", "VALOR TOTAL DEL SERVICIO"))


# --- El archivo solo trae el nombre del mes: el año se deduce ---------------
def test_el_anio_se_deduce_del_cruce_diciembre_enero():
    """Version del 13/09/2026: empieza en OCTUBRE de 2025."""
    from datetime import date
    from app.ingestion.adapters import periodos_de_hoja
    meses = ["OCTUBRE", "NOVIEMBRE", "DICIEMBRE", "ENERO", "FEBRERO", "JULIO"]
    assert periodos_de_hoja(meses, date(2026, 9, 13)) == [
        "2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-07"]


def test_el_ultimo_mes_nunca_queda_en_el_futuro():
    from datetime import date
    from app.ingestion.adapters import periodos_de_hoja
    # corrido en enero de 2027, un archivo que termina en noviembre es de 2026
    assert periodos_de_hoja(["OCTUBRE", "NOVIEMBRE"], date(2027, 1, 10)) == ["2026-10", "2026-11"]
    # y uno que termina en el mes en curso, es de este año
    assert periodos_de_hoja(["AGOSTO", "SEPTIEMBRE"], date(2026, 9, 13)) == ["2026-08", "2026-09"]


def test_meses_mal_escritos_se_reconocen():
    """Casos reales: 'AGOSO' en MOK y 'SEPTIEM' en SURA."""
    from datetime import date
    from app.ingestion.adapters import periodos_de_hoja
    assert periodos_de_hoja(["Julio", "AGOSO", "SEPTIEM "], date(2026, 9, 13)) == [
        "2026-07", "2026-08", "2026-09"]


def test_una_fila_fuera_de_orden_no_cambia_el_anio():
    """ABRIL despues de MAYO es una fila atrasada, no un año nuevo."""
    from datetime import date
    from app.ingestion.adapters import periodos_de_hoja
    assert periodos_de_hoja(["MARZO", "MAYO", "ABRIL", "JUNIO"], date(2026, 9, 1)) == [
        "2026-03", "2026-05", "2026-04", "2026-06"]


def test_celdas_vacias_o_irreconocibles_quedan_sin_periodo():
    from datetime import date
    from app.ingestion.adapters import periodos_de_hoja
    assert periodos_de_hoja(["ENERO", None, "xx", "MARZO"], date(2026, 9, 1)) == [
        "2026-01", None, None, "2026-03"]
    assert periodos_de_hoja([None, ""], date(2026, 9, 1)) == [None, None]


# --- Alcance de Harold H.T definido por lo que cubre el archivo de aseguradoras ---
def _harold_con_cobertura(tmp_path):
    from app.ingestion.adapters import Cobertura
    filas = [COLS_HAROLD,
             _fila_harold(1000001, 100, fecha="2021-03-10"),   # historica, sin contraparte: fuera
             _fila_harold(1017296, 335000, fecha="2025-08-28"), # anterior, pero facturada en el archivo: dentro
             _fila_harold(1017500, 355000, fecha="2025-10-05"), # dentro del periodo cubierto: dentro
             _fila_harold(1017999, 90000, fecha=None)]          # sin fecha y sin contraparte: fuera
    libro = _libro(tmp_path, "cobertura.xlsx", {" HAROLD H.T": filas})
    espec = next(e for e in HOJAS_REPUESTOS if e.hoja == " HAROLD H.T")
    cobertura = Cobertura(desde="2025-10", ordenes=frozenset({"1017296"}))
    return libro, espec, cobertura


def test_se_audita_lo_que_el_archivo_de_aseguradoras_cubre(tmp_path):
    libro, espec, cobertura = _harold_con_cobertura(tmp_path)
    c = consolidar_repuestos(libro, (espec,), cobertura=cobertura)
    assert sorted(l.orden.normalizada for l in c.lineas) == ["1017296", "1017500"]


def test_una_orden_facturada_no_queda_fuera_por_ser_anterior_al_periodo(tmp_path):
    """Caso real: 38 ordenes cobradas por Harold en ago-nov 2025, arriba de la fila 1719."""
    libro, espec, cobertura = _harold_con_cobertura(tmp_path)
    c = consolidar_repuestos(libro, (espec,), cobertura=cobertura)
    linea = next(l for l in c.lineas if l.orden.normalizada == "1017296")
    assert linea.costo.valor == 335000


def test_lo_que_queda_fuera_del_alcance_sigue_visible_para_detectar_duplicados(tmp_path):
    from app.ingestion.adapters import Cobertura
    filas = [COLS_HAROLD,
             _fila_harold(1017500, 355000, fecha="2025-06-01"),  # fuera del alcance
             _fila_harold(1017500, 355000, fecha="2025-11-01")]  # dentro
    libro = _libro(tmp_path, "dup.xlsx", {" HAROLD H.T": filas})
    espec = next(e for e in HOJAS_REPUESTOS if e.hoja == " HAROLD H.T")
    c = consolidar_repuestos(libro, (espec,), cobertura=Cobertura("2025-10", frozenset()))
    assert len(c.lineas) == 1
    assert [p["fila"] for p in c.lineas[0].apariciones_previas] == [2]


def test_sin_archivo_de_aseguradoras_se_usa_el_corte_por_fila(tmp_path):
    filas = [COLS_HAROLD] + [_fila_harold(1000000 + i, 1000) for i in range(10)]
    libro = _libro(tmp_path, "respaldo.xlsx", {" HAROLD H.T": filas})
    espec = dataclasses.replace(next(e for e in HOJAS_REPUESTOS if e.hoja == " HAROLD H.T"), fila_desde=8)
    assert len(consolidar_repuestos(libro, (espec,)).lineas) == 4
