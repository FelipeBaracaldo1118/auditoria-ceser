"""Lecturas de la base de auditoria para la interfaz.

Todo sale de la ultima corrida guardada; el estado de revision, en cambio, es
independiente de la corrida, para que lo ya revisado siga revisado manana.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from app.database.auditoria import (ESTADOS_REVISION, corridas, historial_revision, ordenes,
                                    revisiones, situaciones)

VEREDICTOS = {
    "cuadra": ("Cuadra", "ok"),
    "residuo": ("Queda un residuo", "warn"),
    "facturado_por_debajo": ("Facturado por debajo", "alert"),
    "sin_desglose": ("Sin desglosar", "plain"),
    "sin_contraparte": ("Sin contraparte", "plain"),
    "sin_repuestos": ("Sin repuestos", "plain"),
    "no_aplica": ("No aplica", "plain"),
}
NOMBRE_ESTADO = {"pendiente": "Pendiente", "verificada": "Verificada",
                 "en_gestion": "En gestión", "descartada": "Descartada"}


def ultima_corrida(con):
    return con.execute(select(corridas).order_by(corridas.c.id.desc()).limit(1)).mappings().first()


def resumen(con, corrida_id: int) -> dict:
    o = ordenes.c
    totales = con.execute(
        select(func.count().label("ordenes"),
               func.sum(func.coalesce(o.costo_repuestos_archivo, 0)).label("gastado"),
               func.sum(func.coalesce(o.valor_total_archivo_aseguradora, 0)).label("facturado"),
               func.sum(func.coalesce(o.iva_archivo_aseguradora, 0)).label("iva"),
               func.sum(func.coalesce(o.transporte_columna_archivo, 0)
                        + func.coalesce(o.transporte_archivo, 0)).label("fletes"))
        .where(o.corrida_id == corrida_id, o.valor_total_archivo_aseguradora.is_not(None),
               o.hoja_repuestos != "")).mappings().first()

    neto = Decimal(str(totales["facturado"] or 0)) - Decimal(str(totales["iva"] or 0)) - Decimal(str(totales["fletes"] or 0))
    gastado = Decimal(str(totales["gastado"] or 0))
    sin_contraparte = con.execute(
        select(func.count().label("n"), func.sum(func.coalesce(o.costo_repuestos_archivo, 0)).label("gastado"))
        .where(o.corrida_id == corrida_id, o.valor_total_archivo_aseguradora.is_(None),
               o.hoja_repuestos != "")).mappings().first()
    return {
        "ordenes_comparables": totales["ordenes"],
        "gastado": gastado, "facturado": Decimal(str(totales["facturado"] or 0)),
        "iva": Decimal(str(totales["iva"] or 0)), "fletes": Decimal(str(totales["fletes"] or 0)),
        "ingreso_neto": neto, "utilidad": neto - gastado,
        "margen": ((neto - gastado) / neto * 100).quantize(Decimal("0.01")) if neto else None,
        "sin_contraparte": sin_contraparte["n"],
        "gastado_sin_contraparte": Decimal(str(sin_contraparte["gastado"] or 0)),
    }


def conteo_situaciones(con, corrida_id: int) -> list[dict]:
    s = situaciones.c
    filas = con.execute(
        select(s.etiqueta, s.severidad, func.count().label("n"))
        .where(s.corrida_id == corrida_id).group_by(s.etiqueta, s.severidad)
        .order_by(func.count().desc())).mappings().all()
    return [dict(f) for f in filas]


def conteo_veredictos(con, corrida_id: int) -> dict[str, int]:
    o = ordenes.c
    filas = con.execute(select(o.veredicto_del_cruce, func.count().label("n"))
                        .where(o.corrida_id == corrida_id)
                        .group_by(o.veredicto_del_cruce)).all()
    return {v: n for v, n in filas}


def conteo_estados(con, corrida_id: int) -> dict[str, int]:
    """Cuantas ordenes de la corrida hay en cada estado de revision."""
    o, r = ordenes.c, revisiones.c
    filas = con.execute(
        select(func.coalesce(r.estado, "pendiente").label("estado"), func.count().label("n"))
        .select_from(ordenes.outerjoin(revisiones, o.orden_ceser == r.orden_ceser))
        .where(o.corrida_id == corrida_id)
        .group_by(func.coalesce(r.estado, "pendiente"))).all()
    conteo = {e: 0 for e in ESTADOS_REVISION}
    conteo.update({e: n for e, n in filas})
    return conteo


POR_PAGINA = 50


def listar_ordenes(con, corrida_id: int, veredicto: str = "todas", estado: str = "pendiente",
                   buscar: str = "", pagina: int = 1, por_pagina: int = POR_PAGINA,
                   desde: str = "", hasta: str = "",
                   direccion: str = "desc", hoja: str = "") -> tuple[list[dict], int]:
    """Una pagina de ordenes y el total que cumple los filtros.

    Se pagina porque una corrida trae cerca de 1.800 ordenes: pintarlas todas
    hace la pagina pesadisima y obliga a la gerente a buscar entre ellas.
    """
    o, r = ordenes.c, revisiones.c
    estado_col = func.coalesce(r.estado, "pendiente")
    origen = ordenes.outerjoin(revisiones, o.orden_ceser == r.orden_ceser)
    condiciones = [o.corrida_id == corrida_id]
    if veredicto != "todas":
        condiciones.append(o.veredicto_del_cruce == veredicto)
    if estado != "todas":
        condiciones.append(estado_col == estado)
    if buscar:
        patron = f"%{buscar}%"
        condiciones.append(o.orden_ceser.like(patron) | o.factura_proveedor.like(patron)
                           | o.factura_a_la_aseguradora.like(patron))
    # Por la fecha en que el proveedor facturo. Las fechas se guardan como
    # AAAA-MM-DD, asi que comparar como texto ordena igual que como fecha.
    if hoja:
        condiciones.append(o.hoja_repuestos.contains(hoja))
    if desde:
        condiciones.append(o.fecha_factura_proveedor >= desde)
    if hasta:
        condiciones.append(o.fecha_factura_proveedor <= hasta)

    total = con.execute(select(func.count()).select_from(origen).where(*condiciones)).scalar() or 0
    pagina = max(1, pagina)
    # Por defecto lo mas reciente primero, que es lo que se revisa.
    fecha = o.fecha_factura_proveedor
    criterio = fecha.asc() if direccion == "asc" else fecha.desc()
    filas = con.execute(
        select(ordenes, estado_col.label("estado_revision")).select_from(origen).where(*condiciones)
        .order_by(criterio, o.orden_ceser)
        .limit(por_pagina).offset((pagina - 1) * por_pagina)).mappings()
    return [dict(f) for f in filas], total


def situaciones_de(con, corrida_id: int, numeros: list[str]) -> dict[str, list[dict]]:
    if not numeros:
        return {}
    s = situaciones.c
    filas = con.execute(select(s.orden_ceser, s.etiqueta, s.severidad, s.descripcion)
                        .where(s.corrida_id == corrida_id, s.orden_ceser.in_(numeros))).mappings()
    por_orden: dict[str, list[dict]] = {}
    for f in filas:
        por_orden.setdefault(f["orden_ceser"], []).append(dict(f))
    return por_orden


def historial(con, limite: int = 200) -> list[dict]:
    h = historial_revision.c
    return [dict(f) for f in con.execute(
        select(historial_revision).order_by(h.id.desc()).limit(limite)).mappings()]


def rango_de_fechas(con, corrida_id: int) -> tuple[str | None, str | None]:
    """Primera y ultima fecha de factura de la corrida, para acotar el filtro."""
    o = ordenes.c
    fila = con.execute(select(func.min(o.fecha_factura_proveedor), func.max(o.fecha_factura_proveedor))
                       .where(o.corrida_id == corrida_id,
                              o.fecha_factura_proveedor.is_not(None),
                              o.fecha_factura_proveedor != "")).first()
    return (fila[0], fila[1]) if fila else (None, None)
