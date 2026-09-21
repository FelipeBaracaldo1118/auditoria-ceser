"""Desglose por orden, siguiendo la cadena de principio a fin.

El recorrido es el que definio el area:

  1. En TECH: que repuestos se pidieron y que se presupuesto, item por item.
  2. En el archivo de la aseguradora: el desglose de lo cobrado, si se facturo
     (columna FACT) y el total, que incluye ademas los fletes.
  3. En el archivo de repuestos: cuanto nos cobro el proveedor por la pieza y
     con que factura.
  4. Se comparan: utilidad del repuesto y utilidad total del servicio.

Una fila por orden, con la trazabilidad completa para poder auditar cada cifra.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger(__name__)

COLUMNAS = [
    "orden",
    "enlace_en_tech",
    "aseguradora",
    "estado_sistema",
    # paso 1: el sistema
    "repuestos_pedidos_sistema",
    "presupuesto_detalle",
    "fuente_del_presupuesto",
    "presupuesto_repuestos",
    "presupuesto_mano_obra",
    "presupuesto_iva",
    "valor_total_servicio_sistema",
    # paso 3: el proveedor
    "factura_proveedor",
    "costo_proveedor",
    "fuente_del_costo",
    # paso 2: la aseguradora
    "cobrado_por_repuestos",
    "fuente_de_lo_cobrado",
    "factura_aseguradora",
    "se_cobro_a_la_aseguradora",
    "fletes",
    "total_cobrado_con_envios",
    # paso 4: la comparacion
    "utilidad_repuestos",
    "margen_repuestos",
    "utilidad_total_sin_iva",
    "margen_total_sin_iva",
    "utilidad_total_con_iva_y_fletes",
    "situaciones",
]


def _num(valor) -> str:
    return "" if valor is None else f"{valor:.2f}"


def _pct(valor) -> str:
    return "" if valor is None else f"{valor:.2f}"


def generar(resultado, cliente_tech, directorio: Path, sello: str | None = None,
            solo_conciliadas: bool = True, url_orden: str | None = None) -> dict[str, Path]:
    directorio.mkdir(parents=True, exist_ok=True)
    sello = sello or datetime.now().strftime("%Y%m%d_%H%M%S")

    fotos = [f for f in resultado.fotografias
             if not solo_conciliadas or f.tipo_coincidencia == "directa"]
    numeros = [f.orden_ceser for f in fotos]

    presupuestos: dict[str, list[dict]] = {}
    pedidos: dict[str, list[dict]] = {}
    if cliente_tech is not None and numeros:
        presupuestos = cliente_tech.detalle_presupuesto(numeros)
        pedidos = cliente_tech.repuestos_pedidos(numeros)

    ruta = directorio / f"desglose_ordenes_{sello}.csv"
    with open(ruta, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNAS)
        for f in sorted(fotos, key=lambda x: x.orden_ceser):
            items = presupuestos.get(f.orden_ceser, [])
            detalle = "; ".join(
                f"{i['tipo']} {i['valor']:,.0f}".replace(",", ".") for i in items if i["valor"] is not None
            )
            partes = pedidos.get(f.orden_ceser, [])
            resumen_partes = "; ".join(
                f"{p['parte']} x{p['cantidad']} ({p['estado']})" for p in partes[:6]
            )

            costo = f.costo_total_repuestos
            cobrado_rep = f.valor_repuestos_reconocido
            if cobrado_rep is None:
                cobrado_rep = f.valor_repuestos_tech

            utilidad_rep = (cobrado_rep - costo) if (cobrado_rep is not None and costo is not None) else None
            margen_rep = (utilidad_rep / cobrado_rep * 100) if (utilidad_rep is not None and cobrado_rep) else None
            utilidad_total_con = ((f.valor_aseguradora - costo)
                                  if (f.valor_aseguradora is not None and costo is not None) else None)

            w.writerow([
                f.orden_ceser,
                url_orden.format(orden=f.orden_ceser) if url_orden else "",
                f.aseguradora or "",
                f.estado_actual_tech or "",
                resumen_partes,
                detalle,
                "TECH · presupuesto_seguro" if items else "",
                _num(f.valor_repuestos_tech),
                _num(f.valor_mano_obra_tech),
                _num(f.iva_tech),
                _num(f.valor_total_tech),
                f.facturas_proveedor or "",
                _num(costo),
                f"Repuestos.xlsx · {f.fuente_repuestos}" if f.fuente_repuestos else "",
                _num(cobrado_rep),
                f"Aseguradoras.xlsx · {f.fuente_aseguradora}" if f.fuente_aseguradora else "",
                f.facturas or "",
                "si" if f.facturas else "no",
                _num(f.valor_transporte_cobrado),
                _num(f.valor_aseguradora),
                _num(utilidad_rep),
                _pct(margen_rep),
                _num(f.utilidad_servicio),
                _pct(f.margen_servicio),
                _num(utilidad_total_con),
                " || ".join(s.descripcion for s in f.situaciones),
            ])

    logger.info("Desglose por orden: %s filas en %s", len(fotos), ruta)
    return {"desglose": ruta}
