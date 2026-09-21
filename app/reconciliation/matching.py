"""Relacion de ordenes entre repuestos y aseguradoras.

Jerarquia definida en la especificacion:
  1. coincidencia directa por numero de orden normalizado;
  2. si no hay, consultar TECH para obtener la orden asociada (pendiente: MVP 2);
  3. si tampoco, informar que no se encontro.

El paso 2 esta aislado detras de `BuscadorOrdenAsociada` para poder conectarlo
a MariaDB sin tocar el resto del motor.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from app.ingestion.adapters import Consolidado, LineaAseguradora, LineaRepuesto
from app.normalization.money import sumar

logger = logging.getLogger(__name__)

# Tasa general de IVA. No se aplica a ciegas: se usa para RECONOCER cual de los
# dos patrones de facturacion sigue cada fila del archivo, y solo se reparte el
# IVA cuando la fila encaja en uno de ellos.
TASA_IVA = Decimal("0.19")

# Al reconocer el patron se comparan totales ya redondeados por el propio Excel.
TOLERANCIA_IVA = Decimal("2")


class BuscadorOrdenAsociada(Protocol):
    """Dada una orden CESER, devuelve la orden de aseguradora asociada en TECH."""

    def __call__(self, orden_ceser: str) -> str | None: ...


def sin_tech(orden_ceser: str) -> None:
    """Implementacion por defecto: todavia no hay conexion a MariaDB."""
    return None


def iva_sobre_repuestos(linea: LineaAseguradora) -> Decimal | None:
    """IVA que el archivo le cobro a la aseguradora por la porcion de repuestos.

    Hace falta porque las dos fuentes no hablan en los mismos terminos: el costo
    del proveedor viene con IVA y el valor reconocido por repuestos viene sin el.
    Compararlos de frente subestima el margen.

    El archivo no trae el IVA discriminado por concepto, solo el total, asi que
    se deduce del patron de la fila. Verificado sobre las 483 filas de las tres
    hojas que discriminan IVA (25/08/2026), existen exactamente dos:

      - el IVA es el 19 % de toda la base gravable -> los repuestos van gravados
        (FALABELLA 327, FLAMINGO 107, VIDA TRANQUI 33);
      - el IVA es el 19 % de la base SIN los repuestos -> solo se grava la mano
        de obra y los repuestos van sin IVA (FLAMINGO 16, todas de repuestos de
        alto valor).

    La base gravable se deduce del propio archivo como total - IVA - fletes, sin
    necesidad de la columna de diagnostico. Si una fila no encaja en ninguno de
    los dos patrones se devuelve None: no se reparte un IVA que no se entiende.
    """
    if linea.valor_iva is None or not linea.valor_iva.hay_dato:
        return None
    if linea.valor_repuestos is None or not linea.valor_repuestos.hay_dato:
        return None
    if not linea.valor.hay_dato:
        return None

    transporte = Decimal(0)
    if linea.valor_transporte is not None and linea.valor_transporte.hay_dato:
        transporte = linea.valor_transporte.valor

    iva = linea.valor_iva.valor
    repuestos = linea.valor_repuestos.valor
    base_gravable = linea.valor.valor - iva - transporte
    if base_gravable <= 0:
        return None

    if abs(iva - base_gravable * TASA_IVA) <= TOLERANCIA_IVA:
        return repuestos * TASA_IVA
    if abs(iva - (base_gravable - repuestos) * TASA_IVA) <= TOLERANCIA_IVA:
        return Decimal(0)
    logger.warning(
        "Hoja '%s' fila %s: el IVA de %s no corresponde ni a la base completa ni a "
        "la base sin repuestos; la orden queda sin lectura con IVA",
        linea.hoja_origen, linea.fila, iva,
    )
    return None


@dataclass
class GrupoRepuestos:
    orden: str
    lineas: list[LineaRepuesto]

    @property
    def hojas(self) -> list[str]:
        return sorted({l.hoja_origen for l in self.lineas})

    @property
    def costo_total(self) -> Decimal | None:
        return sumar([l.costo for l in self.lineas])[0]

    @property
    def lineas_sin_costo(self) -> int:
        return sum(1 for l in self.lineas if not l.costo.hay_dato)

    @property
    def periodo(self) -> str | None:
        fechas = [l.fecha for l in self.lineas if l.fecha is not None]
        return max(fechas).strftime("%Y-%m") if fechas else None

    @property
    def fuentes(self) -> list[str]:
        """De donde salio cada linea, para poder ubicarla en el archivo."""
        return [f"{l.hoja_origen.strip()} fila {l.fila}" for l in self.lineas]

    @property
    def facturas_proveedor(self) -> list[str]:
        return sorted({l.factura for l in self.lineas if l.factura})

    @property
    def fecha_factura(self) -> str | None:
        """Fecha con la que el proveedor nos cobro, para trazar contra su factura."""
        fechas = [l.fecha for l in self.lineas if l.fecha is not None]
        return max(fechas).strftime("%Y-%m-%d") if fechas else None

    @property
    def espera_aseguradora(self) -> bool:
        """False si todas sus hojas resuelven la contraparte contra TECH."""
        return any(l.espera_aseguradora for l in self.lineas)

    @property
    def valor_reconocido_repuestos(self) -> Decimal | None:
        """Lo que la hoja de repuestos dice que la aseguradora reconoce por partes."""
        montos = [l.valor_reconocido for l in self.lineas if l.valor_reconocido is not None]
        return sumar(montos)[0] if montos else None

    @property
    def mano_obra_cobrada(self) -> Decimal | None:
        """Mano de obra segun la hoja de repuestos (columna MANO DE OBRA)."""
        montos = [l.valor_mano_obra for l in self.lineas if l.valor_mano_obra is not None]
        return sumar(montos)[0] if montos else None

    @property
    def total_cobrado(self) -> Decimal | None:
        """Columna TOTAL COBRADO: repuesto + mano de obra, sin IVA ni fletes.

        Es el punto de partida del flujo de revision definido por el area: se
        compara contra el total del archivo de aseguradoras.
        """
        montos = [l.total_cobrado for l in self.lineas if l.total_cobrado is not None]
        return sumar(montos)[0] if montos else None


@dataclass
class GrupoAseguradora:
    orden: str
    lineas: list[LineaAseguradora]

    @property
    def hojas(self) -> list[str]:
        return sorted({l.hoja_origen for l in self.lineas})

    @property
    def valor_total(self) -> Decimal | None:
        return sumar([l.valor for l in self.lineas])[0]

    @property
    def valor_por_concepto(self) -> dict[str, Decimal]:
        acumulado: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
        for linea in self.lineas:
            if linea.valor.hay_dato:
                acumulado[linea.concepto] += linea.valor.valor
        return dict(acumulado)

    @property
    def espera_repuestos(self) -> bool:
        """False si todas sus hojas resuelven el costo contra TECH."""
        return any(l.espera_repuestos for l in self.lineas)

    @property
    def valor_repuestos(self) -> Decimal | None:
        """Porcion del valor reconocido que corresponde a repuestos, si la hoja la discrimina."""
        montos = [l.valor_repuestos for l in self.lineas if l.valor_repuestos is not None]
        return sumar(montos)[0] if montos else None

    @property
    def fuentes(self) -> list[str]:
        return [f"{l.hoja_origen.strip()} fila {l.fila}" for l in self.lineas]

    @property
    def valor_mano_obra(self) -> Decimal | None:
        montos = [l.valor_mano_obra for l in self.lineas if l.valor_mano_obra is not None]
        return sumar(montos)[0] if montos else None

    @property
    def valor_transporte_cobrado(self) -> Decimal | None:
        montos = [l.valor_transporte for l in self.lineas if l.valor_transporte is not None]
        return sumar(montos)[0] if montos else None

    @property
    def valor_iva(self) -> Decimal | None:
        """IVA discriminado por la propia hoja de la aseguradora."""
        montos = [l.valor_iva for l in self.lineas if l.valor_iva is not None]
        return sumar(montos)[0] if montos else None

    @property
    def iva_sobre_repuestos(self) -> Decimal | None:
        """IVA cobrado por la porcion de repuestos. None si alguna linea no se entiende."""
        aportantes = [l for l in self.lineas
                      if l.valor_repuestos is not None and l.valor_repuestos.hay_dato]
        if not aportantes:
            return None
        total = Decimal(0)
        for linea in aportantes:
            parcial = iva_sobre_repuestos(linea)
            if parcial is None:
                return None
            total += parcial
        return total

    @property
    def valor_repuestos_con_iva(self) -> Decimal | None:
        """Valor reconocido por repuestos en los mismos terminos que el costo."""
        repuestos = self.valor_repuestos
        iva = self.iva_sobre_repuestos
        if repuestos is None or iva is None:
            return None
        return repuestos + iva

    @property
    def valor_mano_obra_con_iva(self) -> Decimal | None:
        """La mano de obra si va gravada en los dos patrones del archivo."""
        mano_obra = self.valor_mano_obra
        return None if mano_obra is None else mano_obra * (1 + TASA_IVA)

    @property
    def clasificaciones(self) -> list[str]:
        """Como clasifico la aseguradora el caso: dano parcial, dano total, objetado."""
        return sorted({l.clasificacion for l in self.lineas if l.clasificacion})

    @property
    def facturas(self) -> list[str]:
        return sorted({l.factura for l in self.lineas if l.factura})

    @property
    def ordenes_aseguradora(self) -> list[str]:
        return sorted({l.orden_aseguradora for l in self.lineas if l.orden_aseguradora})

    @property
    def periodo(self) -> str | None:
        periodos = [l.periodo for l in self.lineas if l.periodo]
        return max(periodos) if periodos else None


@dataclass
class OrdenConciliada:
    orden_ceser: str
    ordenes_originales: list[str]
    repuestos: GrupoRepuestos | None
    aseguradora: GrupoAseguradora | None
    tipo_coincidencia: str        # "directa" | "via_tech" | "sin_aseguradora" | "sin_repuestos"
    orden_aseguradora_via_tech: str | None = None

    @property
    def costo_repuestos(self) -> Decimal | None:
        return self.repuestos.costo_total if self.repuestos else None

    @property
    def valor_aseguradora(self) -> Decimal | None:
        return self.aseguradora.valor_total if self.aseguradora else None


def agrupar_repuestos(consolidado: Consolidado) -> dict[str, GrupoRepuestos]:
    grupos: dict[str, list[LineaRepuesto]] = defaultdict(list)
    for linea in consolidado.lineas:
        grupos[linea.orden.normalizada].append(linea)
    return {orden: GrupoRepuestos(orden, lineas) for orden, lineas in grupos.items()}


def agrupar_aseguradoras(consolidado: Consolidado) -> dict[str, GrupoAseguradora]:
    grupos: dict[str, list[LineaAseguradora]] = defaultdict(list)
    for linea in consolidado.lineas:
        grupos[linea.orden.normalizada].append(linea)
    return {orden: GrupoAseguradora(orden, lineas) for orden, lineas in grupos.items()}


def conciliar(
    repuestos: Consolidado,
    aseguradoras: Consolidado,
    buscar_en_tech: BuscadorOrdenAsociada = sin_tech,
) -> list[OrdenConciliada]:
    grupos_rep = agrupar_repuestos(repuestos)
    grupos_ase = agrupar_aseguradoras(aseguradoras)

    conciliadas: list[OrdenConciliada] = []
    usadas_aseguradora: set[str] = set()

    for orden, grupo in sorted(grupos_rep.items()):
        originales = sorted({l.orden.original for l in grupo.lineas})

        grupo_ase = grupos_ase.get(orden)
        if grupo_ase is not None:
            usadas_aseguradora.add(orden)
            conciliadas.append(OrdenConciliada(orden, originales, grupo, grupo_ase, "directa"))
            continue

        # Paso 2: la orden de aseguradora puede estar registrada en TECH.
        asociada = buscar_en_tech(orden)
        if asociada and asociada in grupos_ase:
            usadas_aseguradora.add(asociada)
            conciliadas.append(
                OrdenConciliada(orden, originales, grupo, grupos_ase[asociada], "via_tech", asociada)
            )
            continue

        conciliadas.append(
            OrdenConciliada(orden, originales, grupo, None, "sin_aseguradora", asociada)
        )

    # Ordenes facturadas a la aseguradora de las que no hay registro de repuestos.
    for orden, grupo in sorted(grupos_ase.items()):
        if orden in usadas_aseguradora:
            continue
        originales = sorted({l.orden.original for l in grupo.lineas})
        conciliadas.append(OrdenConciliada(orden, originales, None, grupo, "sin_repuestos"))

    logger.info(
        "Conciliacion: %s ordenes (%s directas, %s via TECH, %s sin aseguradora, %s sin repuestos)",
        len(conciliadas),
        sum(1 for c in conciliadas if c.tipo_coincidencia == "directa"),
        sum(1 for c in conciliadas if c.tipo_coincidencia == "via_tech"),
        sum(1 for c in conciliadas if c.tipo_coincidencia == "sin_aseguradora"),
        sum(1 for c in conciliadas if c.tipo_coincidencia == "sin_repuestos"),
    )
    return conciliadas
