"""Adaptadores por hoja hacia un modelo comun.

Cada hoja de los dos archivos tiene su propia estructura. En vez de llenar el
codigo de condicionales, cada hoja se describe de forma declarativa y un unico
adaptador produce el modelo comun.

Las asignaciones de columnas de abajo fueron CONFIRMADAS con el area el
18/08/2026 a partir del reporte estructural; no son suposiciones del sistema.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pandas as pd

from app.ingestion.workbook import HojaCruda, LibroCrudo
from app.normalization.money import MontoNormalizado, normalizar_monto
from app.normalization.orders import OrdenNormalizada, normalizar_orden

logger = logging.getLogger(__name__)

# Las tres primeras letras identifican cada mes sin ambiguedad, lo que absorbe
# los errores de digitacion reales del archivo: 'AGOSO' en MOK, 'SEPTIEM' en
# SURA, y la variante 'SETIEMBRE'.
MESES = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SEP": 9, "SET": 9, "OCT": 10, "NOV": 11, "DIC": 12,
}

# Retroceso minimo, en meses, para entender que la hoja paso al año siguiente
# (DICIEMBRE -> ENERO es 11). Un retroceso corto seria una fila fuera de orden,
# no un cambio de año, y no debe mover el año del resto de la hoja.
RETROCESO_CAMBIO_DE_ANIO = 6


# Una columna puede declararse con varios nombres: el primero es el vigente y
# los siguientes son los que tuvo antes. Los archivos se editan a mano y las
# columnas se renombran (VIDA TRANQUI, 13/09/2026); aceptar ambos nombres deja
# leer tanto la version nueva como las anteriores, sin romper la comparacion.
Columna = str | int | tuple[str, ...]


@dataclass(frozen=True)
class Cobertura:
    """Lo que cubre el archivo de aseguradoras: define que filas de repuestos auditar.

    Una fila entra si su orden aparece en el archivo, o si su fecha cae desde el
    primer mes que el archivo trae. Reemplaza el corte fijo en la fila 1719
    (13/09/2026): ese corte existia porque el archivo no cubria lo anterior, y
    cuando el archivo empezo a traer octubre de 2025 dejo 38 ordenes marcadas
    como 'facturada sin repuestos' que si tenian su repuesto, arriba del corte.
    """
    desde: str                 # 'AAAA-MM', primer mes del archivo de aseguradoras
    ordenes: frozenset[str]    # ordenes normalizadas presentes en el archivo


@dataclass(frozen=True)
class EspecHojaRepuestos:
    hoja: str
    columna_orden: Columna
    columna_costo: str | int          # int = posicion (columnas sin encabezado)
    columna_fecha: Columna | None = None
    columna_orden_externa: Columna | None = None
    columna_valor_reconocido: Columna | None = None   # lo que la aseguradora reconoce por repuestos
    columna_mano_obra: Columna | None = None          # mano de obra que la hoja suma al repuesto
    columna_total_cobrado: Columna | None = None      # repuesto + mano de obra, ANTES de IVA y fletes
    costo_incluye_iva: bool = True
    incluir: bool = True
    columna_factura: Columna | None = "FACT"   # factura con la que el proveedor nos cobro
    fila_desde: int | None = None    # numero de fila de Excel a partir del cual se toma la hoja
    # True = el alcance lo define el archivo de aseguradoras (ver Cobertura). Si
    # no se entrega cobertura, se usa fila_desde como respaldo.
    corte_por_cobertura: bool = False
    espera_aseguradora: bool = True  # False = su contraparte se resuelve contra TECH, no contra el XLSX
    una_fila_por_orden: bool = True  # False = la hoja lista un repuesto por fila, no una orden por fila
    nota: str = ""


@dataclass(frozen=True)
class EspecHojaAseguradora:
    hoja: str
    columna_orden: Columna
    columna_valor: Columna
    columna_orden_aseguradora: Columna | None = None
    columna_mes: Columna | None = None
    columna_valor_repuestos: Columna | None = None    # porcion del total correspondiente a repuestos
    columna_factura: Columna | None = "FACT"          # numero con el que se cobro a la aseguradora
    columna_clasificacion: Columna | None = None      # "Dano Parcial" / "Dano Total" / "Objetado"
    columna_mano_obra: Columna | None = None          # mano de obra cobrada a la aseguradora
    columna_transporte: Columna | None = None         # fletes, que se cobran al costo
    columna_iva: Columna | None = None                # IVA que la propia hoja discrimina
    concepto: str = "reparacion"      # "reparacion" | "transporte"
    incluir: bool = True
    repuestos_vacio_es_cero: bool = False  # True = celda vacia significa "sin repuestos"
    espera_repuestos: bool = True          # False = su costo se resuelve contra TECH
    nota: str = ""


# --- Repuestos --------------------------------------------------------------
# El costo esta con IVA en las cuatro hojas incluidas (confirmado), por lo que es
# comparable contra el valor de aseguradora, que tambien viene con IVA.
HOJAS_REPUESTOS: tuple[EspecHojaRepuestos, ...] = (
    EspecHojaRepuestos(
        hoja="HAROLD ORIGINAL", columna_orden="ORDEN", columna_costo="VALOR",
        columna_fecha="FECHA DE FACTURA", incluir=False,
        nota="Hoja de captura previa: la persona sube todo aqui y luego lo clasifica "
             "en 'HAROLD H.T' o 'HAROLD NO EN BASE DATOS'. Verificado que sus 1.871 "
             "ordenes estan en H.T con valores identicos; incluirla duplicaria costos.",
    ),
    EspecHojaRepuestos(
        hoja=" HAROLD H.T", columna_orden="ORDEN", columna_costo="VALOR",
        columna_fecha="FECHA DE FACTURA", columna_valor_reconocido="VALOR REPUESTO ASEGURADORA",
        columna_mano_obra="MANO DE OBRA", columna_total_cobrado="TOTAL COBRADO",
        fila_desde=1719, corte_por_cobertura=True,
        nota="Ordenes clasificadas que si estan en la base de datos de TECH. Se auditan "
             "las filas cuya orden aparece en el archivo de aseguradoras o cuya fecha cae "
             "dentro del periodo que ese archivo cubre (13/09/2026). La fila 1719 queda "
             "solo como respaldo cuando se consolida sin archivo de aseguradoras.",
    ),
    EspecHojaRepuestos(
        hoja="HAROLD NO EN BASE DATOS", columna_orden="ORDEN", columna_costo="VALOR",
        columna_fecha="FECHA DE FACTURA", columna_valor_reconocido="VALOR REPUESTO ASEGURADORA",
        columna_mano_obra="MANO DE OBRA", columna_total_cobrado="TOTAL COBRADO",
        nota="Ordenes que la persona no encontro en la base de datos de TECH.",
    ),
    EspecHojaRepuestos(
        hoja="SAMSUNG", columna_orden="Orden", columna_costo=16, columna_factura=None,
        columna_fecha="Fecha de Pedido", columna_orden_externa="Orden Fabricante",
        espera_aseguradora=False, una_fila_por_orden=False,
        nota="Mayoritariamente fuera de garantia, accesorios y garantia de fabrica: su "
             "contraparte se revisa contra TECH, no contra el archivo de aseguradoras. "
             "Los encabezados de esta hoja estan corridos: la columna 16 no tiene "
             "titulo y contiene el costo con IVA (columna 14 x 1,19 de la columna 15).",
    ),
    EspecHojaRepuestos(
        hoja="SUPER WEGA", columna_orden="ORDEN", columna_costo="COBRO PROVEE",
        columna_fecha="FECHA ODS", espera_aseguradora=False,
        columna_factura=("FACTURA", "FACT"),
        nota="Se revisa contra TECH, no contra el archivo de aseguradoras. COBRO PROVEE es lo que cobra el proveedor: el costo para CESER.",
    ),
)

# --- Aseguradoras -----------------------------------------------------------
# El valor tomado es el total con IVA aplicado (confirmado).
HOJAS_ASEGURADORAS: tuple[EspecHojaAseguradora, ...] = (
    EspecHojaAseguradora(
        hoja="ACOPIO", columna_orden="ORDEN", columna_valor="VALOR TOTAL",
        columna_orden_aseguradora="CASO CW", columna_mes="MES FACTURADO",
        concepto="transporte",
        nota="Productos que solo se recibieron y hubo que deshacer: se cobro unicamente "
             "el envio. 113 de sus 255 ordenes tambien aparecen facturadas en otra hoja.",
    ),
    EspecHojaAseguradora(
        hoja="FALABELLA", columna_orden="ORDEN", columna_valor="TOTAL",
        columna_clasificacion="OBSERVACION",
        columna_mano_obra="MANO DE OBRA", columna_transporte="TRANSPORTE", columna_iva="IVA",
        columna_orden_aseguradora="CASO CW", columna_mes="MES", columna_valor_repuestos="PARTES",
        repuestos_vacio_es_cero=True,
        nota="Confirmado por el area: PARTES vacia significa que la orden no llevo repuestos.",
    ),
    EspecHojaAseguradora(
        hoja="FLAMINGO", columna_orden="ORDEN", columna_valor="TOTAL",
        columna_clasificacion="OBSERVACION",
        columna_mano_obra="MANO DE OBRA", columna_transporte="TRANSPORTE", columna_iva="IVA",
        columna_orden_aseguradora="CASO CW", columna_mes="MES", columna_valor_repuestos="PARTES",
        repuestos_vacio_es_cero=True,
        nota="PARTES vacia significa que la orden no llevo repuestos: la propia hoja lo "
             "trata asi, SUBTOTAL = DIAGNOSTICO + MANO DE OBRA + PARTES + IVA se cumple en "
             "las 123 filas. No se usa el segundo bloque (SIN IVA, PARTES__1, MO, "
             "SUBTOTAL__1...) porque ahi el primer valor mezcla repuestos y diagnostico, "
             "ni VALOR EQUIPO, que esta vacia en todas las filas.",
    ),
    EspecHojaAseguradora(
        hoja="VIDA TRANQUI", columna_orden="ORDEN",
        columna_valor=("TOTAL", "VALOR TOTAL DEL SERVICIO"),
        columna_orden_aseguradora="NÚMERO DE CASO", columna_mes="MES FACTURADO",
        columna_valor_repuestos=("PARTES", "VALOR DE PARTES Y/O PIEZAS"),
        columna_mano_obra=("MO", "VALOR DE MANO DE OBRA (CUANDO LOS CASOS SI SE REPARAN)"),
        columna_transporte=("TRANSPORTE", "ADICIONAL DE TRANSPORTE O FLETES"),
        columna_iva="IVA 19%",
        repuestos_vacio_es_cero=True,
        nota="Confirmado por el area: si no hay valor en la columna de partes, es cero.",
    ),
    EspecHojaAseguradora(
        hoja="SURA", columna_orden="ORDEN", columna_valor="Total $",
        columna_orden_aseguradora="Nro. de Certificado", columna_mes="MES",
        espera_repuestos=False,
        nota="La hoja no discrimina repuestos: si una orden tiene costo se resuelve contra TECH.",
    ),
    EspecHojaAseguradora(
        hoja="MOK", columna_orden="ORDEN", columna_valor="Suma de Total",
        columna_orden_aseguradora="CASO", columna_mes="MES",
        espera_repuestos=False,
        nota="La hoja no discrimina repuestos: se resuelve contra TECH.",
    ),
)


@dataclass
class LineaRepuesto:
    hoja_origen: str
    fila: int
    orden: OrdenNormalizada
    costo: MontoNormalizado
    fecha: pd.Timestamp | None
    orden_externa: str | None
    valor_reconocido: MontoNormalizado | None = None
    valor_mano_obra: MontoNormalizado | None = None
    total_cobrado: MontoNormalizado | None = None
    espera_aseguradora: bool = True
    una_fila_por_orden: bool = True
    factura: str | None = None
    # Filas de la misma hoja, anteriores al corte, con esta misma orden. No se
    # auditan, pero un cobro doble puede tener su primera mitad ahi.
    apariciones_previas: list[dict] = field(default_factory=list)


@dataclass
class LineaAseguradora:
    hoja_origen: str
    fila: int
    orden: OrdenNormalizada
    valor: MontoNormalizado
    orden_aseguradora: str | None
    concepto: str
    periodo: str | None
    valor_repuestos: MontoNormalizado | None = None
    espera_repuestos: bool = True
    factura: str | None = None
    clasificacion: str | None = None
    valor_mano_obra: MontoNormalizado | None = None
    valor_transporte: MontoNormalizado | None = None
    valor_iva: MontoNormalizado | None = None


@dataclass
class Consolidado:
    lineas: list
    hojas_incluidas: list[str]
    hojas_excluidas: list[tuple[str, str]] = field(default_factory=list)
    descartes: list[dict] = field(default_factory=list)

    @property
    def total_lineas(self) -> int:
        return len(self.lineas)


class HojaFaltante(RuntimeError):
    """La hoja descrita en la especificacion no existe en el archivo."""


def _columna(hoja: HojaCruda, referencia: Columna) -> pd.Series:
    datos = hoja.datos
    if isinstance(referencia, int):
        if referencia >= datos.shape[1]:
            raise HojaFaltante(f"La hoja '{hoja.nombre}' no tiene una columna en la posicion {referencia}")
        return datos.iloc[:, referencia]
    candidatos = referencia if isinstance(referencia, tuple) else (referencia,)
    for nombre in candidatos:
        if nombre in datos.columns:
            if nombre != candidatos[0]:
                logger.info("Hoja '%s': se usa el nombre anterior '%s' para la columna '%s'",
                            hoja.nombre, nombre, candidatos[0])
            return datos[nombre]
    raise HojaFaltante(
        f"La hoja '{hoja.nombre}' no tiene la columna '{candidatos[0]}'"
        + (f" (tampoco con sus nombres anteriores: {', '.join(candidatos[1:])})" if len(candidatos) > 1 else "")
    )


def _columna_opcional(hoja: HojaCruda, referencia: Columna | None):
    """Columnas de apoyo (factura, clasificacion): si no existen no se falla.

    Se distingue de las columnas obligatorias, cuya ausencia si detiene la
    ejecucion porque sin ellas el analisis seria incorrecto.
    """
    if referencia is None:
        return None
    try:
        return _columna(hoja, referencia)
    except HojaFaltante:
        logger.warning("La hoja '%s' no tiene la columna opcional '%s'", hoja.nombre, referencia)
        return None


def _fecha(valor):
    if valor is None:
        return None
    fecha = pd.to_datetime(valor, errors="coerce")
    return None if pd.isna(fecha) else fecha


def _numero_de_mes(valor) -> int | None:
    """'ENERO ' -> 1, 'AGOSO' -> 8, 'Septiem' -> 9. Vacio o irreconocible -> None."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    texto = unicodedata.normalize("NFKD", str(valor)).encode("ascii", "ignore").decode().strip().upper()
    return MESES.get(texto[:3]) if len(texto) >= 3 else None


def periodos_de_hoja(valores, referencia: date) -> list[str | None]:
    """Asigna año a una columna que solo trae el nombre del mes.

    El archivo no trae el año. Hasta el 13/09/2026 se asumia 2026 para todo,
    pero la version de esa fecha empieza en OCTUBRE de 2025: la numeracion de
    facturas lo prueba (octubre 6865 ... diciembre 6955 < enero 7020).

    Las filas estan en orden cronologico, asi que se recorre la hoja y cada vez
    que el mes retrocede fuerte (DICIEMBRE -> ENERO) se avanza un año. Luego se
    ancla: el ultimo mes de la hoja es su ocurrencia mas reciente que no quede
    en el futuro respecto de `referencia`.
    """
    numeros = [_numero_de_mes(v) for v in valores]
    desplazamientos: list[int | None] = []
    anio_relativo, anterior = 0, None
    for n in numeros:
        if n is None:
            desplazamientos.append(None)
            continue
        if anterior is not None and anterior - n >= RETROCESO_CAMBIO_DE_ANIO:
            anio_relativo += 1
        desplazamientos.append(anio_relativo)
        anterior = n

    ultimo = next((i for i in range(len(numeros) - 1, -1, -1) if numeros[i] is not None), None)
    if ultimo is None:
        return [None] * len(numeros)
    anio_del_ultimo = referencia.year if numeros[ultimo] <= referencia.month else referencia.year - 1
    base = anio_del_ultimo - desplazamientos[ultimo]
    return [None if n is None else f"{base + d:04d}-{n:02d}" for n, d in zip(numeros, desplazamientos)]


def _texto(valor) -> str | None:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    texto = str(valor).strip()
    return texto or None


def _indice_hojas(libro: LibroCrudo) -> dict[str, HojaCruda]:
    return {h.nombre: h for h in libro.hojas}


def cobertura_de(aseguradoras: Consolidado) -> Cobertura | None:
    periodos = [l.periodo for l in aseguradoras.lineas if l.periodo]
    if not periodos:
        return None
    return Cobertura(desde=min(periodos),
                     ordenes=frozenset(l.orden.normalizada for l in aseguradoras.lineas))


def consolidar_repuestos(libro: LibroCrudo, especs=HOJAS_REPUESTOS,
                         cobertura: Cobertura | None = None) -> Consolidado:
    hojas = _indice_hojas(libro)
    resultado = Consolidado(lineas=[], hojas_incluidas=[])

    for espec in especs:
        if not espec.incluir:
            resultado.hojas_excluidas.append((espec.hoja, espec.nota))
            logger.info("Hoja '%s' excluida: %s", espec.hoja, espec.nota)
            continue
        hoja = hojas.get(espec.hoja)
        if hoja is None or hoja.datos.empty:
            raise HojaFaltante(f"No se encontro la hoja de repuestos '{espec.hoja}'")

        ordenes = _columna(hoja, espec.columna_orden)
        costos = _columna(hoja, espec.columna_costo)
        fechas = _columna(hoja, espec.columna_fecha) if espec.columna_fecha else None
        externas = _columna(hoja, espec.columna_orden_externa) if espec.columna_orden_externa else None
        reconocidos = _columna(hoja, espec.columna_valor_reconocido) if espec.columna_valor_reconocido else None
        facturas_prov = _columna_opcional(hoja, espec.columna_factura)
        manos_obra = _columna_opcional(hoja, espec.columna_mano_obra)
        totales_cobrados = _columna_opcional(hoja, espec.columna_total_cobrado)

        # Que filas se auditan. Con cobertura: las de ordenes presentes en el
        # archivo de aseguradoras y las fechadas dentro de su periodo. Sin ella,
        # el corte fijo por fila, si la hoja lo tiene.
        total_filas = len(hoja.datos)
        if espec.corte_por_cobertura and cobertura is not None:
            def en_alcance(i: int) -> bool:
                o = normalizar_orden(ordenes.iloc[i])
                if o.valida and o.normalizada in cobertura.ordenes:
                    return True
                f = _fecha(fechas.iloc[i]) if fechas is not None else None
                return f is not None and f.strftime("%Y-%m") >= cobertura.desde
            alcance = [en_alcance(i) for i in range(total_filas)]
            criterio = f"orden en el archivo de aseguradoras o fecha desde {cobertura.desde}"
        elif espec.fila_desde is not None:
            primera = max(0, espec.fila_desde - 2)  # fila de Excel -> indice de datos
            alcance = [i >= primera for i in range(total_filas)]
            criterio = f"desde la fila {espec.fila_desde} de Excel"
        else:
            alcance = [True] * total_filas
            criterio = None
        if criterio:
            logger.info("Hoja '%s': se auditan %s de %s filas (%s)",
                        espec.hoja, sum(alcance), total_filas, criterio)

        # Lo que queda fuera no se audita, pero se mira: si la misma orden
        # aparece dentro y fuera del alcance, Harold la pudo haber cobrado dos veces.
        previas: dict[str, list[dict]] = {}
        for i in range(total_filas):
            if alcance[i]:
                continue
            o = normalizar_orden(ordenes.iloc[i])
            if o.valida:
                c = normalizar_monto(costos.iloc[i])
                previas.setdefault(o.normalizada, []).append({
                    "fila": i + 2,
                    "factura": _texto(facturas_prov.iloc[i]) if facturas_prov is not None else None,
                    "costo": c.valor if c.hay_dato else None,
                    "fecha": _fecha(fechas.iloc[i]) if fechas is not None else None,
                })

        for i in range(total_filas):
            if not alcance[i]:
                continue
            orden = normalizar_orden(ordenes.iloc[i])
            costo = normalizar_monto(costos.iloc[i])
            if not orden.valida:
                resultado.descartes.append(
                    {"archivo": "repuestos", "hoja": espec.hoja, "fila": i + 2,
                     "orden_original": orden.original, "motivo": orden.motivo}
                )
                continue
            resultado.lineas.append(
                LineaRepuesto(
                    hoja_origen=espec.hoja,
                    fila=i + 2,
                    orden=orden,
                    costo=costo,
                    fecha=_fecha(fechas.iloc[i]) if fechas is not None else None,
                    orden_externa=_texto(externas.iloc[i]) if externas is not None else None,
                    valor_reconocido=normalizar_monto(reconocidos.iloc[i]) if reconocidos is not None else None,
                    valor_mano_obra=normalizar_monto(manos_obra.iloc[i]) if manos_obra is not None else None,
                    total_cobrado=normalizar_monto(totales_cobrados.iloc[i]) if totales_cobrados is not None else None,
                    espera_aseguradora=espec.espera_aseguradora,
                    una_fila_por_orden=espec.una_fila_por_orden,
                    factura=_texto(facturas_prov.iloc[i]) if facturas_prov is not None else None,
                    apariciones_previas=previas.get(orden.normalizada, []),
                )
            )
        resultado.hojas_incluidas.append(espec.hoja)
        logger.info("Repuestos '%s': %s lineas utiles", espec.hoja, len(resultado.lineas))
    return resultado


def consolidar_aseguradoras(libro: LibroCrudo, especs=HOJAS_ASEGURADORAS,
                            referencia: date | None = None) -> Consolidado:
    """`referencia` ancla el año de los meses: por defecto, hoy."""
    hojas = _indice_hojas(libro)
    resultado = Consolidado(lineas=[], hojas_incluidas=[])
    referencia = referencia or date.today()

    for espec in especs:
        if not espec.incluir:
            resultado.hojas_excluidas.append((espec.hoja, espec.nota))
            continue
        hoja = hojas.get(espec.hoja)
        if hoja is None or hoja.datos.empty:
            raise HojaFaltante(f"No se encontro la hoja de aseguradoras '{espec.hoja}'")

        ordenes = _columna(hoja, espec.columna_orden)
        valores = _columna(hoja, espec.columna_valor)
        asegs = _columna(hoja, espec.columna_orden_aseguradora) if espec.columna_orden_aseguradora else None
        meses = _columna(hoja, espec.columna_mes) if espec.columna_mes else None
        # El año se deduce de la hoja completa, en su orden: por eso se calcula
        # antes de recorrer y descartar filas.
        periodos = periodos_de_hoja(list(meses), referencia) if meses is not None else None
        partes = _columna(hoja, espec.columna_valor_repuestos) if espec.columna_valor_repuestos else None
        facturas = _columna_opcional(hoja, espec.columna_factura)
        clasif = _columna_opcional(hoja, espec.columna_clasificacion)
        manos = _columna_opcional(hoja, espec.columna_mano_obra)
        fletes = _columna_opcional(hoja, espec.columna_transporte)
        ivas = _columna_opcional(hoja, espec.columna_iva)

        for i in range(len(hoja.datos)):
            orden = normalizar_orden(ordenes.iloc[i])
            if not orden.valida:
                resultado.descartes.append(
                    {"archivo": "aseguradoras", "hoja": espec.hoja, "fila": i + 2,
                     "orden_original": orden.original, "motivo": orden.motivo}
                )
                continue
            valor_partes = normalizar_monto(partes.iloc[i]) if partes is not None else None
            if valor_partes is not None and not valor_partes.hay_dato and espec.repuestos_vacio_es_cero:
                valor_partes = MontoNormalizado(
                    valor_partes.original, Decimal(0),
                    "celda vacia interpretada como cero por definicion del area",
                )

            resultado.lineas.append(
                LineaAseguradora(
                    hoja_origen=espec.hoja,
                    fila=i + 2,
                    orden=orden,
                    valor=normalizar_monto(valores.iloc[i]),
                    orden_aseguradora=_texto(asegs.iloc[i]) if asegs is not None else None,
                    concepto=espec.concepto,
                    periodo=periodos[i] if periodos is not None else None,
                    valor_repuestos=valor_partes,
                    espera_repuestos=espec.espera_repuestos,
                    factura=_texto(facturas.iloc[i]) if facturas is not None else None,
                    clasificacion=_texto(clasif.iloc[i]) if clasif is not None else None,
                    valor_mano_obra=normalizar_monto(manos.iloc[i]) if manos is not None else None,
                    valor_transporte=normalizar_monto(fletes.iloc[i]) if fletes is not None else None,
                    valor_iva=normalizar_monto(ivas.iloc[i]) if ivas is not None else None,
                )
            )
        resultado.hojas_incluidas.append(espec.hoja)
        logger.info("Aseguradoras '%s': %s lineas utiles", espec.hoja, len(resultado.lineas))
    return resultado
