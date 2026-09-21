"""Informe visual de la auditoria, pensado para presentar a gerencia.

Genera una pagina HTML autocontenida a partir del resultado de la conciliacion.
No agrega informacion nueva: presenta la misma que los CSV, ordenada para que
alguien que no conoce el sistema entienda que hay que revisar y por que.
"""

from __future__ import annotations

import html
import logging
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger(__name__)

TIPOGRAFIAS = (
    "https://fonts.googleapis.com/css2?"
    "family=IBM+Plex+Mono:wght@400;500&"
    "family=IBM+Plex+Sans:wght@400;450;500;600&"
    "family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400&display=swap"
)

CSS = """
:root {
  --papel: #f6f7f5;
  --superficie: #ffffff;
  --linea: #d9ddd6;
  --linea-suave: #e9ece7;
  --tinta: #16211c;
  --tinta-media: #4a564f;
  --tinta-suave: #6f7a73;
  --acento: #1e5b4f;
  --acento-claro: #eef3f0;
  --critico: #a8321f;
  --critico-fondo: #fbeeeb;
  --atencion: #8a5a12;
  --atencion-fondo: #fbf4e7;
  --neutro: #4a564f;
  --neutro-fondo: #eef0ed;
  --banda: #fafbf9;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --papel: #12160f;
    --superficie: #191e18;
    --linea: #2f3830;
    --linea-suave: #232b24;
    --tinta: #e8ede7;
    --tinta-media: #b0bcb3;
    --tinta-suave: #87948b;
    --acento: #7fc0ac;
    --acento-claro: #1c2a24;
    --critico: #e8907c;
    --critico-fondo: #2d1e1a;
    --atencion: #d9ab60;
    --atencion-fondo: #2b2418;
    --neutro: #b0bcb3;
    --neutro-fondo: #232b24;
    --banda: #1c211b;
  }
}
:root[data-theme="dark"] {
  --papel: #12160f;
  --superficie: #191e18;
  --linea: #2f3830;
  --linea-suave: #232b24;
  --tinta: #e8ede7;
  --tinta-media: #b0bcb3;
  --tinta-suave: #87948b;
  --acento: #7fc0ac;
  --acento-claro: #1c2a24;
  --critico: #e8907c;
  --critico-fondo: #2d1e1a;
  --atencion: #d9ab60;
  --atencion-fondo: #2b2418;
  --neutro: #b0bcb3;
  --neutro-fondo: #232b24;
  --banda: #1c211b;
}

* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--papel);
  color: var(--tinta);
  font-family: "IBM Plex Sans", "Helvetica Neue", Arial, sans-serif;
  font-size: 16px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}
.hoja { max-width: 62rem; margin: 0 auto; padding: 3.5rem 1.5rem 5rem; }
.pila { display: flex; flex-direction: column; }

.encabezado { border-bottom: 2px solid var(--tinta); padding-bottom: 1.5rem; gap: .5rem; }
.rotulo {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: .72rem; letter-spacing: .14em; text-transform: uppercase;
  color: var(--tinta-suave);
}
h1 {
  font-family: Newsreader, Georgia, serif;
  font-size: clamp(2rem, 4.5vw, 3rem); font-weight: 500; line-height: 1.1;
  margin: 0; text-wrap: balance; letter-spacing: -.01em;
}
.bajada { font-size: 1.05rem; color: var(--tinta-media); max-width: 46rem; margin: .4rem 0 0; }

.ficha {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
  gap: 1rem 2rem; margin-top: 1.5rem;
}
.ficha div { display: flex; flex-direction: column; gap: .15rem; }
.ficha dt, .ficha .clave {
  font-family: "IBM Plex Mono", monospace; font-size: .7rem;
  letter-spacing: .1em; text-transform: uppercase; color: var(--tinta-suave);
}
.ficha .valor { font-size: .95rem; color: var(--tinta); }

section { margin-top: 3.5rem; }
h2 {
  font-family: Newsreader, Georgia, serif; font-weight: 500;
  font-size: 1.6rem; margin: 0 0 .35rem; letter-spacing: -.01em;
}
h2 + .nota { margin-top: 0; }
.nota { color: var(--tinta-media); margin: 0 0 1.5rem; max-width: 44rem; font-size: .95rem; }

.cifras { display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); gap: 1px;
  background: var(--linea); border: 1px solid var(--linea); margin-top: 1.5rem; }
.cifra { background: var(--superficie); padding: 1.25rem 1.25rem 1.4rem; display: flex;
  flex-direction: column; gap: .3rem; }
.cifra .n {
  font-family: "IBM Plex Mono", monospace; font-variant-numeric: tabular-nums;
  font-size: 1.9rem; font-weight: 500; line-height: 1; letter-spacing: -.02em;
}
.cifra .n.destacado { color: var(--acento); }
.cifra .n.alerta { color: var(--critico); }
.cifra .et { font-size: .85rem; color: var(--tinta-suave); }

.envoltura { overflow-x: auto; border: 1px solid var(--linea); background: var(--superficie); }
table { width: 100%; border-collapse: collapse; font-size: .9rem; }
thead th {
  text-align: left; font-family: "IBM Plex Mono", monospace; font-weight: 500;
  font-size: .68rem; letter-spacing: .1em; text-transform: uppercase;
  color: var(--tinta-suave); padding: .8rem 1rem; border-bottom: 1px solid var(--linea);
  white-space: nowrap; background: var(--banda);
}
tbody td { padding: .75rem 1rem; border-bottom: 1px solid var(--linea-suave); vertical-align: top; }
tbody tr:last-child td { border-bottom: 0; }
td.num, th.num { text-align: right; font-family: "IBM Plex Mono", monospace;
  font-variant-numeric: tabular-nums; white-space: nowrap; }
td.orden { font-family: "IBM Plex Mono", monospace; font-weight: 500; white-space: nowrap; }

.marca { display: inline-flex; align-items: center; gap: .4rem; font-size: .75rem;
  font-family: "IBM Plex Mono", monospace; letter-spacing: .05em; text-transform: uppercase;
  padding: .2rem .55rem; border-radius: 2px; white-space: nowrap; }
.marca.alta { background: var(--critico-fondo); color: var(--critico); }
.marca.media { background: var(--atencion-fondo); color: var(--atencion); }
.marca.informativa { background: var(--neutro-fondo); color: var(--neutro); }

.casos { display: flex; flex-direction: column; gap: 1rem; margin-top: 1.5rem; }
.caso { background: var(--superficie); border: 1px solid var(--linea);
  border-left: 3px solid var(--critico); padding: 1.2rem 1.4rem; display: flex;
  flex-direction: column; gap: .6rem; }
.caso.media { border-left-color: var(--atencion); }
.caso .fila { display: flex; flex-wrap: wrap; align-items: baseline; gap: .6rem 1rem; }
.caso .id { font-family: "IBM Plex Mono", monospace; font-weight: 500; font-size: 1rem; }
.caso .id a { color: var(--acento); text-decoration: none; border-bottom: 1px solid transparent; }
.caso .id a:hover, .caso .id a:focus-visible { border-bottom-color: var(--acento); }
.caso .id a:focus-visible { outline: 2px solid var(--acento); outline-offset: 3px; }
.caso .meta { font-size: .82rem; color: var(--tinta-suave); }
.caso p { margin: 0; color: var(--tinta-media); font-size: .95rem; max-width: 52rem; }
.caso .datos { display: flex; flex-wrap: wrap; gap: 1.5rem; padding-top: .3rem; }
.caso .datos span { font-size: .8rem; color: var(--tinta-suave); }
.caso .datos b { display: block; font-family: "IBM Plex Mono", monospace;
  font-variant-numeric: tabular-nums; font-size: .95rem; font-weight: 500; color: var(--tinta); }

.descartes { border: 1px solid var(--linea); background: var(--acento-claro); padding: 1.4rem 1.6rem; }
.descartes ul { margin: .8rem 0 0; padding-left: 1.1rem; color: var(--tinta-media); font-size: .92rem; }
.descartes li { margin-bottom: .35rem; }
.descartes li:last-child { margin-bottom: 0; }

footer { margin-top: 4rem; padding-top: 1.5rem; border-top: 1px solid var(--linea);
  color: var(--tinta-suave); font-size: .82rem; }
footer p { max-width: 50rem; }
footer code { font-family: "IBM Plex Mono", monospace; font-size: .95em; }
@media (max-width: 40rem) { .hoja { padding: 2.5rem 1rem 3rem; } }
"""


def _pesos(valor) -> str:
    if valor is None:
        return "—"
    negativo = valor < 0
    texto = f"{abs(Decimal(valor)):,.0f}".replace(",", ".")
    return f"{'−' if negativo else ''}${texto}"


def _miles(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _e(texto) -> str:
    return html.escape(str(texto)) if texto is not None else ""


def _porcentaje(valor) -> str:
    return "—" if valor is None else f"{valor:.2f}".replace(".", ",") + " %"


def contenido(resultado, etiquetas: dict[str, str], verificaciones: list[str] | None = None,
              url_orden: str | None = None) -> str:
    r = resultado.resumen
    fotos = resultado.fotografias

    por_tipo: dict[str, list] = defaultdict(list)
    for f in fotos:
        for s in f.situaciones:
            por_tipo[s.tipo_interno].append((f, s))

    fecha = datetime.fromisoformat(resultado.fecha_analisis)
    periodos = sorted({f.periodo_aseguradora for f in fotos if f.periodo_aseguradora})
    rango = f"{periodos[0]} a {periodos[-1]}" if periodos else "sin periodo"

    p: list[str] = ['<div class="hoja">']

    # -- identificacion --
    p.append('<header class="pila encabezado">')
    p.append('<span class="rotulo">Auditoría financiera · CESER</span>')
    p.append("<h1>Órdenes que requieren revisión</h1>")
    p.append(f'<p class="bajada">Cruce de los archivos de repuestos y aseguradoras contra el '
             f'sistema TECH para el periodo {_e(rango)}. Cada situación señalada indica una '
             f'diferencia entre fuentes que conviene explicar, no necesariamente un error.</p>')
    p.append('<div class="ficha">')
    for clave, valor in (
        ("Fecha del análisis", fecha.strftime("%d/%m/%Y %H:%M")),
        ("Periodo cubierto", rango),
        ("Órdenes analizadas", _miles(r["ordenes_analizadas"])),
        ("Fuentes cruzadas", "Repuestos · Aseguradoras · TECH"),
    ):
        p.append(f'<div><span class="clave">{_e(clave)}</span><span class="valor">{_e(valor)}</span></div>')
    p.append("</div></header>")

    # -- cifras --
    p.append("<section>")
    p.append("<h2>El panorama</h2>")
    p.append('<p class="nota">De las órdenes analizadas, estas son las que el sistema pudo '
             "conciliar completamente y las que presentan alguna diferencia.</p>")
    p.append('<div class="cifras">')
    for numero, etiqueta, clase in (
        (_miles(r["ordenes_analizadas"]), "Órdenes analizadas", ""),
        (_miles(r["sin_novedades"]), "Sin novedades", "destacado"),
        (_miles(r["para_revisar"]), "Para revisar", "alerta"),
    ):
        p.append(f'<div class="cifra"><span class="n {clase}">{numero}</span>'
                 f'<span class="et">{etiqueta}</span></div>')
    p.append("</div>")
    p.append("</section>")

    # -- de donde sale cada cifra --
    grupos = (
        ("Con costo y con valor cobrado", "directa",
         "Las únicas donde se puede medir utilidad: hay repuesto comprado y hay cobro a la aseguradora."),
        ("Con costo, sin contraparte", "sin_aseguradora",
         "Órdenes con repuestos registrados que no aparecen en el archivo de aseguradoras. "
         "En su mayoría son SAMSUNG y SUPER WEGA, que no son negocio de aseguradora."),
        ("Con cobro, sin costo registrado", "sin_repuestos",
         "Órdenes facturadas a la aseguradora sin ningún repuesto asociado: diagnósticos, "
         "casos objetados y daños totales."),
    )
    p.append("<section>")
    p.append("<h2>De dónde sale cada cifra</h2>")
    p.append('<p class="nota">Las órdenes analizadas no forman un solo bloque. Sumar el costo '
             "de unas con el cobro de otras daría una cifra sin sentido, así que cada grupo "
             "se presenta por separado.</p>")
    p.append('<div class="envoltura"><table><thead><tr><th>Grupo</th>'
             '<th class="num">Órdenes</th><th class="num">Costo de repuestos</th>'
             '<th class="num">Cobrado a aseguradoras</th></tr></thead><tbody>')
    for etiqueta, tipo, _ in grupos:
        g = [f for f in fotos if f.tipo_coincidencia == tipo]
        costo_g = sum((f.costo_total_repuestos for f in g if f.costo_total_repuestos is not None), Decimal(0))
        cobro_g = sum((f.valor_aseguradora for f in g if f.valor_aseguradora is not None), Decimal(0))
        p.append(f"<tr><td>{etiqueta}</td>"
                 f'<td class="num">{_miles(len(g))}</td>'
                 f'<td class="num">{_pesos(costo_g) if costo_g else "—"}</td>'
                 f'<td class="num">{_pesos(cobro_g) if cobro_g else "—"}</td></tr>')
    p.append("</tbody></table></div>")
    for etiqueta, _, explicacion in grupos:
        p.append(f'<p class="nota" style="margin:.8rem 0 0"><strong>{etiqueta}.</strong> {explicacion}</p>')
    p.append("</section>")

    # -- utilidad en sus tres lecturas --
    # Las tres lecturas se calculan sobre EL MISMO conjunto de ordenes: las que
    # tienen costo, valor cobrado por repuestos y total registrado en el sistema.
    # Con conjuntos distintos los porcentajes no serian comparables entre si.
    comparables = [
        f for f in fotos
        if f.costo_total_repuestos is not None
        and f.valor_repuestos_reconocido is not None
        and f.base_servicio is not None
        and f.valor_total_tech is not None
    ]
    costo_c = sum((f.costo_total_repuestos for f in comparables), Decimal(0))
    cobrado_rep = sum((f.valor_repuestos_reconocido for f in comparables), Decimal(0))
    base_serv = sum((f.base_servicio for f in comparables), Decimal(0))
    total_iva = sum((f.valor_total_tech for f in comparables), Decimal(0))

    p.append("<section>")
    p.append("<h2>La utilidad, en tres lecturas</h2>")
    conciliadas = [f for f in fotos if f.tipo_coincidencia == "directa"]
    p.append(f'<p class="nota">Calculada sobre {len(comparables)} de las {len(conciliadas)} '
             "órdenes conciliadas: son las que tienen a la vez el costo del repuesto, el valor "
             "cobrado por repuestos y el total registrado en el sistema. Las demás quedan fuera "
             "porque su hoja no discrimina el valor de los repuestos, como ocurre con SURA y MOK.</p>")
    p.append('<p class="nota">Tres lecturas de la misma operación. La del repuesto compara lo '
             "que costó la pieza contra lo que se cobró por ella. La del servicio suma la mano "
             "de obra facturada. La tercera toma el total del servicio tal como queda registrado "
             "en el sistema, con IVA incluido, que es lo que se ve en la factura.</p>")
    p.append('<div class="envoltura"><table><thead><tr>'
             "<th>Nivel</th><th class=\"num\">Órdenes</th><th class=\"num\">Cobrado</th>"
             "<th class=\"num\">Costo del repuesto</th><th class=\"num\">Utilidad</th>"
             "<th class=\"num\">Margen</th></tr></thead><tbody>")
    for etiqueta, ingreso in (
        ("Del repuesto", cobrado_rep),
        ("Del servicio, sin IVA", base_serv),
        ("Del servicio, con IVA", total_iva),
    ):
        util = ingreso - costo_c
        margen = (util / ingreso * 100) if ingreso else None
        p.append(f"<tr><td>{etiqueta}</td><td class=\"num\">{len(comparables)}</td>"
                 f'<td class="num">{_pesos(ingreso)}</td><td class="num">{_pesos(costo_c)}</td>'
                 f'<td class="num">{_pesos(util)}</td><td class="num">{_porcentaje(margen)}</td></tr>')
    p.append("</tbody></table></div>")
    p.append('<p class="nota" style="margin-top:1.25rem"><strong>Cuál usar.</strong> '
             "Para juzgar rentabilidad, la del servicio sin IVA: el IVA se recauda para la DIAN "
             "y no queda en la empresa, y los fletes se cobran al costo. La lectura con IVA "
             "aparece más alta y es la que suele verse al mirar la factura, por eso se incluye: "
             "para que la diferencia entre las dos quede explicada y no se discuta sobre "
             "cifras distintas.</p>")
    facturado_comp = sum((f.valor_aseguradora for f in comparables
                          if f.valor_aseguradora is not None), Decimal(0))
    p.append(f'<p class="nota">A esas mismas {len(comparables)} órdenes se les facturó '
             f"{_pesos(facturado_comp)} en total, cifra que suma además los fletes.</p>")
    p.append("</section>")

    # -- situaciones --
    p.append("<section>")
    p.append("<h2>Situaciones encontradas</h2>")
    p.append('<p class="nota">Ordenadas por gravedad. El valor involucrado es lo que la '
             "aseguradora reconoce en esas órdenes, para dimensionar el alcance.</p>")
    p.append('<div class="envoltura"><table><thead><tr>'
             '<th>Situación</th><th>Gravedad</th><th class="num">Órdenes</th>'
             '<th class="num">Valor involucrado</th></tr></thead><tbody>')
    orden_gravedad = {"alta": 0, "media": 1, "informativa": 2}
    for tipo, items in sorted(
        por_tipo.items(),
        key=lambda x: (orden_gravedad.get(x[1][0][1].severidad, 3), -len(x[1])),
    ):
        severidad = items[0][1].severidad
        involucrado = sum((f.valor_aseguradora for f, _ in items if f.valor_aseguradora is not None), Decimal(0))
        p.append(f"<tr><td>{_e(etiquetas.get(tipo, tipo))}</td>"
                 f'<td><span class="marca {severidad}">{severidad}</span></td>'
                 f'<td class="num">{len(items)}</td>'
                 f'<td class="num">{_pesos(involucrado)}</td></tr>')
    p.append("</tbody></table></div></section>")

    # -- casos concretos --
    criticos = [(f, s) for tipo, items in por_tipo.items() for f, s in items if s.severidad == "alta"]
    if criticos:
        p.append("<section>")
        p.append("<h2>Casos que requieren una decisión</h2>")
        p.append('<p class="nota">Cada uno fue verificado contra las tres fuentes. '
                 "El texto explica qué se encontró.</p>")
        p.append('<div class="casos">')
        criticos.sort(key=lambda x: -(x[0].valor_aseguradora or Decimal(0)))
        for f, s in criticos[:20]:
            p.append('<div class="caso">')
            p.append('<div class="fila">')
            if url_orden:
                enlace = _e(url_orden.format(orden=f.orden_ceser))
                p.append(f'<span class="id"><a href="{enlace}" target="_blank" '
                         f'rel="noopener">Orden {_e(f.orden_ceser)}</a></span>')
            else:
                p.append(f'<span class="id">Orden {_e(f.orden_ceser)}</span>')
            partes = [f.aseguradora, f.estado_actual_tech,
                      f"factura {f.facturas}" if f.facturas else None]
            p.append(f'<span class="meta">{_e(" · ".join(x for x in partes if x))}</span>')
            p.append(f'<span class="marca alta">{_e(etiquetas.get(s.tipo_interno, s.tipo_interno))}</span>')
            p.append("</div>")
            p.append(f"<p>{_e(s.descripcion)}</p>")
            p.append('<div class="datos">')
            for etiqueta, valor in (
                ("Costo del repuesto, con IVA", _pesos(f.costo_total_repuestos)),
                ("Cobrado por repuestos, como esta en el archivo",
                 _pesos(f.valor_repuestos_reconocido)),
                ("Cobrado por repuestos, con el IVA que cobro el archivo",
                 _pesos(f.valor_repuestos_con_iva)),
                ("Mano de obra", _pesos(f.valor_mano_obra)),
                ("Utilidad del repuesto, sin IVA", f"{_pesos(f.utilidad_sobre_repuestos)}"
                                                   f" · {_porcentaje(f.margen_sobre_repuestos)}"),
                ("Utilidad del repuesto, comparable con el costo",
                 f"{_pesos(f.utilidad_sobre_repuestos_con_iva)}"
                 f" · {_porcentaje(f.margen_sobre_repuestos_con_iva)}"),
                ("Utilidad del servicio, sin IVA", f"{_pesos(f.utilidad_servicio)}"
                                                   f" · {_porcentaje(f.margen_servicio)}"),
                ("Utilidad del servicio, comparable con el costo",
                 f"{_pesos(f.utilidad_servicio_con_iva)}"
                 f" · {_porcentaje(f.margen_servicio_con_iva)}"),
                ("Total del servicio en el sistema", f"{_pesos(f.utilidad_servicio_sistema)}"
                                                     f" · {_porcentaje(f.margen_servicio_sistema)}"),
            ):
                p.append(f"<span>{etiqueta}<b>{valor}</b></span>")
            p.append("</div></div>")
        p.append("</div></section>")

    # -- verificaciones --
    if verificaciones:
        p.append("<section>")
        p.append("<h2>Qué se verificó antes de señalar</h2>")
        p.append('<p class="nota">Antes de marcar una orden, el análisis descarta las '
                 "explicaciones alternativas. Esto es lo que ya se comprobó.</p>")
        p.append('<div class="descartes"><span class="rotulo">Comprobaciones realizadas</span><ul>')
        for v in verificaciones:
            p.append(f"<li>{_e(v)}</li>")
        p.append("</ul></div></section>")

    # -- pie --
    archivos = ", ".join(a.nombre_archivo for a in resultado.archivos) or "archivos locales"
    p.append("<footer>")
    p.append(f"<p>Generado el {fecha.strftime('%d/%m/%Y a las %H:%M')} a partir de {_e(archivos)} "
             f"y de la base de datos de TECH consultada en modo lectura. "
             f"Se leyeron {_miles(r['lineas_repuestos'])} registros de repuestos y "
             f"{_miles(r['lineas_aseguradoras'])} de aseguradoras. "
             f"{_miles(r['descartes_repuestos'] + r['descartes_aseguradoras'])} filas quedaron fuera "
             "por no tener un número de orden válido y se listan aparte.</p>")
    p.append("<p>Cada número de orden enlaza a su ficha en TECH. El detalle completo, con la hoja "
             "y la fila exacta de donde salió cada cifra, está en el archivo de desglose que "
             "acompaña este informe.</p>")
    p.append("</footer>")
    p.append("</div>")
    return "\n".join(p)


def documento(resultado, etiquetas: dict[str, str], verificaciones: list[str] | None = None,
              titulo: str = "Órdenes que requieren revisión", url_orden: str | None = None) -> str:
    """Pagina completa, para abrir en el navegador o imprimir a PDF."""
    return (
        "<!doctype html>\n<html lang=\"es\">\n<head>\n"
        '<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_e(titulo)}</title>\n"
        f'<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        f'<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        f'<link rel="stylesheet" href="{TIPOGRAFIAS}">\n'
        f"<style>{CSS}</style>\n</head>\n<body>\n"
        + contenido(resultado, etiquetas, verificaciones, url_orden)
        + "\n</body>\n</html>\n"
    )


def generar(resultado, directorio: Path, etiquetas: dict[str, str],
            verificaciones: list[str] | None = None, sello: str | None = None,
            url_orden: str | None = None) -> Path:
    directorio.mkdir(parents=True, exist_ok=True)
    sello = sello or datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta = directorio / f"informe_{sello}.html"
    ruta.write_text(documento(resultado, etiquetas, verificaciones, url_orden=url_orden),
                    encoding="utf-8")
    logger.info("Informe visual generado: %s", ruta)
    return ruta
