"""Ejecucion completa de una conciliacion y armado de sus resultados.

Produce una fotografia por orden (equivalente a `order_snapshots` de la
especificacion) mas las situaciones detectadas. Todavia sin MariaDB: los campos
que dependen de TECH (estado, valor registrado, pagos) quedan en None.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable

from app.database.catalogo import nombre_aseguradora
from app.drive.client import ArchivoDescargado
from app.ingestion.adapters import cobertura_de, Consolidado, consolidar_aseguradoras, consolidar_repuestos
from app.ingestion.workbook import LibroCrudo
from app.reconciliation.calculations import ResultadoFinanciero, calcular
from app.reconciliation.matching import BuscadorOrdenAsociada, OrdenConciliada, conciliar, sin_tech
from app.rules.situations import fijar_cobertura_aseguradoras, Situacion, evaluar, pesos, porcentaje

logger = logging.getLogger(__name__)


@dataclass
class FotografiaOrden:
    fecha_analisis: str
    orden_ceser: str
    orden_ceser_original: str
    orden_aseguradora: str | None
    hojas_repuestos: str
    hojas_aseguradora: str
    tipo_coincidencia: str
    estado_actual_tech: str | None
    existe_en_tech: bool
    fuente_tech: str | None            # "ordenes" | "productos_seguro" | None
    aseguradora: str | None
    nro_siniestro_tech: str | None
    facturas: str | None               # con la que le cobramos a la aseguradora
    facturas_proveedor: str | None     # con la que el proveedor nos cobro
    fecha_factura_proveedor: str | None
    fuente_repuestos: str | None
    fuente_aseguradora: str | None
    valor_transporte_cobrado: Decimal | None
    periodo_repuestos: str | None
    periodo_aseguradora: str | None
    cantidad_registros_repuestos: int
    costo_total_repuestos: Decimal | None
    valor_aseguradora: Decimal | None
    valor_repuestos_reconocido: Decimal | None   # segun el archivo de aseguradoras
    valor_reconocido_hoja: Decimal | None        # segun la hoja de repuestos (VALOR REPUESTO ASEGURADORA)
    valor_repuestos_con_iva: Decimal | None
    iva_sobre_repuestos: Decimal | None
    total_cobrado_hoja: Decimal | None
    mano_obra_hoja: Decimal | None
    iva_archivo: Decimal | None
    valor_repuestos_tech: Decimal | None
    valor_mano_obra_tech: Decimal | None
    iva_tech: Decimal | None
    valor_total_tech: Decimal | None
    valor_transporte: Decimal | None
    valor_ceser: Decimal | None
    total_pagado: Decimal | None
    saldo: Decimal | None
    valor_mano_obra: Decimal | None
    utilidad_esperada: Decimal | None
    margen_esperado: Decimal | None
    utilidad_sobre_repuestos: Decimal | None
    margen_sobre_repuestos: Decimal | None
    utilidad_sobre_repuestos_con_iva: Decimal | None
    margen_sobre_repuestos_con_iva: Decimal | None
    base_servicio: Decimal | None
    utilidad_servicio: Decimal | None
    margen_servicio: Decimal | None
    base_servicio_con_iva: Decimal | None
    utilidad_servicio_con_iva: Decimal | None
    margen_servicio_con_iva: Decimal | None
    utilidad_servicio_sistema: Decimal | None
    margen_servicio_sistema: Decimal | None
    utilidad_segun_pagos: Decimal | None
    situaciones: list[Situacion] = field(default_factory=list)

    @property
    def requiere_revision(self) -> bool:
        return bool(self.situaciones)


@dataclass
class DatosTech:
    """Todo lo que TECH sabe de una orden.

    `consultado` distingue dos situaciones que no son lo mismo: que se haya
    preguntado al sistema y la orden no este, o que no se haya preguntado. Sin
    esa distincion, una corrida con --sin-tech reportaba que las 1.524 ordenes
    no existen en el sistema, que es falso y ademas desacredita el informe.
    """

    orden: object | None = None            # OrdenTech
    seguro: object | None = None           # SeguroTech
    presupuesto: object | None = None      # PresupuestoSeguro
    pagos: object | None = None            # PagosTech
    consultado: bool = False

    @property
    def existe(self) -> bool:
        return any((self.orden, self.seguro, self.presupuesto))

    @property
    def fuente(self) -> str | None:
        if self.seguro or self.presupuesto:
            return "productos_seguro"
        return "ordenes" if self.orden else None

    @property
    def estado(self) -> str | None:
        return self.orden.estado if self.orden else None


@dataclass
class ResultadoAuditoria:
    fecha_analisis: str
    fotografias: list[FotografiaOrden]
    repuestos: Consolidado
    aseguradoras: Consolidado
    archivos: list[ArchivoDescargado]
    duracion_segundos: float = 0.0

    @property
    def resumen(self) -> dict:
        por_tipo: dict[str, int] = {}
        por_situacion: dict[str, int] = {}
        for foto in self.fotografias:
            por_tipo[foto.tipo_coincidencia] = por_tipo.get(foto.tipo_coincidencia, 0) + 1
            for s in foto.situaciones:
                por_situacion[s.tipo_interno] = por_situacion.get(s.tipo_interno, 0) + 1
        con_situaciones = sum(1 for f in self.fotografias if f.requiere_revision)
        pendientes_tech = sum(
            1 for f in self.fotografias
            if f.tipo_coincidencia == "sin_aseguradora" and not f.requiere_revision
        )
        # De donde salen las ordenes que quedaron sin contraparte, para poder
        # distinguir un problema real de una hoja que no es negocio de aseguradora.
        sin_contraparte: dict[str, int] = {}
        for foto in self.fotografias:
            if foto.tipo_coincidencia == "sin_aseguradora" and foto.requiere_revision:
                for hoja in foto.hojas_repuestos.split(", "):
                    sin_contraparte[hoja] = sin_contraparte.get(hoja, 0) + 1
        return {
            "ordenes_analizadas": len(self.fotografias),
            "sin_novedades": len(self.fotografias) - con_situaciones,
            "para_revisar": con_situaciones,
            "por_tipo_coincidencia": por_tipo,
            "por_situacion": por_situacion,
            "sin_aseguradora_en_periodo_por_hoja": sin_contraparte,
            "pendientes_de_revisar_con_tech": pendientes_tech,
            "lineas_repuestos": self.repuestos.total_lineas,
            "lineas_aseguradoras": self.aseguradoras.total_lineas,
            "descartes_repuestos": len(self.repuestos.descartes),
            "descartes_aseguradoras": len(self.aseguradoras.descartes),
        }


def _fotografiar(
    orden: OrdenConciliada, fin: ResultadoFinanciero, fecha: str, tech: "DatosTech | None" = None
) -> FotografiaOrden:
    conceptos = orden.aseguradora.valor_por_concepto if orden.aseguradora else {}
    tech = tech or DatosTech()
    presupuesto = tech.presupuesto
    return FotografiaOrden(
        fecha_analisis=fecha,
        orden_ceser=orden.orden_ceser,
        orden_ceser_original=" | ".join(orden.ordenes_originales),
        orden_aseguradora=", ".join(orden.aseguradora.ordenes_aseguradora) if orden.aseguradora else None,
        hojas_repuestos=", ".join(orden.repuestos.hojas) if orden.repuestos else "",
        hojas_aseguradora=", ".join(orden.aseguradora.hojas) if orden.aseguradora else "",
        tipo_coincidencia=orden.tipo_coincidencia,
        estado_actual_tech=tech.estado,
        existe_en_tech=tech.existe,
        fuente_tech=tech.fuente,
        aseguradora=nombre_aseguradora(tech.seguro.aseguradora_codigo) if tech.seguro else None,
        nro_siniestro_tech=tech.seguro.nro_siniestro if tech.seguro else None,
        facturas=", ".join(orden.aseguradora.facturas) if orden.aseguradora else None,
        facturas_proveedor=", ".join(orden.repuestos.facturas_proveedor) if orden.repuestos else None,
        fecha_factura_proveedor=orden.repuestos.fecha_factura if orden.repuestos else None,
        fuente_repuestos="; ".join(orden.repuestos.fuentes) if orden.repuestos else None,
        fuente_aseguradora="; ".join(orden.aseguradora.fuentes) if orden.aseguradora else None,
        valor_transporte_cobrado=(orden.aseguradora.valor_transporte_cobrado
                                  if orden.aseguradora else None),
        periodo_repuestos=orden.repuestos.periodo if orden.repuestos else None,
        periodo_aseguradora=orden.aseguradora.periodo if orden.aseguradora else None,
        cantidad_registros_repuestos=len(orden.repuestos.lineas) if orden.repuestos else 0,
        costo_total_repuestos=fin.costo_repuestos,
        valor_aseguradora=fin.valor_aseguradora,
        valor_repuestos_reconocido=fin.valor_repuestos_reconocido,
        valor_reconocido_hoja=(orden.repuestos.valor_reconocido_repuestos
                               if orden.repuestos else None),
        valor_repuestos_con_iva=fin.valor_repuestos_con_iva,
        iva_sobre_repuestos=(orden.aseguradora.iva_sobre_repuestos if orden.aseguradora else None),
        total_cobrado_hoja=orden.repuestos.total_cobrado if orden.repuestos else None,
        mano_obra_hoja=orden.repuestos.mano_obra_cobrada if orden.repuestos else None,
        iva_archivo=orden.aseguradora.valor_iva if orden.aseguradora else None,
        valor_repuestos_tech=presupuesto.valor_repuestos if presupuesto else None,
        valor_mano_obra_tech=presupuesto.valor_mano_obra if presupuesto else None,
        iva_tech=presupuesto.iva if presupuesto else None,
        valor_total_tech=presupuesto.valor_total if presupuesto else None,
        valor_transporte=conceptos.get("transporte"),
        valor_ceser=tech.orden.valor_fac if tech.orden else None,
        total_pagado=fin.total_pagado,
        saldo=fin.saldo,
        valor_mano_obra=fin.valor_mano_obra,
        utilidad_esperada=fin.utilidad_esperada,
        margen_esperado=fin.margen_esperado,
        utilidad_sobre_repuestos=fin.utilidad_sobre_repuestos,
        margen_sobre_repuestos=fin.margen_sobre_repuestos,
        utilidad_sobre_repuestos_con_iva=fin.utilidad_sobre_repuestos_con_iva,
        margen_sobre_repuestos_con_iva=fin.margen_sobre_repuestos_con_iva,
        base_servicio=fin.base_servicio,
        utilidad_servicio=fin.utilidad_servicio,
        margen_servicio=fin.margen_servicio,
        base_servicio_con_iva=fin.base_servicio_con_iva,
        utilidad_servicio_con_iva=fin.utilidad_servicio_con_iva,
        margen_servicio_con_iva=fin.margen_servicio_con_iva,
        utilidad_servicio_sistema=fin.utilidad_servicio_sistema,
        margen_servicio_sistema=fin.margen_servicio_sistema,
        utilidad_segun_pagos=fin.utilidad_segun_pagos,
        situaciones=evaluar(orden, fin, tech),
    )


def _consultar_tech(cliente, numeros: list[str], desde_pagos: str | None) -> dict[str, DatosTech]:
    """Una consulta por lote y por tabla, todas contra claves indexadas."""
    ordenes = cliente.obtener_ordenes(numeros)
    seguros = cliente.obtener_seguro(numeros)
    presupuestos = cliente.obtener_presupuesto_seguro(numeros)
    pagos = cliente.obtener_pagos(numeros, desde=desde_pagos)

    datos: dict[str, DatosTech] = {}
    for numero in numeros:
        datos[numero] = DatosTech(
            orden=ordenes.get(numero),
            seguro=seguros.get(numero),
            presupuesto=presupuestos.get(numero),
            pagos=pagos.get(numero),
            consultado=True,
        )
    return datos


def ejecutar_auditoria(
    libro_repuestos: LibroCrudo,
    libro_aseguradoras: LibroCrudo,
    archivos: list[ArchivoDescargado],
    buscar_en_tech: BuscadorOrdenAsociada = sin_tech,
    cliente_tech=None,
    desde_pagos: str | None = "2025-01-01",
) -> ResultadoAuditoria:
    inicio = datetime.now()
    fecha = inicio.isoformat(timespec="seconds")

    aseguradoras = consolidar_aseguradoras(libro_aseguradoras)

    periodos = sorted({l.periodo for l in aseguradoras.lineas if l.periodo})
    if periodos:
        fijar_cobertura_aseguradoras(periodos[0], periodos[-1])
        logger.info("El archivo de aseguradoras cubre de %s a %s", periodos[0], periodos[-1])

    # El alcance de las hojas de repuestos depende de lo que cubra el archivo de
    # aseguradoras, por eso se consolidan despues.
    repuestos = consolidar_repuestos(libro_repuestos, cobertura=cobertura_de(aseguradoras))

    conciliadas = conciliar(repuestos, aseguradoras, buscar_en_tech)

    datos_tech: dict[str, DatosTech] = {}
    if cliente_tech is not None:
        numeros = [c.orden_ceser for c in conciliadas]
        logger.info("Consultando TECH para %s ordenes", len(numeros))
        datos_tech = _consultar_tech(cliente_tech, numeros, desde_pagos)

    fotografias = []
    for orden in conciliadas:
        tech = datos_tech.get(orden.orden_ceser)
        valor_repuestos = orden.aseguradora.valor_repuestos if orden.aseguradora else None
        # La mano de obra viene del archivo; si la hoja no la trae, se toma del
        # presupuesto del sistema, que la registra como una linea aparte.
        mano_obra = orden.aseguradora.valor_mano_obra if orden.aseguradora else None
        if mano_obra is None and tech is not None and tech.presupuesto is not None:
            mano_obra = tech.presupuesto.valor_mano_obra
        # Segunda lectura: el valor reconocido puesto en los mismos terminos que
        # el costo, que si trae IVA. Es None cuando la hoja no discrimina IVA.
        repuestos_con_iva = orden.aseguradora.valor_repuestos_con_iva if orden.aseguradora else None
        mano_obra_con_iva = orden.aseguradora.valor_mano_obra_con_iva if orden.aseguradora else None
        fin = calcular(
            orden.costo_repuestos,
            orden.valor_aseguradora,
            valor_total_tech=tech.orden.valor_fac if tech and tech.orden else None,
            total_pagado=tech.pagos.total_pagado if tech and tech.pagos else None,
            valor_repuestos_reconocido=valor_repuestos,
            valor_mano_obra=mano_obra,
            total_servicio_sistema=(tech.presupuesto.valor_total
                                    if tech is not None and tech.presupuesto is not None else None),
            valor_repuestos_con_iva=repuestos_con_iva,
            valor_mano_obra_con_iva=mano_obra_con_iva,
        )
        fotografias.append(_fotografiar(orden, fin, fecha, tech))

    duracion = (datetime.now() - inicio).total_seconds()
    logger.info("Auditoria completada en %.2fs sobre %s ordenes", duracion, len(fotografias))
    return ResultadoAuditoria(fecha, fotografias, repuestos, aseguradoras, archivos, duracion)


def _num(valor: Decimal | None) -> str:
    return "" if valor is None else f"{valor:.2f}"


ETIQUETAS_SITUACION = {
    "LOW_MARGIN": "Margen menor al minimo",
    "COST_ABOVE_REVENUE": "Costo superior al valor reconocido",
    "ORDER_NOT_FOUND": "Orden sin contraparte de aseguradora",
    "COST_NOT_FOUND": "Orden facturada sin repuestos registrados",
    "VALUE_IN_MULTIPLE_SHEETS": "Facturada en mas de una hoja",
    "MISSING_COST_DATA": "Registros de repuestos sin costo",
    "VALUE_MISMATCH": "Valor cobrado inicialmente distinto al registrado",
    "TECH_VALUE_MISMATCH": "Cobrado por debajo del costo presupuestado",
    "SERVICE_TOTAL_MISMATCH": "El total del servicio no cuadra con lo facturado",
    "NO_PROFIT": "Cobrado sin utilidad sobre el presupuesto",
    "BUDGET_NOT_BILLED": "Repuestos aprobados que no se cobraron",
    "BUDGET_NOT_EXECUTED": "Presupuesto no ejecutado (orden fuera del flujo normal)",
    "ORDER_NOT_IN_TECH": "Orden que no existe en el sistema",
    "CASE_MISMATCH": "Caso de aseguradora distinto al del sistema",
    "PAYMENT_DIFFERENCE": "Saldo pendiente de pago",
    "BILLED_BELOW_CHARGED": "Facturado a la aseguradora por debajo de lo cobrado",
    "BILLED_ABOVE_CHARGED": "Facturado por encima de lo cobrado, sin explicar",
    "BILLING_NOT_BROKEN_DOWN": "Diferencia que la hoja no permite desglosar",
    "DUPLICATE_IN_SHEET": "Orden repetida en la hoja de repuestos",
    "BILLED_BEFORE_PURCHASE": "El proveedor nos cobro despues de facturarle a la aseguradora",
}


def etiqueta(tipo: str) -> str:
    return ETIQUETAS_SITUACION.get(tipo, tipo)


def veredicto_del_cruce(f: FotografiaOrden) -> str:
    """Resultado del flujo de revision: total cobrado en la hoja contra el total del archivo.

    Es el mismo agrupamiento que usa la vista de revision: cuadra, residuo,
    facturado por debajo, sin desglose posible, o sin una de las dos partes.
    """
    tipos = {s.tipo_interno for s in f.situaciones}
    if not f.hojas_repuestos:
        return "sin_repuestos"
    if f.valor_aseguradora is None:
        return "sin_contraparte"
    if f.total_cobrado_hoja is None:
        return "no_aplica"          # hojas sin TOTAL COBRADO: el flujo no se define para ellas
    if "BILLED_BELOW_CHARGED" in tipos:
        return "facturado_por_debajo"
    if "BILLED_ABOVE_CHARGED" in tipos:
        return "residuo"
    if "BILLING_NOT_BROKEN_DOWN" in tipos:
        return "sin_desglose"
    return "cuadra"


def _esperado_del_cruce(f: FotografiaOrden) -> Decimal | None:
    """TOTAL COBRADO + IVA + fletes: lo que el archivo deberia facturar si todo cuadra."""
    if f.total_cobrado_hoja is None or f.valor_aseguradora is None:
        return None
    return (f.total_cobrado_hoja + (f.iva_archivo or Decimal(0))
            + (f.valor_transporte_cobrado or Decimal(0)) + (f.valor_transporte or Decimal(0)))


def _residuo_del_cruce(f: FotografiaOrden) -> Decimal | None:
    esperado = _esperado_del_cruce(f)
    return None if esperado is None else f.valor_aseguradora - esperado


# Una sola definicion de las columnas por orden. Alimenta el CSV de precios y la
# base de auditoria, de modo que los dos nunca digan cosas distintas.
COLUMNAS_ORDEN: list[tuple[str, Callable[[FotografiaOrden], object]]] = [
    ("fecha_analisis", lambda f: f.fecha_analisis),
    ("orden_ceser", lambda f: f.orden_ceser),
    ("aseguradora", lambda f: f.aseguradora),
    ("factura_proveedor", lambda f: f.facturas_proveedor),
    ("fecha_factura_proveedor", lambda f: f.fecha_factura_proveedor),
    ("factura_a_la_aseguradora", lambda f: f.facturas),
    ("mes_facturado_a_la_aseguradora", lambda f: f.periodo_aseguradora),
    ("caso_aseguradora_archivo", lambda f: f.orden_aseguradora),
    ("siniestro_sistema", lambda f: f.nro_siniestro_tech),
    ("estado_sistema", lambda f: f.estado_actual_tech),
    ("existe_en_sistema", lambda f: f.existe_en_tech),
    ("fuente_sistema", lambda f: f.fuente_tech),
    ("hoja_repuestos", lambda f: f.hojas_repuestos),
    ("hoja_aseguradora", lambda f: f.hojas_aseguradora),
    ("costo_repuestos_archivo", lambda f: f.costo_total_repuestos),
    ("cantidad_registros_repuestos", lambda f: f.cantidad_registros_repuestos),
    ("repuestos_cobrados_hoja_repuestos", lambda f: f.valor_reconocido_hoja),
    ("repuestos_reconocidos_archivo", lambda f: f.valor_repuestos_reconocido),
    ("iva_sobre_repuestos", lambda f: f.iva_sobre_repuestos),
    ("repuestos_reconocidos_con_iva", lambda f: f.valor_repuestos_con_iva),
    ("total_cobrado_hoja_repuestos", lambda f: f.total_cobrado_hoja),
    ("mano_obra_hoja_repuestos", lambda f: f.mano_obra_hoja),
    ("iva_archivo_aseguradora", lambda f: f.iva_archivo),
    ("repuestos_presupuestados_sistema", lambda f: f.valor_repuestos_tech),
    ("mano_obra_sistema", lambda f: f.valor_mano_obra_tech),
    ("iva_sistema", lambda f: f.iva_tech),
    ("valor_total_servicio_sistema", lambda f: f.valor_total_tech),
    ("mano_obra_cobrada", lambda f: f.valor_mano_obra),
    ("valor_total_archivo_aseguradora", lambda f: f.valor_aseguradora),
    ("transporte_archivo", lambda f: f.valor_transporte),
    ("valor_facturado_sistema", lambda f: f.valor_ceser),
    ("total_pagado", lambda f: f.total_pagado),
    ("saldo", lambda f: f.saldo),
    ("utilidad_del_repuesto", lambda f: f.utilidad_sobre_repuestos),
    ("margen_del_repuesto", lambda f: f.margen_sobre_repuestos),
    ("utilidad_del_repuesto_con_iva", lambda f: f.utilidad_sobre_repuestos_con_iva),
    ("margen_del_repuesto_con_iva", lambda f: f.margen_sobre_repuestos_con_iva),
    ("base_del_servicio", lambda f: f.base_servicio),
    ("utilidad_del_servicio", lambda f: f.utilidad_servicio),
    ("margen_del_servicio", lambda f: f.margen_servicio),
    ("base_del_servicio_con_iva", lambda f: f.base_servicio_con_iva),
    ("utilidad_del_servicio_con_iva", lambda f: f.utilidad_servicio_con_iva),
    ("margen_del_servicio_con_iva", lambda f: f.margen_servicio_con_iva),
    ("utilidad_del_servicio_total_sistema", lambda f: f.utilidad_servicio_sistema),
    ("margen_del_servicio_total_sistema", lambda f: f.margen_servicio_sistema),
    ("utilidad_sobre_total_facturado", lambda f: f.utilidad_esperada),
    ("margen_sobre_total_facturado", lambda f: f.margen_esperado),
    ("requiere_revision", lambda f: f.requiere_revision),
    # agregadas el 13/09/2026: la vista de revision las calculaba por su cuenta
    ("transporte_columna_archivo", lambda f: f.valor_transporte_cobrado),
    ("esperado_del_cruce", _esperado_del_cruce),
    ("residuo_del_cruce", _residuo_del_cruce),
    ("veredicto_del_cruce", veredicto_del_cruce),
    # Donde esta la orden en cada Excel, para poder ir a verificarla
    ("ubicacion_en_repuestos", lambda f: f.fuente_repuestos),
    ("ubicacion_en_aseguradoras", lambda f: f.fuente_aseguradora),
]


def _celda_csv(valor) -> object:
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "si" if valor else "no"
    if isinstance(valor, Decimal):
        return _num(valor)
    return valor


def _reporte_precios(resultado: ResultadoAuditoria, ruta: Path) -> None:
    """Reporte general: todos los precios de cada orden, por fuente."""
    with open(ruta, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([nombre for nombre, _ in COLUMNAS_ORDEN])
        for f in resultado.fotografias:
            w.writerow([_celda_csv(extraer(f)) for _, extraer in COLUMNAS_ORDEN])


def _reporte_anomalias_detalle(resultado: ResultadoAuditoria, ruta: Path) -> None:
    """Una fila por situacion encontrada, con su explicacion en texto claro."""
    with open(ruta, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "fecha_analisis", "orden_ceser", "aseguradora",
            "factura_proveedor", "fecha_factura_proveedor",
            "factura_a_la_aseguradora", "mes_facturado_a_la_aseguradora", "estado_sistema",
            "situacion", "severidad", "explicacion",
            "costo_repuestos", "repuestos_reconocidos_archivo",
            "repuestos_presupuestados_sistema", "total_cobrado_hoja_repuestos",
            "valor_total_archivo", "transporte_archivo", "iva_archivo",
            "repuestos_reconocidos_con_iva", "margen_sobre_repuestos",
            "margen_sobre_repuestos_con_iva", "hoja_repuestos", "hoja_aseguradora",
            "estado_auditoria", "responsable", "comentario",
        ])
        for f in resultado.fotografias:
            for s in f.situaciones:
                w.writerow([
                    f.fecha_analisis, f.orden_ceser, f.aseguradora or "",
                    f.facturas_proveedor or "", f.fecha_factura_proveedor or "",
                    f.facturas or "", f.periodo_aseguradora or "",
                    f.estado_actual_tech or "", etiqueta(s.tipo_interno), s.severidad,
                    s.descripcion, _num(f.costo_total_repuestos),
                    _num(f.valor_repuestos_reconocido), _num(f.valor_repuestos_tech),
                    _num(f.total_cobrado_hoja), _num(f.valor_aseguradora),
                    _num(f.valor_transporte_cobrado), _num(f.iva_archivo),
                    _num(f.valor_repuestos_con_iva), _num(f.margen_sobre_repuestos),
                    _num(f.margen_sobre_repuestos_con_iva),
                    f.hojas_repuestos, f.hojas_aseguradora,
                    "Pendiente por revisar", "", "",   # columnas para el seguimiento manual
                ])


def _fmt(valor: Decimal | None) -> str:
    if valor is None:
        return "sin dato"
    return "$" + f"{valor:,.0f}".replace(",", ".")


def _reporte_anomalias_resumen(resultado: ResultadoAuditoria, ruta: Path) -> None:
    """Resumen general de anomalias, legible por una persona no tecnica."""
    r = resultado.resumen
    por_tipo: dict[str, list[FotografiaOrden]] = {}
    for f in resultado.fotografias:
        for s in f.situaciones:
            por_tipo.setdefault(s.tipo_interno, []).append(f)

    out: list[str] = ["# Situaciones para revisar", ""]
    out.append(f"Analisis del {resultado.fecha_analisis}")
    out.append("")
    out.append(f"- Ordenes analizadas: **{r['ordenes_analizadas']:,}**".replace(",", "."))
    out.append(f"- Sin novedades: **{r['sin_novedades']:,}**".replace(",", "."))
    out.append(f"- Para revisar: **{r['para_revisar']:,}**".replace(",", "."))
    out.append("")

    costo = sum((f.costo_total_repuestos for f in resultado.fotografias
                 if f.costo_total_repuestos is not None), Decimal(0))
    valor = sum((f.valor_aseguradora for f in resultado.fotografias
                 if f.valor_aseguradora is not None), Decimal(0))
    out.append("## Totales del periodo analizado")
    out.append("")
    out.append(f"| Concepto | Valor |")
    out.append("|---|---|")
    out.append(f"| Costo de repuestos | {_fmt(costo)} |")
    out.append(f"| Valor reconocido por aseguradoras | {_fmt(valor)} |")
    out.append(f"| Diferencia | {_fmt(valor - costo)} |")
    out.append("")

    out.append("## Situaciones encontradas")
    out.append("")
    out.append("| Situacion | Ordenes | Valor involucrado |")
    out.append("|---|---|---|")
    for tipo, fotos in sorted(por_tipo.items(), key=lambda x: -len(x[1])):
        involucrado = sum((f.valor_aseguradora for f in fotos if f.valor_aseguradora is not None), Decimal(0))
        out.append(f"| {etiqueta(tipo)} | {len(fotos)} | {_fmt(involucrado)} |")
    out.append("")

    criticas = [f for f in resultado.fotografias
                if any(s.severidad == "alta" for s in f.situaciones)]
    if criticas:
        out.append("## Lo mas urgente")
        out.append("")
        criticas.sort(key=lambda f: (f.margen_sobre_repuestos is None, f.margen_sobre_repuestos))
        for f in criticas[:25]:
            out.append(f"**Orden {f.orden_ceser}** — {f.aseguradora or 'sin aseguradora'}"
                       f"{' — ' + f.estado_actual_tech if f.estado_actual_tech else ''}")
            for s in f.situaciones:
                if s.severidad == "alta":
                    out.append(f"- {s.descripcion}")
            out.append("")

    ruta.write_text("\n".join(out), encoding="utf-8")


def exportar(resultado: ResultadoAuditoria, directorio: Path, sello: str | None = None) -> dict[str, Path]:
    directorio.mkdir(parents=True, exist_ok=True)
    sello = sello or datetime.now().strftime("%Y%m%d_%H%M%S")

    rutas = {
        "precios": directorio / f"precios_{sello}.csv",
        "anomalias_resumen": directorio / f"anomalias_resumen_{sello}.md",
        "anomalias_detalle": directorio / f"anomalias_detalle_{sello}.csv",
        "descartes": directorio / f"descartes_{sello}.csv",
        "resumen": directorio / f"resumen_ejecucion_{sello}.json",
    }

    _reporte_precios(resultado, rutas["precios"])
    _reporte_anomalias_detalle(resultado, rutas["anomalias_detalle"])
    _reporte_anomalias_resumen(resultado, rutas["anomalias_resumen"])

    with open(rutas["descartes"], "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["archivo", "hoja", "fila", "orden_original", "motivo"])
        for d in resultado.repuestos.descartes + resultado.aseguradoras.descartes:
            w.writerow([d["archivo"], d["hoja"], d["fila"], d["orden_original"] or "", d["motivo"]])

    rutas["resumen"].write_text(
        json.dumps(
            {
                "fecha_analisis": resultado.fecha_analisis,
                "duracion_segundos": resultado.duracion_segundos,
                "archivos": [a.as_dict() for a in resultado.archivos],
                "hojas_repuestos_incluidas": resultado.repuestos.hojas_incluidas,
                "hojas_repuestos_excluidas": [
                    {"hoja": h, "motivo": m} for h, m in resultado.repuestos.hojas_excluidas
                ],
                "hojas_aseguradoras_incluidas": resultado.aseguradoras.hojas_incluidas,
                "resumen": resultado.resumen,
            },
            indent=2, ensure_ascii=False, default=str,
        ),
        encoding="utf-8",
    )
    return rutas
