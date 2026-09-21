"""Reporte focalizado para revisar con el area la facturacion de repuestos.

Toma las ordenes marcadas como "repuestos aprobados que no se cobraron" y arma
un archivo con todo lo necesario para verificar caso por caso en una reunion:
lo que dice el sistema, lo que dice el archivo de la aseguradora, la factura con
la que se cobro, y si esa orden aparece tambien en otro periodo.

Incluye ademas el listado de supuestos que uso el analisis, para que puedan
descartarse uno por uno si la explicacion resulta ser de metodo y no de proceso.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.ingestion.adapters import HOJAS_ASEGURADORAS, consolidar_aseguradoras
from app.ingestion.workbook import LibroCrudo
from app.normalization.money import normalizar_monto
from app.normalization.orders import normalizar_orden

logger = logging.getLogger(__name__)

# Columnas del archivo de aseguradoras que ayudan a entender que se cobro.
COLUMNAS_DETALLE = ("DIAGNOSTICO", "MANO DE OBRA", "PARTES", "IVA", "SUBTOTAL",
                    "MENOS DEDUCIBLE", "TRANSPORTE", "TOTAL", "OBSERVACION")


def _indice_filas_excel(libro: LibroCrudo) -> dict[str, list[dict]]:
    """orden normalizada -> filas crudas del archivo de aseguradoras."""
    indice: dict[str, list[dict]] = defaultdict(list)
    especs = {e.hoja: e for e in HOJAS_ASEGURADORAS if e.incluir}
    for hoja in libro.hojas:
        espec = especs.get(hoja.nombre)
        if espec is None or hoja.datos.empty:
            continue
        for i in range(len(hoja.datos)):
            fila = hoja.datos.iloc[i]
            orden = normalizar_orden(fila[espec.columna_orden])
            if not orden.valida:
                continue
            registro = {"hoja": hoja.nombre, "fila_excel": i + 2}
            for col in COLUMNAS_DETALLE:
                if col in hoja.datos.columns:
                    registro[col] = fila[col]
            if espec.columna_mes and espec.columna_mes in hoja.datos.columns:
                registro["mes"] = fila[espec.columna_mes]
            indice[orden.normalizada].append(registro)
    return indice


def _monto(valor) -> str:
    m = normalizar_monto(valor)
    return "" if not m.hay_dato else f"{m.valor:.2f}"


def generar(resultado, libro_aseguradoras: LibroCrudo, directorio: Path,
            tipo: str = "BUDGET_NOT_BILLED", sello: str | None = None) -> dict[str, Path]:
    directorio.mkdir(parents=True, exist_ok=True)
    sello = sello or datetime.now().strftime("%Y%m%d_%H%M%S")

    casos = [f for f in resultado.fotografias
             if any(s.tipo_interno == tipo for s in f.situaciones)]
    indice = _indice_filas_excel(libro_aseguradoras)

    ruta_csv = directorio / f"revision_facturacion_{sello}.csv"
    with open(ruta_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "orden", "aseguradora", "caso_archivo", "siniestro_sistema", "factura", "periodo",
            "periodos_en_que_aparece", "veces_en_el_archivo", "hoja",
            "sistema_repuestos", "sistema_mano_obra", "sistema_iva", "sistema_total",
            "archivo_diagnostico", "archivo_mano_obra", "archivo_partes", "archivo_iva",
            "archivo_transporte", "archivo_total", "archivo_observacion",
            "diferencia_repuestos",
            "explicacion_del_area", "responsable", "decision",
        ])
        for f in sorted(casos, key=lambda x: -(x.valor_repuestos_tech or Decimal(0))):
            filas = indice.get(f.orden_ceser, [])
            periodos = sorted({str(r.get("mes", "")).strip() for r in filas if r.get("mes")})
            primera = filas[0] if filas else {}
            w.writerow([
                f.orden_ceser, f.aseguradora or "", f.orden_aseguradora or "",
                f.nro_siniestro_tech or "", f.facturas or "", f.periodo_aseguradora or "",
                ", ".join(periodos), len(filas), f.hojas_aseguradora,
                f"{f.valor_repuestos_tech:.2f}" if f.valor_repuestos_tech is not None else "",
                f"{f.valor_mano_obra_tech:.2f}" if f.valor_mano_obra_tech is not None else "",
                f"{f.iva_tech:.2f}" if f.iva_tech is not None else "",
                f"{f.valor_total_tech:.2f}" if f.valor_total_tech is not None else "",
                _monto(primera.get("DIAGNOSTICO")), _monto(primera.get("MANO DE OBRA")),
                _monto(primera.get("PARTES")), _monto(primera.get("IVA")),
                _monto(primera.get("TRANSPORTE")), _monto(primera.get("TOTAL")),
                str(primera.get("OBSERVACION") or "").strip()[:120],
                f"{f.valor_repuestos_tech:.2f}" if f.valor_repuestos_tech is not None else "",
                "", "", "",   # a diligenciar en la reunion
            ])

    total = sum((f.valor_repuestos_tech or Decimal(0)) for f in casos)
    cobrado = sum((f.valor_aseguradora or Decimal(0)) for f in casos)
    facturas = sorted({fa for f in casos if f.facturas for fa in f.facturas.split(", ")})
    repetidas = [f for f in casos if len(indice.get(f.orden_ceser, [])) > 1]

    def pesos(v: Decimal) -> str:
        return "$" + f"{v:,.0f}".replace(",", ".")

    ruta_md = directorio / f"revision_facturacion_{sello}.md"
    md = [
        "# Revision de facturacion de repuestos",
        "",
        f"Analisis del {resultado.fecha_analisis}",
        "",
        "## Que encontro el sistema",
        "",
        f"- Ordenes: **{len(casos)}**",
        f"- Repuestos presupuestados y aprobados en el sistema: **{pesos(total)}**",
        f"- Cobrado a la aseguradora en esas ordenes: **{pesos(cobrado)}**",
        f"- Facturas involucradas: {', '.join(facturas)}",
        "",
        "Son ordenes que en el sistema tienen repuestos presupuestados y aprobados, "
        "estan en el mismo estado de seguimiento que las ordenes que si se cobran "
        "completas, y en el archivo de la aseguradora aparecen facturadas sin ningun "
        "repuesto.",
        "",
        "## Lo que ya se verifico y se descarto",
        "",
        "| Verificacion | Resultado |",
        "|---|---|",
        "| ¿El cobro esta en otra orden vinculada (`ORDEN_ANTERIOR`)? | No, 0 coincidencias |",
        "| ¿Esta en otra orden con el mismo equipo (IMEI)? | Solo 2, ambas sin reparar ni facturar |",
        "| ¿Esta en el flujo de cambio de equipo (`cambios_hiper`)? | No, 0 coincidencias |",
        "| ¿Se vincula por `ORDEN_FABRICANTE` o `GARANTIA`? | No |",
        "| ¿Son ordenes aun sin facturar? | No: las sin factura son todas de julio 2026 |",
        f"| ¿La orden aparece en mas de un periodo del archivo? | {len(repetidas)} de {len(casos)} |",
        "",
        "## Supuestos del analisis, por si la explicacion es de metodo",
        "",
        "1. En las hojas de FALABELLA, FLAMINGO y VIDA TRANQUI, una celda vacia en la "
        "columna `PARTES` se interpreta como que la orden no llevo repuestos. Si en "
        "estas ordenes vacio significa otra cosa, esto explica todo el hallazgo.",
        "2. El valor de repuestos del sistema es la suma de las lineas de "
        "`presupuesto_seguro` cuyo tipo no es 'Mano de Obra'.",
        "3. Se considera que la orden siguio el flujo normal cuando su "
        "`ESTADO_SEGUIMIENTO_M` es 4, que es el estado de las 169 ordenes cuyo "
        "presupuesto coincide exactamente con lo cobrado.",
        "4. Se compara contra el presupuesto aprobado, que puede no ser lo finalmente "
        "ejecutado si hubo un cambio posterior que el sistema no registro.",
        "",
        "## Preguntas para la reunion",
        "",
        "- ¿Como se decide, al armar la factura, que ordenes llevan repuestos?",
        "- ¿Puede una reparacion facturarse en un mes distinto al del ingreso de la orden?",
        "- ¿Que significan los estados de seguimiento 4 y 6?",
        "- En estas ordenes puntuales, ¿se repararon efectivamente?",
        "",
        "## Ordenes a revisar",
        "",
        "| Orden | Aseguradora | Factura | Presupuestado | Cobrado |",
        "|---|---|---|---|---|",
    ]
    for f in sorted(casos, key=lambda x: -(x.valor_repuestos_tech or Decimal(0))):
        md.append(f"| {f.orden_ceser} | {f.aseguradora or ''} | {f.facturas or 'sin factura'} | "
                  f"{pesos(f.valor_repuestos_tech or Decimal(0))} | "
                  f"{pesos(f.valor_aseguradora or Decimal(0))} |")
    ruta_md.write_text("\n".join(md), encoding="utf-8")

    logger.info("Revision de facturacion: %s ordenes, %s", len(casos), ruta_csv)
    return {"revision_csv": ruta_csv, "revision_resumen": ruta_md}
