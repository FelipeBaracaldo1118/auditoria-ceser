"""Base de auditoria: guarda cada corrida para que la interfaz la consulte.

Separada de TECH a proposito: TECH es la base de produccion y se consulta en
solo lectura; aqui se escribe. Por defecto es un archivo SQLite dentro del
proyecto, que en el servidor no exige montar nada; con AUDIT_DB_HOST definido
se usa MariaDB, con el mismo codigo.

Que se guarda:
  corridas            una fila por ejecucion: cuando, con que archivos, cobertura
  ordenes             una fila por orden por corrida, con las mismas metricas del
                      CSV de precios (salen de la misma definicion, COLUMNAS_ORDEN)
  situaciones         lo que cada orden presento en esa corrida
  revisiones          el estado que marco quien revisa; NO depende de la corrida,
                      para que lo ya revisado siga revisado en la siguiente
  historial_revision  cada cambio de estado, con quien y cuando
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import (Boolean, Column, Float, ForeignKey, Index, Integer, MetaData, Numeric,
                        String, Table, Text, create_engine, event, func, insert, select, update)
from sqlalchemy.engine import Engine

from app.reconciliation.audit import COLUMNAS_ORDEN, ETIQUETAS_SITUACION, ResultadoAuditoria

logger = logging.getLogger(__name__)

ESTADOS_REVISION = ("pendiente", "verificada", "en_gestion", "descartada")

# Tipo de cada columna de COLUMNAS_ORDEN. Todo lo que no figura aqui es un monto
# o un porcentaje. Una prueba verifica que ninguna columna numerica traiga texto.
_TEXTO = {
    "aseguradora", "factura_proveedor", "fecha_factura_proveedor", "factura_a_la_aseguradora",
    "mes_facturado_a_la_aseguradora", "caso_aseguradora_archivo", "siniestro_sistema",
    "estado_sistema", "fuente_sistema", "hoja_repuestos", "hoja_aseguradora", "veredicto_del_cruce",
    "ubicacion_en_repuestos", "ubicacion_en_aseguradoras",
}
_BOOLEANO = {"existe_en_sistema", "requiere_revision"}
_ENTERO = {"cantidad_registros_repuestos"}
_FUERA_DE_LA_TABLA = {"fecha_analisis", "orden_ceser"}   # viven en la corrida y en la clave

# En MariaDB las tablas deben ser InnoDB: la corrida se guarda en una transaccion
# y las revisiones usan claves foraneas, cosas que MyISAM (el motor de TECH) no
# soporta. En SQLite estas opciones se ignoran.
metadata = MetaData()

corridas = Table(
    "corridas", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("fecha_analisis", String(32), nullable=False),
    Column("guardada_en", String(32), nullable=False),
    Column("duracion_segundos", Float),
    Column("origen_archivos", String(10)),          # "drive" | "local"
    Column("tech_consultado", Boolean, nullable=False),
    Column("cobertura_desde", String(7)),
    Column("cobertura_hasta", String(7)),
    Column("ordenes_analizadas", Integer),
    Column("para_revisar", Integer),
    Column("archivos", Text),                       # JSON: version exacta de cada archivo usado
    Column("resumen", Text),                        # JSON: el mismo resumen de la ejecucion
    mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci",
)


def _tipo(nombre: str):
    if nombre in _TEXTO:
        return String(255)
    if nombre in _BOOLEANO:
        return Boolean
    if nombre in _ENTERO:
        return Integer
    return Numeric(18, 2)


ordenes = Table(
    "ordenes", metadata,
    Column("corrida_id", Integer, ForeignKey("corridas.id", ondelete="CASCADE"), primary_key=True),
    Column("orden_ceser", String(20), primary_key=True),
    *[Column(nombre, _tipo(nombre)) for nombre, _ in COLUMNAS_ORDEN if nombre not in _FUERA_DE_LA_TABLA],
    Index("ix_ordenes_orden", "orden_ceser"),
    Index("ix_ordenes_veredicto", "corrida_id", "veredicto_del_cruce"),
    mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci",
)

situaciones = Table(
    "situaciones", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("corrida_id", Integer, ForeignKey("corridas.id", ondelete="CASCADE"), nullable=False),
    Column("orden_ceser", String(20), nullable=False),
    Column("tipo", String(40), nullable=False),
    Column("etiqueta", String(120), nullable=False),
    Column("severidad", String(20), nullable=False),
    Column("descripcion", Text, nullable=False),
    Index("ix_situaciones_corrida_tipo", "corrida_id", "tipo"),
    Index("ix_situaciones_orden", "orden_ceser"),
    mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci",
)

revisiones = Table(
    "revisiones", metadata,
    Column("orden_ceser", String(20), primary_key=True),
    Column("estado", String(20), nullable=False),
    Column("actualizado_en", String(32), nullable=False),
    Column("actualizado_por", String(120)),
    Column("nota", Text),
    mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci",
)

historial_revision = Table(
    "historial_revision", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("orden_ceser", String(20), nullable=False),
    Column("estado_anterior", String(20), nullable=False),
    Column("estado_nuevo", String(20), nullable=False),
    Column("cuando", String(32), nullable=False),
    Column("por", String(120)),
    Column("nota", Text),
    Column("corrida_id", Integer, ForeignKey("corridas.id", ondelete="SET NULL")),
    Index("ix_historial_orden", "orden_ceser"),
    mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci",
)


def motor(url: str) -> Engine:
    """Engine para la base de auditoria. En SQLite crea la carpeta y activa WAL,
    que deja leer a la interfaz mientras la tarea programada escribe."""
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, future=True, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _pragmas(conexion, _):
            cursor = conexion.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
    return engine


def crear_esquema(engine: Engine) -> None:
    """Idempotente: crea las tablas que falten y agrega las columnas nuevas.

    Sin lo segundo, cada columna que se agregue al reporte obligaria a rehacer
    la base y se perderia el historial de revision.
    """
    metadata.create_all(engine)
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    for tabla in metadata.sorted_tables:
        existentes = {c["name"] for c in inspector.get_columns(tabla.name)}
        faltantes = [c for c in tabla.columns if c.name not in existentes]
        if not faltantes:
            continue
        with engine.begin() as con:
            for columna in faltantes:
                tipo = columna.type.compile(engine.dialect)
                con.execute(text(f"ALTER TABLE {tabla.name} ADD COLUMN {columna.name} {tipo}"))
                logger.info("Columna agregada a %s: %s", tabla.name, columna.name)


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def guardar_corrida(engine: Engine, resultado: ResultadoAuditoria, tech_consultado: bool = False,
                    cobertura: tuple[str | None, str | None] = (None, None)) -> int:
    """Guarda la corrida completa en una sola transaccion: o queda toda o no queda nada."""
    r = resultado.resumen
    origenes = {a.origen for a in resultado.archivos}
    with engine.begin() as con:
        corrida_id = con.execute(insert(corridas).values(
            fecha_analisis=resultado.fecha_analisis,
            guardada_en=_ahora(),
            duracion_segundos=resultado.duracion_segundos,
            origen_archivos=origenes.pop() if len(origenes) == 1 else ("mixto" if origenes else None),
            tech_consultado=tech_consultado,
            cobertura_desde=cobertura[0],
            cobertura_hasta=cobertura[1],
            ordenes_analizadas=r.get("ordenes_analizadas"),
            para_revisar=r.get("para_revisar"),
            archivos=json.dumps([a.as_dict() for a in resultado.archivos], ensure_ascii=False),
            resumen=json.dumps(r, ensure_ascii=False, default=str),
        )).inserted_primary_key[0]

        filas, sits = [], []
        for f in resultado.fotografias:
            fila = {"corrida_id": corrida_id}
            for nombre, extraer in COLUMNAS_ORDEN:
                if nombre == "orden_ceser":
                    fila[nombre] = f.orden_ceser
                elif nombre not in _FUERA_DE_LA_TABLA:
                    fila[nombre] = extraer(f)
            filas.append(fila)
            sits.extend({
                "corrida_id": corrida_id, "orden_ceser": f.orden_ceser, "tipo": s.tipo_interno,
                "etiqueta": ETIQUETAS_SITUACION.get(s.tipo_interno, s.tipo_interno),
                "severidad": s.severidad, "descripcion": s.descripcion,
            } for s in f.situaciones)
        if filas:
            con.execute(insert(ordenes), filas)
        if sits:
            con.execute(insert(situaciones), sits)

    logger.info("Corrida %s guardada: %s ordenes, %s situaciones", corrida_id, len(filas), len(sits))
    return corrida_id


def registrar_revision(engine: Engine, orden: str, estado: str, por: str | None = None,
                       nota: str | None = None, corrida_id: int | None = None) -> bool:
    """Cambia el estado de revision de una orden y deja el cambio en el historial.

    El veredicto de la auditoria no se toca: esto es una capa aparte. Devuelve
    False si el estado ya era ese y no hubo nada que registrar.
    """
    if estado not in ESTADOS_REVISION:
        raise ValueError(f"Estado de revision desconocido: {estado!r}. Validos: {ESTADOS_REVISION}")
    cuando = _ahora()
    with engine.begin() as con:
        actual = con.execute(select(revisiones.c.estado).where(revisiones.c.orden_ceser == orden)).scalar()
        anterior = actual or "pendiente"
        if anterior == estado:
            return False
        valores = dict(estado=estado, actualizado_en=cuando, actualizado_por=por, nota=nota)
        if actual is None:
            con.execute(insert(revisiones).values(orden_ceser=orden, **valores))
        else:
            con.execute(update(revisiones).where(revisiones.c.orden_ceser == orden).values(**valores))
        con.execute(insert(historial_revision).values(
            orden_ceser=orden, estado_anterior=anterior, estado_nuevo=estado,
            cuando=cuando, por=por, nota=nota, corrida_id=corrida_id,
        ))
    return True


def ultima_corrida(engine: Engine) -> dict | None:
    with engine.connect() as con:
        fila = con.execute(select(corridas).order_by(corridas.c.id.desc()).limit(1)).mappings().first()
    return dict(fila) if fila else None


def dinero_por_mes(engine: Engine, corrida_id: int, hoja_repuestos: str | None = None) -> list[dict]:
    """Lo gastado contra lo recibido, por el mes en que el proveedor cobro.

    Misma metrica que la vista de revision: gastado, facturado, IVA y fletes
    cuentan solo las ordenes con contraparte, que son las unicas comparables;
    las demas se reportan aparte, con su gasto.
    """
    o = ordenes.c
    mes = func.substr(o.fecha_factura_proveedor, 1, 7).label("mes")
    filtro = [o.corrida_id == corrida_id, o.fecha_factura_proveedor.is_not(None), o.hoja_repuestos != ""]
    if hoja_repuestos:
        filtro.append(o.hoja_repuestos.contains(hoja_repuestos))
    consulta = select(
        mes,
        o.valor_total_archivo_aseguradora.is_not(None).label("con_contraparte"),
        func.count().label("ordenes"),
        func.sum(o.costo_repuestos_archivo).label("gastado"),
        func.sum(o.valor_total_archivo_aseguradora).label("facturado"),
        func.sum(o.iva_archivo_aseguradora).label("iva"),
        func.sum(func.coalesce(o.transporte_columna_archivo, 0) + func.coalesce(o.transporte_archivo, 0)).label("fletes"),
    ).where(*filtro).group_by(mes, "con_contraparte").order_by(mes)

    por_mes: dict[str, dict] = {}
    with engine.connect() as con:
        for fila in con.execute(consulta).mappings():
            m = por_mes.setdefault(fila["mes"], {
                "mes": fila["mes"], "ordenes": 0, "gastado": Decimal(0), "facturado": Decimal(0),
                "iva": Decimal(0), "fletes": Decimal(0), "ordenes_sin_contraparte": 0,
                "gastado_sin_contraparte": Decimal(0)})
            if fila["con_contraparte"]:
                m["ordenes"] += fila["ordenes"]
                for k in ("gastado", "facturado", "iva", "fletes"):
                    m[k] += Decimal(str(fila[k] or 0))
            else:
                m["ordenes_sin_contraparte"] += fila["ordenes"]
                m["gastado_sin_contraparte"] += Decimal(str(fila["gastado"] or 0))
    for m in por_mes.values():
        m["ingreso_neto"] = m["facturado"] - m["iva"] - m["fletes"]
        m["utilidad"] = m["ingreso_neto"] - m["gastado"]
        m["margen"] = (m["utilidad"] / m["ingreso_neto"] * 100).quantize(Decimal("0.01")) if m["ingreso_neto"] else None
    return list(por_mes.values())
