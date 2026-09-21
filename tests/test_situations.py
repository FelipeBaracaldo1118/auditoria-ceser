"""Pruebas de las reglas. Cada regla se evalua de forma independiente."""

from decimal import Decimal

import pandas as pd
import pytest

import app.rules.situations as sit
from app.ingestion.adapters import Consolidado, LineaAseguradora, LineaRepuesto
from app.normalization.money import normalizar_monto
from app.normalization.orders import normalizar_orden
from app.reconciliation.calculations import calcular
from app.reconciliation.matching import conciliar
from tests.test_matching import _ase, _c, _rep


def _evaluar(repuestos, aseguradoras, **kwargs):
    ordenes = conciliar(_c(repuestos), _c(aseguradoras))
    salida = []
    for orden in ordenes:
        valor_partes = orden.aseguradora.valor_repuestos if orden.aseguradora else None
        mano_obra = orden.aseguradora.valor_mano_obra if orden.aseguradora else None
        fin = calcular(orden.costo_repuestos, orden.valor_aseguradora,
                       valor_repuestos_reconocido=valor_partes,
                       valor_mano_obra=mano_obra,
                       valor_repuestos_con_iva=(orden.aseguradora.valor_repuestos_con_iva
                                                if orden.aseguradora else None),
                       valor_mano_obra_con_iva=(orden.aseguradora.valor_mano_obra_con_iva
                                                if orden.aseguradora else None),
                       **kwargs)
        salida.append((orden, fin, sit.evaluar(orden, fin)))
    return salida


def _tipos(situaciones):
    return {s.tipo_interno for s in situaciones}


def test_margen_bajo_se_explica_en_lenguaje_claro():
    (_, _, situaciones), = _evaluar([_rep("1017550", 900000, fecha="2026-05-01")],
                                    [_ase("1017550", 1190000, partes=1000000)])
    assert "LOW_MARGIN" in _tipos(situaciones)
    texto = next(s.descripcion for s in situaciones if s.tipo_interno == "LOW_MARGIN")
    assert "10,00 %" in texto and "15,00 %" in texto
    assert "LOW_MARGIN" not in texto


def test_margen_suficiente_no_genera_situacion():
    (_, _, situaciones), = _evaluar([_rep("1017550", 500000, fecha="2026-05-01")],
                                    [_ase("1017550", 1190000, partes=1000000)])
    assert "LOW_MARGIN" not in _tipos(situaciones)


def test_costo_mayor_que_el_valor_reconocido():
    (_, _, situaciones), = _evaluar([_rep("1017550", 850000, fecha="2026-05-01")],
                                    [_ase("1017550", 800000)])
    assert "COST_ABOVE_REVENUE" in _tipos(situaciones)
    texto = next(s.descripcion for s in situaciones if s.tipo_interno == "COST_ABOVE_REVENUE")
    assert "$850.000" in texto and "$800.000" in texto and "$50.000" in texto


def test_orden_sin_aseguradora_dentro_del_periodo():
    resultados = _evaluar([_rep("1017550", 100000, fecha="2026-05-01")], [])
    assert "ORDER_NOT_FOUND" in _tipos(resultados[0][2])


def test_orden_sin_aseguradora_fuera_del_periodo_no_genera_ruido():
    """El archivo de aseguradoras solo cubre 2026: una orden de 2021 no 'falta'."""
    resultados = _evaluar([_rep("1017550", 100000, fecha="2021-03-01")], [])
    assert "ORDER_NOT_FOUND" not in _tipos(resultados[0][2])


def test_orden_facturada_sin_repuestos():
    resultados = _evaluar([], [_ase("1017550", 500000)])
    assert "COST_NOT_FOUND" in _tipos(resultados[0][2])


def test_diferencia_entre_el_valor_cobrado_y_el_registrado():
    (_, _, situaciones), = _evaluar(
        [_rep("1017550", 300000, fecha="2026-05-01", reconocido=900000)],
        [_ase("1017550", 1200000, partes=950000)],
    )
    assert "VALUE_MISMATCH" in _tipos(situaciones)
    texto = next(s.descripcion for s in situaciones if s.tipo_interno == "VALUE_MISMATCH")
    assert "$950.000" in texto and "$900.000" in texto and "$50.000" in texto


def test_valores_iguales_no_generan_diferencia():
    (_, _, situaciones), = _evaluar(
        [_rep("1017550", 300000, fecha="2026-05-01", reconocido=900000)],
        [_ase("1017550", 1200000, partes=900000)],
    )
    assert "VALUE_MISMATCH" not in _tipos(situaciones)


def test_registros_sin_costo_se_avisan():
    (_, _, situaciones), = _evaluar(
        [_rep("1017550", 300000, fecha="2026-05-01"), _rep("1017550", None, fecha="2026-05-01")],
        [_ase("1017550", 1000000)],
    )
    assert "MISSING_COST_DATA" in _tipos(situaciones)


def test_una_orden_puede_tener_varias_situaciones():
    tipos = _tipos(_evaluar(
        [_rep("12710231", 900000, fecha="2026-05-01")],
        [_ase("12710231", 500000, hoja="FLAMINGO", partes=400000),
         _ase("12710231", 70000, hoja="ACOPIO", concepto="transporte")],
    )[0][2])
    assert {"LOW_MARGIN", "COST_ABOVE_REVENUE"} <= tipos
    # el envio facturado aparte no cuenta como cobro repetido
    assert "VALUE_IN_MULTIPLE_SHEETS" not in tipos


def test_margen_bajo_en_la_base_de_repuestos_aunque_el_total_este_bien():
    """Caso real 1017550: 34,95 % sobre el total pero -11,67 % sobre repuestos."""
    (_, _, situaciones), = _evaluar(
        [_rep("1017550", 335000, fecha="2026-05-01")],
        [_ase("1017550", 514956, partes=300000)],
    )
    texto = next(s.descripcion for s in situaciones if s.tipo_interno == "LOW_MARGIN")
    assert "cobrado por repuestos" in texto
    assert "-11,67 %" in texto


def test_margen_bajo_en_ambas_bases_se_reporta_completo():
    """Repuestos al costo y sin mano de obra: queda corto en las dos medidas."""
    (_, _, situaciones), = _evaluar(
        [_rep("1017550", 950000, fecha="2026-05-01")],
        [_ase("1017550", 1190000, partes=1000000)],
    )
    texto = next(s.descripcion for s in situaciones if s.tipo_interno == "LOW_MARGIN")
    assert "cobrado por repuestos" in texto and "servicio completo" in texto


def test_la_mano_de_obra_mejora_el_margen_del_servicio():
    """La misma orden con mano de obra cobrada supera el minimo en el servicio."""
    (_, fin, situaciones), = _evaluar(
        [_rep("1017550", 950000, fecha="2026-05-01")],
        [_ase("1017550", 1500000, partes=1000000, mano_obra=200000)],
    )
    assert fin.margen_sobre_repuestos == Decimal("5.00")
    assert fin.margen_servicio == Decimal("20.83")
    texto = next(s.descripcion for s in situaciones if s.tipo_interno == "LOW_MARGIN")
    assert "cobrado por repuestos" in texto
    assert "servicio completo" not in texto


def test_ordenes_que_se_revisan_con_tech_no_se_marcan_como_no_encontradas():
    linea = _rep("11953441", 29935, fecha="2026-05-01")
    linea.espera_aseguradora = False
    resultados = _evaluar([linea], [])
    assert "ORDER_NOT_FOUND" not in _tipos(resultados[0][2])


def test_ninguna_descripcion_muestra_codigos_internos():
    resultados = _evaluar(
        [_rep("1017550", 900000, fecha="2026-05-01")],
        [_ase("1017550", 800000, partes=500000)],
    )
    for _, _, situaciones in resultados:
        for s in situaciones:
            assert s.tipo_interno not in s.descripcion


class _Presupuesto:
    def __init__(self, valor_repuestos, valor_total=None, valor_mano_obra=None):
        self.valor_repuestos = Decimal(str(valor_repuestos))
        self.valor_mano_obra = Decimal(str(valor_mano_obra)) if valor_mano_obra is not None else None
        self.valor_total = Decimal(str(valor_total)) if valor_total is not None else None


class _Tech:
    """TECH ya consultado. `consultado=False` representa una corrida sin conexion."""

    def __init__(self, presupuesto=None, seguro=None, pagos=None, orden=None,
                 existe=True, consultado=True):
        self.presupuesto, self.seguro, self.pagos, self.orden = presupuesto, seguro, pagos, orden
        self.existe = existe
        self.consultado = consultado


def _evaluar_con_tech(repuestos, aseguradoras, tech):
    ordenes = conciliar(_c(repuestos), _c(aseguradoras))
    orden = ordenes[0]
    valor_partes = orden.aseguradora.valor_repuestos if orden.aseguradora else None
    fin = calcular(orden.costo_repuestos, orden.valor_aseguradora,
                   valor_repuestos_reconocido=valor_partes)
    return sit.evaluar(orden, fin, tech)


def test_cobrar_por_encima_del_presupuesto_es_normal():
    """El presupuesto no incluye utilidad: cobrar mas es lo esperado."""
    situaciones = _evaluar_con_tech(
        [_rep("12710414", 1000000, fecha="2026-05-01")],
        [_ase("12710414", 3000000, partes=1869151)],
        _Tech(presupuesto=_Presupuesto(1590000)),
    )
    assert "TECH_VALUE_MISMATCH" not in _tipos(situaciones)


def test_cobrar_por_debajo_del_presupuesto_es_anomalia():
    situaciones = _evaluar_con_tech(
        [_rep("12710414", 1000000, fecha="2026-05-01")],
        [_ase("12710414", 3000000, partes=1200000)],
        _Tech(presupuesto=_Presupuesto(1590000)),
    )
    assert "TECH_VALUE_MISMATCH" in _tipos(situaciones)
    texto = next(s.descripcion for s in situaciones if s.tipo_interno == "TECH_VALUE_MISMATCH")
    assert "por debajo del costo" in texto


def test_cobrar_exactamente_el_presupuesto_se_marca_si_se_exige_utilidad(monkeypatch):
    monkeypatch.setattr(sit, "EXIGIR_UTILIDAD_SOBRE_PRESUPUESTO", True)
    situaciones = _evaluar_con_tech(
        [_rep("12710414", 1000000, fecha="2026-05-01")],
        [_ase("12710414", 3000000, partes=1590000)],
        _Tech(presupuesto=_Presupuesto(1590000)),
    )
    assert "NO_PROFIT" in _tipos(situaciones)


def test_por_defecto_cobrar_igual_no_se_marca():
    situaciones = _evaluar_con_tech(
        [_rep("12710414", 1000000, fecha="2026-05-01")],
        [_ase("12710414", 3000000, partes=1590000)],
        _Tech(presupuesto=_Presupuesto(1590000)),
    )
    assert "NO_PROFIT" not in _tipos(situaciones)


def test_el_total_del_servicio_debe_cuadrar_con_lo_facturado():
    """Caso real 1017583: TECH 634.256,91 + fletes 47.300 = 681.556,91 facturado."""
    situaciones = _evaluar_con_tech(
        [_rep("1017583", 320000, fecha="2026-01-30")],
        [_ase("1017583", 681556.91, partes=440000, mano_obra=92989, transporte=47300)],
        _Tech(presupuesto=_Presupuesto(440000, valor_total=634256.91, valor_mano_obra=92989)),
    )
    assert "SERVICE_TOTAL_MISMATCH" not in _tipos(situaciones)


def test_si_falta_plata_en_la_factura_se_avisa():
    situaciones = _evaluar_con_tech(
        [_rep("1017583", 320000, fecha="2026-01-30")],
        [_ase("1017583", 600000, partes=440000, mano_obra=92989, transporte=47300)],
        _Tech(presupuesto=_Presupuesto(440000, valor_total=634256.91, valor_mano_obra=92989)),
    )
    assert "SERVICE_TOTAL_MISMATCH" in _tipos(situaciones)
    texto = next(s.descripcion for s in situaciones if s.tipo_interno == "SERVICE_TOTAL_MISMATCH")
    assert "faltan" in texto.lower()


# --- Flujo definido por el area: TOTAL COBRADO contra el total del archivo ---
# Los numeros son los de la orden 1017550 en produccion: la hoja cobra 392.989
# (300.000 de repuesto + 92.989 de mano de obra) y FALABELLA factura 514.957,
# que son esos 392.989 mas 74.668 de IVA y 47.300 de fletes.
def _flujo(total_cobrado, total_archivo, iva=None, transporte=None,
           mano_obra_hoja=None, mano_obra_archivo=None,
           repuesto_hoja=None, repuesto_archivo=None, hoja="FALABELLA"):
    (_, _, situaciones), = _evaluar(
        [_rep("1017550", 250000, fecha="2026-05-01", total_cobrado=total_cobrado,
              mano_obra=mano_obra_hoja, reconocido=repuesto_hoja)],
        [_ase("1017550", total_archivo, hoja=hoja, iva=iva, transporte=transporte,
              mano_obra=mano_obra_archivo, partes=repuesto_archivo)],
    )
    return situaciones


def test_total_del_archivo_igual_al_total_cobrado_no_genera_situacion():
    situaciones = _flujo(total_cobrado=392989, total_archivo=392989)
    assert not _tipos(situaciones) & {"BILLED_BELOW_CHARGED", "BILLED_ABOVE_CHARGED",
                                      "BILLING_NOT_BROKEN_DOWN"}


def test_total_mayor_explicado_por_iva_y_fletes_no_genera_situacion():
    """El caso normal: el archivo cobra mas porque suma IVA y transporte."""
    situaciones = _flujo(total_cobrado=392989, total_archivo=514957,
                         iva=74668, transporte=47300)
    assert not _tipos(situaciones) & {"BILLED_BELOW_CHARGED", "BILLED_ABOVE_CHARGED"}


def test_total_menor_que_el_cobrado_genera_alerta():
    situaciones = _flujo(total_cobrado=392989, total_archivo=380000)
    assert "BILLED_BELOW_CHARGED" in _tipos(situaciones)
    texto = next(s.descripcion for s in situaciones if s.tipo_interno == "BILLED_BELOW_CHARGED")
    assert "$392.989" in texto and "$380.000" in texto and "$12.989" in texto


def test_desglose_que_no_cierra_por_debajo_es_alerta_y_nombra_la_causa():
    """1017583: el archivo factura 440.000 de repuesto donde la hoja cobro 450.000."""
    situaciones = _flujo(total_cobrado=542989, total_archivo=681557,
                         iva=101557, transporte=47300,
                         repuesto_hoja=450000, repuesto_archivo=440000)
    assert "BILLED_BELOW_CHARGED" in _tipos(situaciones)
    texto = next(s.descripcion for s in situaciones if s.tipo_interno == "BILLED_BELOW_CHARGED")
    assert "$450.000" in texto and "$440.000" in texto
    assert "valor del repuesto" in texto


def test_desglose_que_no_cierra_por_encima_senala_la_mano_de_obra():
    """Caso frecuente: la hoja quedo con la tarifa vieja de mano de obra."""
    situaciones = _flujo(total_cobrado=692989, total_archivo=882900,
                         iva=132569, transporte=52600,
                         mano_obra_hoja=92989, mano_obra_archivo=97731)
    assert "BILLED_ABOVE_CHARGED" in _tipos(situaciones)
    texto = next(s.descripcion for s in situaciones if s.tipo_interno == "BILLED_ABOVE_CHARGED")
    assert "mano de obra" in texto and "$92.989" in texto and "$97.731" in texto


def test_hoja_sin_iva_ni_fletes_se_informa_sin_alertar():
    """SURA y MOK no discriminan: la diferencia no se puede desglosar."""
    situaciones = _flujo(total_cobrado=392989, total_archivo=514957, hoja="SURA")
    assert "BILLING_NOT_BROKEN_DOWN" in _tipos(situaciones)
    assert "BILLED_ABOVE_CHARGED" not in _tipos(situaciones)


def test_sin_total_cobrado_la_regla_no_opina():
    situaciones = _flujo(total_cobrado=None, total_archivo=514957)
    assert not _tipos(situaciones) & {"BILLED_BELOW_CHARGED", "BILLED_ABOVE_CHARGED",
                                      "BILLING_NOT_BROKEN_DOWN"}


# --- Las dos lecturas del margen: sin IVA (como el archivo) y con IVA -------
def _dos_lecturas(costo, total_archivo, partes, iva, transporte=None, mano_obra=None):
    from app.reconciliation.matching import agrupar_aseguradoras
    grupos = agrupar_aseguradoras(_c([_ase("1017550", total_archivo, partes=partes, iva=iva,
                                           transporte=transporte, mano_obra=mano_obra)]))
    grupo = grupos["1017550"]
    return calcular(Decimal(costo), Decimal(total_archivo),
                    valor_repuestos_reconocido=grupo.valor_repuestos,
                    valor_mano_obra=grupo.valor_mano_obra,
                    valor_repuestos_con_iva=grupo.valor_repuestos_con_iva,
                    valor_mano_obra_con_iva=grupo.valor_mano_obra_con_iva)


def test_repuestos_gravados_el_iva_se_reparte_sobre_las_partes():
    """1017550: partes 300.000, IVA 74.668 sobre toda la base, fletes 47.300."""
    fin = _dos_lecturas(costo=335000, total_archivo=514957, partes=300000,
                        iva=74668, transporte=47300, mano_obra=92989)
    assert fin.valor_repuestos_con_iva == Decimal("357000.00")
    assert fin.margen_sobre_repuestos == Decimal("-11.67")   # lectura del archivo
    assert fin.margen_sobre_repuestos_con_iva == Decimal("6.16")


def test_repuestos_no_gravados_el_valor_con_iva_es_el_mismo():
    """FLAMINGO: 16 filas donde el IVA solo grava la mano de obra."""
    fin = _dos_lecturas(costo=2400000, total_archivo=2706300, partes=2590000,
                        iva=18569, transporte=0, mano_obra=97731)
    assert fin.valor_repuestos_con_iva == Decimal("2590000")
    assert fin.margen_sobre_repuestos == fin.margen_sobre_repuestos_con_iva


def test_hoja_sin_iva_no_produce_lectura_con_iva():
    """Nada se inventa: si la hoja no discrimina IVA, la segunda lectura es None."""
    fin = _dos_lecturas(costo=335000, total_archivo=514957, partes=300000, iva=None)
    assert fin.valor_repuestos_con_iva is None
    assert fin.margen_sobre_repuestos_con_iva is None


def test_patron_de_iva_desconocido_no_se_reparte():
    """Un IVA que no corresponde a ninguno de los dos patrones queda sin lectura."""
    fin = _dos_lecturas(costo=335000, total_archivo=500000, partes=300000, iva=33333)
    assert fin.valor_repuestos_con_iva is None


def test_margen_bajo_se_mide_con_iva_y_menciona_la_lectura_del_archivo():
    """1017550: 6,16 % comparable (con IVA), -11,67 % en el archivo. Sigue marcada."""
    (_, _, situaciones), = _evaluar(
        [_rep("1017550", 335000, fecha="2026-05-01")],
        [_ase("1017550", 514957, partes=300000, iva=74668, transporte=47300, mano_obra=92989)],
    )
    s = next(x for x in situaciones if x.tipo_interno == "LOW_MARGIN")
    assert "6,16 %" in s.descripcion          # la lectura que decide
    assert "-11,67 %" in s.descripcion         # la del archivo, como contexto
    assert s.severidad == "media"


def test_margen_que_solo_era_bajo_por_no_sumar_el_iva_ya_no_se_marca():
    """1017683: 10,94 % en el archivo pero 25,16 % comparable. Decision del area 13/09/2026."""
    (_, _, situaciones), = _evaluar(
        [_rep("1017683", 285000, fecha="2026-05-01")],
        [_ase("1017683", 517457, partes=320000, iva=78468, transporte=26000, mano_obra=92989)],
    )
    assert "LOW_MARGIN" not in _tipos(situaciones)


def test_sin_lectura_con_iva_el_margen_bajo_es_solo_informativo():
    """Si la hoja no deja reconstruir el IVA, la lectura del archivo sale corta: no se alarma."""
    (_, _, situaciones), = _evaluar([_rep("1017550", 900000, fecha="2026-05-01")],
                                    [_ase("1017550", 1190000, partes=1000000)])
    s = next(x for x in situaciones if x.tipo_interno == "LOW_MARGIN")
    assert s.severidad == "informativa" and "confirmarlo a mano" in s.descripcion


# --- Los mensajes deben nombrar la hoja concreta de cada lado ---------------
def test_los_mensajes_nombran_las_dos_hojas():
    """Sin el nombre de la hoja, quien revisa no sabe donde ir a comprobar."""
    (_, _, situaciones), = _evaluar(
        [_rep("1017583", 400000, hoja=" HAROLD H.T", fecha="2026-05-01",
              total_cobrado=542989, reconocido=450000, mano_obra=92989)],
        [_ase("1017583", 681557, hoja="FALABELLA", partes=440000,
              iva=101268, transporte=47300, mano_obra=92989)],
    )
    texto = next(s.descripcion for s in situaciones if s.tipo_interno == "BILLED_BELOW_CHARGED")
    assert "HAROLD H.T" in texto and "FALABELLA" in texto
    assert "la hoja de repuestos" not in texto   # la frase generica que no ubicaba nada


def test_el_nombre_de_la_hoja_se_limpia_de_espacios():
    """La hoja se llama ' HAROLD H.T', con espacio al inicio, en el Excel."""
    from app.reconciliation.matching import conciliar
    orden, = conciliar(_c([_rep("1017583", 400000, hoja=" HAROLD H.T")]),
                       _c([_ase("1017583", 500000, hoja="FALABELLA")]))
    assert sit.hoja_repuestos(orden) == "HAROLD H.T"
    assert sit.hoja_aseguradora(orden) == "FALABELLA"


# --- Orden repetida dentro de una hoja: Harold cobrando dos veces -----------
def test_orden_repetida_con_misma_factura_y_valor_es_alerta():
    lineas = [_rep("1017550", 300000, hoja=" HAROLD H.T", fecha="2026-05-01", factura="93081"),
              _rep("1017550", 300000, hoja=" HAROLD H.T", fecha="2026-05-01", factura="93081")]
    lineas[1].fila = 1800
    (_, _, situaciones), = _evaluar(lineas, [])
    assert "DUPLICATE_IN_SHEET" in _tipos(situaciones)
    s = next(x for x in situaciones if x.tipo_interno == "DUPLICATE_IN_SHEET")
    assert s.severidad == "alta"
    assert "cobro doble" in s.descripcion and "HAROLD H.T" in s.descripcion


def test_orden_repetida_con_valores_distintos_solo_se_marca_para_confirmar():
    """Pueden ser dos repuestos legitimos de la misma orden."""
    lineas = [_rep("1017550", 300000, hoja=" HAROLD H.T", fecha="2026-05-01", factura="93081"),
              _rep("1017550", 145000, hoja=" HAROLD H.T", fecha="2026-05-01", factura="93082")]
    lineas[1].fila = 1800
    (_, _, situaciones), = _evaluar(lineas, [])
    s = next(x for x in situaciones if x.tipo_interno == "DUPLICATE_IN_SHEET")
    assert s.severidad == "media"
    assert "repuestos legitimos" in s.descripcion


def test_orden_en_dos_hojas_distintas_no_es_repeticion():
    (_, _, situaciones), = _evaluar(
        [_rep("1017550", 300000, hoja=" HAROLD H.T", fecha="2026-05-01"),
         _rep("1017550", 300000, hoja="HAROLD NO EN BASE DATOS", fecha="2026-05-01")], [])
    assert "DUPLICATE_IN_SHEET" not in _tipos(situaciones)


# --- Cobrada a la aseguradora antes de que el proveedor nos facturara -------
def test_cobro_por_delante_de_la_compra():
    (_, _, situaciones), = _evaluar(
        [_rep("1017595", 325000, hoja=" HAROLD H.T", fecha="2026-03-19")],
        [_ase("1017595", 500000, periodo="2026-02")],
    )
    assert "BILLED_BEFORE_PURCHASE" in _tipos(situaciones)
    texto = next(s.descripcion for s in situaciones if s.tipo_interno == "BILLED_BEFORE_PURCHASE")
    assert "2026-02" in texto and "2026-03-19" in texto


def test_el_orden_natural_no_genera_situacion():
    (_, _, situaciones), = _evaluar(
        [_rep("1017595", 325000, hoja=" HAROLD H.T", fecha="2026-03-19")],
        [_ase("1017595", 500000, periodo="2026-04")],
    )
    assert "BILLED_BEFORE_PURCHASE" not in _tipos(situaciones)


def test_mismo_mes_no_genera_situacion():
    (_, _, situaciones), = _evaluar(
        [_rep("1017595", 325000, hoja=" HAROLD H.T", fecha="2026-03-19")],
        [_ase("1017595", 500000, periodo="2026-03")],
    )
    assert "BILLED_BEFORE_PURCHASE" not in _tipos(situaciones)


def test_hoja_que_lista_un_repuesto_por_fila_no_cuenta_como_repeticion():
    """SAMSUNG lista un repuesto por fila: varias filas por orden son lo normal."""
    lineas = [_rep("11953386", 50000, hoja="SAMSUNG", fecha="2026-05-01"),
              _rep("11953386", 60000, hoja="SAMSUNG", fecha="2026-05-01")]
    for l in lineas:
        l.una_fila_por_orden = False
    lineas[1].fila = 9
    (_, _, situaciones), = _evaluar(lineas, [])
    assert "DUPLICATE_IN_SHEET" not in _tipos(situaciones)


# --- No preguntarle al sistema no es una conclusion sobre la orden ---------
def test_sin_consultar_tech_no_se_afirma_que_la_orden_no_existe():
    """Una corrida con --sin-tech no puede reportar 'no existe en el sistema'."""
    situaciones = _evaluar_con_tech(
        [_rep("1017550", 300000, fecha="2026-05-01")],
        [_ase("1017550", 500000, partes=400000)],
        _Tech(existe=False, consultado=False),
    )
    assert "ORDER_NOT_IN_TECH" not in _tipos(situaciones)


def test_consultado_y_ausente_si_se_reporta():
    situaciones = _evaluar_con_tech(
        [_rep("1017550", 300000, fecha="2026-05-01")],
        [_ase("1017550", 500000, partes=400000)],
        _Tech(existe=False, consultado=True),
    )
    assert "ORDER_NOT_IN_TECH" in _tipos(situaciones)
    texto = next(s.descripcion for s in situaciones if s.tipo_interno == "ORDER_NOT_IN_TECH")
    assert "no existe en el sistema" in texto


# --- Facturar en dos hojas: el reparto normal contra el cobro doble ---------
def test_reparacion_y_transporte_en_hojas_distintas_es_lo_normal():
    """La reparación se factura en FLAMINGO y el envío en ACOPIO: no es anomalía."""
    (_, _, situaciones), = _evaluar(
        [_rep("1017550", 300000, fecha="2026-05-01")],
        [_ase("1017550", 500000, hoja="FLAMINGO", partes=400000),
         _ase("1017550", 47300, hoja="ACOPIO", concepto="transporte")],
    )
    assert "VALUE_IN_MULTIPLE_SHEETS" not in _tipos(situaciones)


def test_dos_hojas_cobrando_el_mismo_concepto_si_es_anomalia():
    (_, _, situaciones), = _evaluar(
        [_rep("1017550", 300000, fecha="2026-05-01")],
        [_ase("1017550", 500000, hoja="FLAMINGO", partes=400000),
         _ase("1017550", 500000, hoja="FALABELLA", partes=400000)],
    )
    assert "VALUE_IN_MULTIPLE_SHEETS" in _tipos(situaciones)
    s = next(x for x in situaciones if x.tipo_interno == "VALUE_IN_MULTIPLE_SHEETS")
    assert s.severidad == "alta"
    assert "FALABELLA" in s.descripcion and "FLAMINGO" in s.descripcion


# --- Cobro tardio del proveedor (definido por el area, 13/09/2026) ----------
def test_cobro_tardio_de_varios_meses_es_alta_y_confirma_cobro_unico():
    """Caso real 1017386: Falabella facturo en 2025-11, Harold cobro el 2026-04-07."""
    (_, _, situaciones), = _evaluar(
        [_rep("1017386", 275000, hoja=" HAROLD H.T", fecha="2026-04-07", factura="92465")],
        [_ase("1017386", 767654, periodo="2025-11")],
    )
    s = next(x for x in situaciones if x.tipo_interno == "BILLED_BEFORE_PURCHASE")
    assert s.severidad == "alta"
    assert "5 meses despues" in s.descripcion
    assert "una sola vez" in s.descripcion and "92465" in s.descripcion


def test_cobro_tardio_de_un_mes_es_media():
    (_, _, situaciones), = _evaluar(
        [_rep("1017595", 325000, hoja=" HAROLD H.T", fecha="2026-03-19")],
        [_ase("1017595", 500000, periodo="2026-02")],
    )
    s = next(x for x in situaciones if x.tipo_interno == "BILLED_BEFORE_PURCHASE")
    assert s.severidad == "media" and "1 mes despues" in s.descripcion


def test_cobro_tardio_que_ademas_esta_repetido_lo_advierte():
    linea = _rep("1017386", 275000, hoja=" HAROLD H.T", fecha="2026-04-07", factura="92465")
    linea.apariciones_previas = [{"fila": 1650, "factura": "92465", "costo": Decimal("275000"), "fecha": None}]
    (_, _, situaciones), = _evaluar([linea], [_ase("1017386", 767654, periodo="2026-03")])
    tardio = next(x for x in situaciones if x.tipo_interno == "BILLED_BEFORE_PURCHASE")
    assert "2 veces" in tardio.descripcion and "cobro doble" in tardio.descripcion
    assert tardio.severidad == "alta"
    # y la repeticion aparece tambien como su propia situacion
    dup = next(x for x in situaciones if x.tipo_interno == "DUPLICATE_IN_SHEET")
    assert "1650" in dup.descripcion and dup.severidad == "alta"


def test_cobro_repetido_antes_y_despues_del_corte_se_detecta():
    """La primera mitad del cobro doble puede estar por encima de la fila 1719."""
    linea = _rep("1017999", 80000, hoja=" HAROLD H.T", fecha="2026-01-10", factura="93000")
    linea.fila = 1800
    linea.apariciones_previas = [{"fila": 1700, "factura": "93000", "costo": Decimal("80000"), "fecha": None}]
    (_, _, situaciones), = _evaluar([linea], [])
    s = next(x for x in situaciones if x.tipo_interno == "DUPLICATE_IN_SHEET")
    assert "1700, 1800" in s.descripcion and s.severidad == "alta"


def test_la_ventana_de_cobertura_sale_de_los_datos():
    try:
        sit.fijar_cobertura_aseguradoras("2025-10", "2026-09")
        resultados = _evaluar([_rep("1017550", 100000, fecha="2025-11-15")], [])
        assert "ORDER_NOT_FOUND" in _tipos(resultados[0][2])
    finally:
        sit.fijar_cobertura_aseguradoras("2026-01", "2026-12")
