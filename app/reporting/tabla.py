"""Tabla de utilidad por orden, con las columnas que pidio el area.

Una fila por orden y nada mas: numero de orden enlazado a TECH, factura y precio
del proveedor, precio y factura de la aseguradora, y las dos utilidades.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.reporting.informe import CSS, TIPOGRAFIAS, _miles, _pesos

logger = logging.getLogger(__name__)

CSS_TABLA = """
.tabla-datos { margin-top: 2rem; }
.tabla-datos table { font-size: .85rem; }
.tabla-datos thead th { position: sticky; top: 0; z-index: 1; }
.tabla-datos tbody tr:hover td { background: var(--banda); }
.tabla-datos td a { color: var(--acento); text-decoration: none;
  border-bottom: 1px solid transparent; font-weight: 500; }
.tabla-datos td a:hover { border-bottom-color: var(--acento); }
.tabla-datos td a:focus-visible { outline: 2px solid var(--acento); outline-offset: 2px; }
.negativo { color: var(--critico); }
.sin-cobrar { color: var(--atencion); font-style: italic; }
tfoot td { padding: .9rem 1rem; border-top: 2px solid var(--tinta);
  font-weight: 600; background: var(--banda); }
.grupo-titulo { font-family: "IBM Plex Mono", monospace; font-size: .7rem;
  letter-spacing: .12em; text-transform: uppercase; color: var(--tinta-suave);
  padding: 1.2rem 1rem .5rem; background: var(--banda); }
"""


def _e(v) -> str:
    return html.escape(str(v)) if v is not None else ""


def _celda(valor: Decimal | None) -> str:
    if valor is None:
        return '<td class="num">—</td>'
    clase = "num negativo" if valor < 0 else "num"
    return f'<td class="{clase}">{_pesos(valor)}</td>'


def contenido(resultado, url_orden: str | None = None) -> str:
    fotos = [f for f in resultado.fotografias if f.tipo_coincidencia == "directa"]
    fotos.sort(key=lambda f: (f.aseguradora or "zzz", f.orden_ceser))
    fecha = datetime.fromisoformat(resultado.fecha_analisis)

    def cobrado_repuestos(f):
        return f.valor_repuestos_reconocido if f.valor_repuestos_reconocido is not None else f.valor_repuestos_tech

    p: list[str] = ['<div class="hoja">']
    p.append('<header class="pila encabezado">')
    p.append('<span class="rotulo">Auditoría financiera · CESER</span>')
    p.append("<h1>Utilidad orden por orden</h1>")
    p.append('<p class="bajada">Lo que nos cobró el proveedor, lo que se le cobró a la aseguradora '
             "y la utilidad de cada orden. El número de orden abre su ficha en TECH.</p>")
    p.append('<div class="ficha">')
    for clave, valor in (
        ("Fecha del análisis", fecha.strftime("%d/%m/%Y %H:%M")),
        ("Órdenes", _miles(len(fotos))),
        ("Fuente del costo", "Repuestos.xlsx"),
        ("Fuente del cobro", "Aseguradoras.xlsx y TECH"),
    ):
        p.append(f'<div><span class="clave">{_e(clave)}</span><span class="valor">{_e(valor)}</span></div>')
    p.append("</div></header>")

    p.append('<section class="tabla-datos"><div class="envoltura"><table>')
    p.append("<thead><tr>"
             "<th>Orden</th>"
             "<th>Factura proveedor</th>"
             '<th class="num">Nos cobró el proveedor</th>'
             '<th class="num">Cobrado por el repuesto</th>'
             '<th class="num">Utilidad del repuesto</th>'
             '<th class="num">Total del servicio</th>'
             "<th>Factura aseguradora</th>"
             '<th class="num">Total con envíos</th>'
             '<th class="num">Utilidad total</th>'
             "</tr></thead><tbody>")

    totales = {k: Decimal(0) for k in ("costo", "cobrado", "util_rep", "servicio", "total", "util_total")}
    aseguradora_actual = None
    for f in fotos:
        etiqueta = f.aseguradora or "Sin aseguradora identificada"
        if etiqueta != aseguradora_actual:
            aseguradora_actual = etiqueta
            p.append(f'<tr><td colspan="9" class="grupo-titulo">{_e(etiqueta)}</td></tr>')

        costo = f.costo_total_repuestos
        cobrado = cobrado_repuestos(f)
        util_rep = (cobrado - costo) if (cobrado is not None and costo is not None) else None

        if costo is not None:
            totales["costo"] += costo
        if cobrado is not None:
            totales["cobrado"] += cobrado
        if util_rep is not None:
            totales["util_rep"] += util_rep
        if f.valor_total_tech is not None:
            totales["servicio"] += f.valor_total_tech
        if f.valor_aseguradora is not None:
            totales["total"] += f.valor_aseguradora
        if f.utilidad_servicio is not None:
            totales["util_total"] += f.utilidad_servicio

        enlace = (f'<a href="{_e(url_orden.format(orden=f.orden_ceser))}" target="_blank" '
                  f'rel="noopener">{_e(f.orden_ceser)}</a>') if url_orden else _e(f.orden_ceser)
        factura_aseg = (f'{_e(f.facturas)}' if f.facturas
                        else '<span class="sin-cobrar">sin cobrar</span>')

        p.append("<tr>")
        p.append(f'<td class="orden">{enlace}</td>')
        p.append(f"<td>{_e(f.facturas_proveedor) or '—'}</td>")
        p.append(_celda(costo))
        p.append(_celda(cobrado))
        p.append(_celda(util_rep))
        p.append(_celda(f.valor_total_tech))
        p.append(f"<td>{factura_aseg}</td>")
        p.append(_celda(f.valor_aseguradora))
        p.append(_celda(f.utilidad_servicio))
        p.append("</tr>")

    p.append("</tbody><tfoot><tr>")
    p.append('<td colspan="2">Totales</td>')
    for clave in ("costo", "cobrado", "util_rep", "servicio"):
        p.append(f'<td class="num">{_pesos(totales[clave])}</td>')
    p.append("<td></td>")
    for clave in ("total", "util_total"):
        p.append(f'<td class="num">{_pesos(totales[clave])}</td>')
    p.append("</tr></tfoot></table></div>")
    p.append('<p class="nota" style="margin-top:1.25rem">La utilidad del repuesto es lo cobrado '
             "por la pieza menos lo que costó. La utilidad total suma la mano de obra facturada y "
             "no incluye IVA ni fletes, que se cobran al costo. El total con envíos sí los incluye, "
             "por eso es mayor que el total del servicio.</p>")
    p.append("</section></div>")
    return "\n".join(p)


def generar(resultado, directorio: Path, sello: str | None = None,
            url_orden: str | None = None) -> Path:
    directorio.mkdir(parents=True, exist_ok=True)
    sello = sello or datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta = directorio / f"tabla_utilidad_{sello}.html"
    ruta.write_text(
        '<!doctype html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Utilidad orden por orden</title>\n"
        f'<link rel="stylesheet" href="{TIPOGRAFIAS}">\n'
        f"<style>{CSS}{CSS_TABLA}</style>\n</head>\n<body>\n"
        + contenido(resultado, url_orden) + "\n</body>\n</html>\n",
        encoding="utf-8",
    )
    logger.info("Tabla de utilidad generada: %s", ruta)
    return ruta
