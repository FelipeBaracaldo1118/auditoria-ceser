"""Lectura de la base de TECH (MariaDB). SOLO CONSULTAS.

Restricciones que impone este modulo, ademas del usuario de solo lectura:

  - toda sentencia se valida antes de ejecutarse: si no empieza por SELECT o
    SHOW, se rechaza aqui mismo;
  - nunca se recorre una tabla completa: las ordenes se consultan por lotes
    contra el indice unico, porque `ordenes` es MyISAM (147 MB, 338.771 filas)
    y un escaneo bloquea las escrituras de TECH;
  - hay tiempos limite de conexion y de lectura.

Modelo real, verificado contra produccion el 19/08/2026:

  - el numero de orden que usan los archivos de Excel es `ordenes.PREFIJO_ORDEN`
    (prefijo + consecutivo), que es la PRIMARY KEY; `ordenes.ORDEN` es solo el
    consecutivo y se repite entre prefijos;
  - las ordenes de aseguradora no viven en `ordenes`: estan en `productos_seguro`,
    con la misma numeracion en `PREFIJO_CONSECUTIVO_M`, tambien PRIMARY KEY;
  - los valores presupuestados a la aseguradora estan en `presupuesto_seguro`,
    una fila por item, donde "Mano de Obra" se separa de los repuestos;
  - `abonos.ORDEN` usa la numeracion completa y tiene indice, pero hay numeros
    reutilizados por esquemas de numeracion antiguos: los pagos se filtran por
    fecha para no mezclar ordenes distintas;
  - `est_orden` NO tiene indice por ORDEN (1,1 millones de filas): consultarla
    por orden obliga a recorrerla entera y bloquea la tabla. Por eso el estado
    se toma de `ordenes.ESTADO` y el historial queda deshabilitado por defecto.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal

from app.config.settings import ConfigError, TechDBConfig

logger = logging.getLogger(__name__)

RE_SOLO_LECTURA = re.compile(r"^\s*(select|show)\b", re.IGNORECASE)
PALABRAS_PROHIBIDAS = re.compile(
    r"\b(insert|update|delete|drop|truncate|alter|create|replace|grant|revoke|lock)\b",
    re.IGNORECASE,
)


class ConsultaNoPermitida(RuntimeError):
    """Se intento ejecutar algo que no es una consulta de lectura."""


@dataclass(frozen=True)
class OrdenTech:
    orden: str
    prefijo_orden: str | None
    orden_anterior: str | None
    orden_fabricante: str | None
    estado: str | None
    fecha_estado: object | None
    valor_fac: Decimal | None
    iva_fac: Decimal | None
    vr_partes: Decimal | None
    valor_labor: Decimal | None
    flete: Decimal | None
    descuento: Decimal | None
    factura_nro: str | None
    cobrada: str | None
    fecha_ingreso: object | None
    fecha_salida: object | None
    marca: str | None
    modelo: str | None
    serie: str | None

    @property
    def valor_total(self) -> Decimal | None:
        """Valor facturado por CESER. VALOR_FAC ya deberia venir con IVA;
        si no lo trae, se suma IVA_FAC. Pendiente de confirmar con datos reales."""
        return self.valor_fac


@dataclass(frozen=True)
class PagosTech:
    orden: str
    total_pagado: Decimal
    cantidad_abonos: int
    ultimo_pago: object | None


@dataclass(frozen=True)
class SeguroTech:
    orden: str
    consecutivo: str | None
    prefijo: str | None
    nro_siniestro: str | None
    aseguradora_codigo: str | None
    estado_seguimiento: str | None
    fecha_ingreso: object | None
    fecha_salida: object | None


@dataclass(frozen=True)
class PresupuestoSeguro:
    """Lo que CESER presupuesto a la aseguradora, desglosado."""

    orden: str
    valor_repuestos: Decimal | None
    valor_mano_obra: Decimal | None
    iva: Decimal | None
    items: int
    viable: str | None
    fecha_registro: object | None

    @property
    def valor_total(self) -> Decimal | None:
        partes = [v for v in (self.valor_repuestos, self.valor_mano_obra, self.iva) if v is not None]
        return sum(partes, Decimal(0)) if partes else None


def _validar(sql: str) -> str:
    if not RE_SOLO_LECTURA.match(sql):
        raise ConsultaNoPermitida("Este modulo solo ejecuta SELECT y SHOW.")
    sin_comentarios = re.sub(r"--[^\n]*", "", sql)
    if PALABRAS_PROHIBIDAS.search(sin_comentarios):
        raise ConsultaNoPermitida("La consulta contiene una sentencia de escritura.")
    return sql


def _a_decimal(valor) -> Decimal | None:
    if valor is None:
        return None
    try:
        return Decimal(str(valor))
    except (ValueError, ArithmeticError):
        return None


def _texto(valor) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _clave_orden(valor) -> str:
    """Normaliza la clave para poder cruzar con las ordenes del Excel."""
    texto = str(valor).strip()
    return re.sub(r"\.0+$", "", texto).lstrip("0") or "0"


def _parametros_lote(numeros: list[str]) -> tuple[str, dict]:
    """Genera marcadores nombrados: evita interpolar valores en el SQL."""
    marcadores, parametros = [], {}
    for i, numero in enumerate(numeros):
        clave = f"o{i}"
        marcadores.append(f":{clave}")
        parametros[clave] = int(numero) if numero.isdigit() else numero
    return ", ".join(marcadores), parametros


class TechReadOnly:
    """Cliente de solo lectura sobre la base de TECH."""

    def __init__(self, config: TechDBConfig):
        self.config = config
        self._engine = None

    # -- conexion ----------------------------------------------------------
    @property
    def engine(self):
        if self._engine is None:
            try:
                from sqlalchemy import create_engine
            except ImportError as exc:  # pragma: no cover
                raise ConfigError("Falta SQLAlchemy. Ejecute: pip install -r requirements.txt") from exc
            self._engine = create_engine(
                self.config.url(),
                pool_pre_ping=True,
                pool_recycle=1800,
                connect_args={
                    "connect_timeout": self.config.connect_timeout,
                    "read_timeout": self.config.read_timeout,
                },
            )
            logger.info("Conexion a TECH preparada: %s", self.config.describir())
        return self._engine

    def consultar(self, sql: str, parametros: dict | None = None) -> list[dict]:
        from sqlalchemy import text

        _validar(sql)
        with self.engine.connect() as conexion:
            filas = conexion.execute(text(sql), parametros or {}).mappings().all()
        return [dict(f) for f in filas]

    # -- diagnostico -------------------------------------------------------
    def probar(self) -> dict:
        """Verifica conectividad, identidad y que el usuario sea de solo lectura."""
        info = self.consultar(
            "SELECT VERSION() AS version, CURRENT_USER() AS usuario, "
            "DATABASE() AS base, NOW() AS ahora"
        )[0]
        permisos = [list(f.values())[0] for f in self.consultar("SHOW GRANTS FOR CURRENT_USER()")]
        info["permisos"] = permisos
        info["solo_lectura"] = not any(
            re.search(r"\b(ALL PRIVILEGES|INSERT|UPDATE|DELETE)\b", p, re.IGNORECASE)
            for p in permisos
        )
        return info

    # -- consultas del negocio --------------------------------------------
    def obtener_ordenes(self, numeros: list[str]) -> dict[str, OrdenTech]:
        """Trae las ordenes por lotes, siempre contra el indice unico."""
        resultado: dict[str, OrdenTech] = {}
        for lote in self._lotes(numeros):
            marcadores, parametros = _parametros_lote(lote)
            filas = self.consultar(
                f"""
                SELECT PREFIJO_ORDEN, PREFIJO, ORDEN, ORDEN_ANTERIOR, ORDEN_FABRICANTE,
                       ESTADO, FECHA_ESTADO, VALOR_FAC, IVA_FAC, VR_PARTES,
                       VALOR_LABOR, FLETE, DESCUENTO, FACTURA_NRO, COBRADA,
                       FECHA_INGRESO, FECHA_SALIDA, MARCA, MODELO, SERIE
                FROM ordenes
                WHERE PREFIJO_ORDEN IN ({marcadores})
                """,
                parametros,
            )
            for f in filas:
                orden = OrdenTech(
                    orden=_clave_orden(f["PREFIJO_ORDEN"]),
                    prefijo_orden=_texto(f["PREFIJO"]),
                    orden_anterior=_texto(f["ORDEN_ANTERIOR"]),
                    orden_fabricante=_texto(f["ORDEN_FABRICANTE"]),
                    estado=_texto(f["ESTADO"]),
                    fecha_estado=f["FECHA_ESTADO"],
                    valor_fac=_a_decimal(f["VALOR_FAC"]),
                    iva_fac=_a_decimal(f["IVA_FAC"]),
                    vr_partes=_a_decimal(f["VR_PARTES"]),
                    valor_labor=_a_decimal(f["VALOR_LABOR"]),
                    flete=_a_decimal(f["FLETE"]),
                    descuento=_a_decimal(f["DESCUENTO"]),
                    factura_nro=_texto(f["FACTURA_NRO"]),
                    cobrada=_texto(f["COBRADA"]),
                    fecha_ingreso=f["FECHA_INGRESO"],
                    fecha_salida=f["FECHA_SALIDA"],
                    marca=_texto(f["MARCA"]),
                    modelo=_texto(f["MODELO"]),
                    serie=_texto(f["SERIE"]),
                )
                resultado[orden.orden] = orden
        logger.info("TECH: %s de %s ordenes encontradas", len(resultado), len(numeros))
        return resultado

    def obtener_pagos(self, numeros: list[str], desde: str | None = None) -> dict[str, PagosTech]:
        """Suma de abonos por orden. `ordenes` no guarda el acumulado pagado.

        `desde` descarta pagos anteriores a esa fecha. Hace falta porque los
        numeros de orden se reutilizaron entre esquemas de numeracion: sin el
        filtro, una orden de 2026 recoge abonos de 2012 que no son suyos.
        """
        resultado: dict[str, PagosTech] = {}
        for lote in self._lotes(numeros):
            marcadores, parametros = _parametros_lote(lote)
            filtro_fecha = ""
            if desde:
                filtro_fecha = "AND FECHA >= :desde"
                parametros["desde"] = desde
            filas = self.consultar(
                f"""
                SELECT ORDEN,
                       SUM(VALOR) AS total_pagado,
                       COUNT(*) AS cantidad,
                       MAX(FECHA) AS ultimo
                FROM abonos
                WHERE ORDEN IN ({marcadores})
                {filtro_fecha}
                GROUP BY ORDEN
                """,
                parametros,
            )
            for f in filas:
                clave = _clave_orden(f["ORDEN"])
                resultado[clave] = PagosTech(
                    orden=clave,
                    total_pagado=_a_decimal(f["total_pagado"]) or Decimal(0),
                    cantidad_abonos=int(f["cantidad"]),
                    ultimo_pago=f["ultimo"],
                )
        logger.info("TECH: pagos encontrados para %s ordenes", len(resultado))
        return resultado

    def obtener_seguro(self, numeros: list[str]) -> dict[str, SeguroTech]:
        """Ordenes de aseguradora. `PREFIJO_CONSECUTIVO_M` es la PRIMARY KEY y
        usa la misma numeracion que los archivos de Excel."""
        resultado: dict[str, SeguroTech] = {}
        for lote in self._lotes(numeros):
            marcadores, parametros = _parametros_lote(lote)
            filas = self.consultar(
                f"""
                SELECT PREFIJO_CONSECUTIVO_M, CONSECUTIVO_M, PREFIJO_M, NRO_SINIESTRO_M,
                       ASEGURADORA_M, ESTADO_SEGUIMIENTO_M, FECHA_INGRESO_M, FECHA_SALIDA_M
                FROM productos_seguro
                WHERE PREFIJO_CONSECUTIVO_M IN ({marcadores})
                """,
                parametros,
            )
            for f in filas:
                clave = _clave_orden(f["PREFIJO_CONSECUTIVO_M"])
                resultado[clave] = SeguroTech(
                    orden=clave,
                    consecutivo=_texto(f["CONSECUTIVO_M"]),
                    prefijo=_texto(f["PREFIJO_M"]),
                    nro_siniestro=_texto(f["NRO_SINIESTRO_M"]),
                    aseguradora_codigo=_texto(f["ASEGURADORA_M"]),
                    estado_seguimiento=_texto(f["ESTADO_SEGUIMIENTO_M"]),
                    fecha_ingreso=f["FECHA_INGRESO_M"],
                    fecha_salida=f["FECHA_SALIDA_M"],
                )
        logger.info("TECH: %s ordenes de seguro encontradas", len(resultado))
        return resultado

    def obtener_presupuesto_seguro(self, numeros: list[str]) -> dict[str, PresupuestoSeguro]:
        """Valores presupuestados a la aseguradora, separando repuestos de mano de obra.

        `presupuesto_seguro` tiene una fila por item; los repuestos son todas las
        filas cuyo TIPO_PRESUPUESTO_M no es 'Mano de Obra'. El IVA se repite en
        cada fila del mismo presupuesto, por eso se toma el maximo y no la suma.
        """
        resultado: dict[str, PresupuestoSeguro] = {}
        for lote in self._lotes(numeros):
            marcadores, parametros = _parametros_lote(lote)
            filas = self.consultar(
                f"""
                SELECT PREFIJO_CONSECUTIVO_M,
                       SUM(CASE WHEN TIPO_PRESUPUESTO_M = 'Mano de Obra'
                                THEN 0 ELSE VALOR_M END) AS valor_repuestos,
                       SUM(CASE WHEN TIPO_PRESUPUESTO_M = 'Mano de Obra'
                                THEN VALOR_M ELSE 0 END) AS valor_mano_obra,
                       MAX(IVA_APLICADO_M) AS iva,
                       COUNT(*) AS items,
                       MAX(VIABLE_M) AS viable,
                       MAX(FECHA_REGISTRO_M) AS fecha_registro
                FROM presupuesto_seguro
                WHERE PREFIJO_CONSECUTIVO_M IN ({marcadores})
                GROUP BY PREFIJO_CONSECUTIVO_M
                """,
                parametros,
            )
            for f in filas:
                clave = _clave_orden(f["PREFIJO_CONSECUTIVO_M"])
                resultado[clave] = PresupuestoSeguro(
                    orden=clave,
                    valor_repuestos=_a_decimal(f["valor_repuestos"]),
                    valor_mano_obra=_a_decimal(f["valor_mano_obra"]),
                    iva=_a_decimal(f["iva"]),
                    items=int(f["items"]),
                    viable=_texto(f["viable"]),
                    fecha_registro=f["fecha_registro"],
                )
        logger.info("TECH: presupuesto encontrado para %s ordenes", len(resultado))
        return resultado

    def detalle_presupuesto(self, numeros: list[str]) -> dict[str, list[dict]]:
        """Las lineas del presupuesto tal como se registraron, una por item."""
        resultado: dict[str, list[dict]] = {}
        for lote in self._lotes(numeros):
            marcadores, parametros = _parametros_lote(lote)
            filas = self.consultar(
                f"""
                SELECT PREFIJO_CONSECUTIVO_M, TIPO_PRESUPUESTO_M, VALOR_M, VIABLE_M
                FROM presupuesto_seguro
                WHERE PREFIJO_CONSECUTIVO_M IN ({marcadores})
                ORDER BY PREFIJO_CONSECUTIVO_M, ID_CONSECUTIVO_M
                """,
                parametros,
            )
            for f in filas:
                resultado.setdefault(_clave_orden(f["PREFIJO_CONSECUTIVO_M"]), []).append(
                    {"tipo": _texto(f["TIPO_PRESUPUESTO_M"]),
                     "valor": _a_decimal(f["VALOR_M"]),
                     "viable": _texto(f["VIABLE_M"])}
                )
        return resultado

    def repuestos_pedidos(self, numeros: list[str]) -> dict[str, list[dict]]:
        """Repuestos solicitados al proveedor en TECH.

        `repuestos.work_order` no tiene indice, asi que se consulta en un solo
        recorrido por lote y no una vez por orden.
        """
        resultado: dict[str, list[dict]] = {}
        for lote in self._lotes(numeros):
            marcadores, parametros = _parametros_lote(lote)
            filas = self.consultar(
                f"""
                SELECT work_order, part_number, description, qty, status, order_date, provider_order
                FROM repuestos WHERE work_order IN ({marcadores})
                """,
                parametros,
            )
            for f in filas:
                resultado.setdefault(_clave_orden(f["work_order"]), []).append(
                    {"parte": _texto(f["part_number"]), "descripcion": _texto(f["description"]),
                     "cantidad": f["qty"], "estado": _texto(f["status"]),
                     "pedido_a": _texto(f["provider_order"])}
                )
        logger.info("TECH: repuestos pedidos encontrados para %s ordenes", len(resultado))
        return resultado

    def historial_estados(self, orden: str, limite: int = 50, permitir_escaneo: bool = False) -> list[dict]:
        """Historial de estados de una orden.

        ATENCION: `est_orden` no tiene indice por ORDEN y tiene 1,1 millones de
        filas. Consultarla por orden recorre la tabla completa y, siendo MyISAM,
        bloquea las escrituras de TECH mientras dura. Por eso hay que pedirlo
        explicitamente con `permitir_escaneo=True`.
        """
        if not permitir_escaneo:
            raise ConsultaNoPermitida(
                "est_orden no tiene indice por ORDEN: la consulta recorreria 1,1 millones "
                "de filas y bloquearia la tabla. Use permitir_escaneo=True solo fuera de "
                "horario, o tome el estado actual de ordenes.ESTADO."
            )
        return self.consultar(
            "SELECT CONS, ORDEN, ESTADOS, FECHA, HORA, USUARIO FROM est_orden "
            "WHERE ORDEN = :orden ORDER BY CONS DESC LIMIT :limite",
            {"orden": int(orden) if orden.isdigit() else orden, "limite": limite},
        )

    def _lotes(self, numeros: list[str]):
        unicos = sorted(set(numeros))
        tamano = self.config.tamano_lote
        for i in range(0, len(unicos), tamano):
            yield unicos[i : i + tamano]


def buscador_orden_asociada(cliente: TechReadOnly) -> "callable":
    """Implementa `BuscadorOrdenAsociada` del motor de conciliacion.

    Para una orden CESER devuelve el identificador de la aseguradora que TECH
    tenga registrado: primero el numero de siniestro de `productos_seguro`,
    y si no, la orden de fabricante de `ordenes` (caso SAMSUNG).
    """
    cache: dict[str, str | None] = {}

    def buscar(orden_ceser: str) -> str | None:
        if orden_ceser in cache:
            return cache[orden_ceser]
        seguro = cliente.obtener_seguro([orden_ceser])
        asociada = None
        if orden_ceser in seguro:
            asociada = seguro[orden_ceser].nro_siniestro
        if not asociada:
            ordenes = cliente.obtener_ordenes([orden_ceser])
            if orden_ceser in ordenes:
                asociada = ordenes[orden_ceser].orden_fabricante
        cache[orden_ceser] = asociada
        return asociada

    return buscar
