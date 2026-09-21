"""Reglas que detectan situaciones para revisar.

Cada regla es una funcion independiente y testeable que recibe una orden
conciliada y su resultado financiero, y devuelve una Situacion o None.

El usuario final ve `descripcion`, nunca `tipo_interno`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.reconciliation.calculations import ResultadoFinanciero
from app.reconciliation.matching import OrdenConciliada

MARGEN_MINIMO = Decimal("15")

# Bases sobre las que se mide el margen minimo. Definicion del area (19/08/2026):
# se revisa toda orden que quede por debajo del 15 % en cualquiera de las dos,
# sin importar la magnitud ni el signo.
#   "repuestos" -> utilidad del repuesto sobre lo cobrado por repuestos
#   "servicio"  -> utilidad de toda la orden sobre repuestos mas mano de obra
#
# No se usa el total facturado a la aseguradora como base: incluye IVA, que se
# traslada a la DIAN, y fletes, que se cobran al costo. Ese margen sale inflado
# y no dice nada sobre la rentabilidad real.
#
# Definicion del area (13/09/2026): el margen se mide en la lectura COMPARABLE,
# con IVA. El costo del proveedor viene con IVA y el valor reconocido del archivo
# no; compararlos de frente subestimaba el margen en unos 19 puntos y marcaba 9
# ordenes de las cuales 7 quedaban por encima del minimo al ponerlas en los
# mismos terminos.
BASES_MARGEN = ("repuestos", "servicio")

# Tolerancia al comparar el valor de repuestos de los dos archivos (en pesos).
TOLERANCIA_DIFERENCIA = Decimal("1")

# Al comparar totales se acumulan redondeos de IVA en varias lineas, asi que la
# tolerancia es un poco mas amplia que al comparar un valor individual.
TOLERANCIA_TOTAL = Decimal("10")

# Estados de seguimiento de `productos_seguro` en los que la orden sigue el flujo
# normal y termina cobrandose. Deducido de los datos de produccion (19/08/2026):
#
#   estado 4 (7.987 ordenes): 64 % con presupuesto aprobado, 42 % con fecha de
#     reparacion, 24 % con fecha de salida, 18 % con finiquito cargado.
#   estado 6 (14.972 ordenes): 2 % aprobadas, 0,2 % con fecha de reparacion,
#     0,08 % con fecha de salida. Es un estado terminal sin reparacion.
#
# Ademas, de las 169 ordenes cuyo presupuesto coincide con lo cobrado, las 169
# estan en estado 4 y ninguna en estado 6.
# Pendiente de que el area confirme el nombre de cada codigo.
ESTADOS_SEGUIMIENTO_FACTURABLES = {"4"}

# Clasificaciones del propio archivo (columna OBSERVACION) en las que NO se
# cobran repuestos, y por lo tanto no hay nada que revisar. Verificado sobre los
# datos: de 188 ordenes marcadas "Dano Total" ninguna cobra repuestos, y de 40
# "Objetado" tampoco; en cambio 148 de 151 "Dano Parcial" si los cobran.
CLASIFICACIONES_SIN_REPUESTOS = {"dano total", "daño total", "objetado"}

# Regla pedida por el area: cobrar a la aseguradora lo mismo que el presupuesto
# significa vender sin utilidad, y eso deberia revisarse.
#
# Esta apagada porque los datos no la sostienen todavia: de 175 ordenes
# comparables, 169 cobran exactamente el presupuesto y solo 6 cobran por encima.
# La diferencia entre el total cobrado y el presupuesto resulta ser el flete
# (26.300, 47.300, 52.600 segun destino), no un margen. Es decir, el presupuesto
# y lo cobrado son el mismo numero por construccion, y la utilidad real vive
# entre el costo de compra del repuesto y ese valor reconocido, que es lo que
# mide `regla_margen_bajo`.
# Cambiar a True si el area confirma que igual debe marcarse.
EXIGIR_UTILIDAD_SOBRE_PRESUPUESTO = False


def hoja_repuestos(orden: OrdenConciliada) -> str:
    """Nombre de la hoja de repuestos, para que el mensaje diga cual se comparo."""
    if orden.repuestos is None or not orden.repuestos.hojas:
        return "la hoja de repuestos"
    return " y ".join(h.strip() for h in orden.repuestos.hojas)


def hoja_aseguradora(orden: OrdenConciliada) -> str:
    """Nombre de la hoja de la aseguradora, idem."""
    if orden.aseguradora is None or not orden.aseguradora.hojas:
        return "el archivo de aseguradoras"
    return " y ".join(h.strip() for h in orden.aseguradora.hojas)


def _sin_repuestos_por_clasificacion(orden: OrdenConciliada) -> str | None:
    if orden.aseguradora is None:
        return None
    for clasificacion in orden.aseguradora.clasificaciones:
        if clasificacion.strip().lower() in CLASIFICACIONES_SIN_REPUESTOS:
            return clasificacion
    return None

# Ventana cubierta por el archivo de aseguradoras (confirmado: es de 2026).
# Fuera de ella no tiene sentido decir que "falta" la orden de aseguradora.
PERIODO_ASEGURADORAS_DESDE = "2026-01"
PERIODO_ASEGURADORAS_HASTA = "2026-12"


def fijar_cobertura_aseguradoras(desde: str | None, hasta: str | None) -> None:
    """La ventana sale de los meses que realmente trae el archivo, no de una constante.

    Con la version del 13/09/2026 el archivo empieza en 2025-10; con la
    ventana fija en 2026 las ordenes de octubre a diciembre de 2025 no se
    evaluaban aunque el archivo si las cubriera.
    """
    global PERIODO_ASEGURADORAS_DESDE, PERIODO_ASEGURADORAS_HASTA
    if desde and hasta:
        PERIODO_ASEGURADORAS_DESDE, PERIODO_ASEGURADORAS_HASTA = desde, hasta

SEVERIDAD_ALTA, SEVERIDAD_MEDIA, SEVERIDAD_INFO = "alta", "media", "informativa"


@dataclass(frozen=True)
class Situacion:
    tipo_interno: str
    descripcion: str
    severidad: str


def pesos(valor: Decimal | None) -> str:
    if valor is None:
        return "sin dato"
    negativo = valor < 0
    entero = f"{abs(valor):,.0f}".replace(",", ".")
    return f"{'-' if negativo else ''}${entero}"


def porcentaje(valor: Decimal | None) -> str:
    return "sin dato" if valor is None else f"{valor:.2f}".replace(".", ",") + " %"


def _en_ventana(periodo: str | None) -> bool:
    return bool(periodo) and PERIODO_ASEGURADORAS_DESDE <= periodo <= PERIODO_ASEGURADORAS_HASTA


# --- Regla 1: margen por debajo del minimo ---------------------------------
ETIQUETA_BASE = {
    "repuestos": "el valor cobrado por repuestos",
    "servicio": "el servicio completo, sin IVA ni fletes",
}

# La segunda lectura suma el IVA, asi que no puede llamarse "sin IVA".
ETIQUETA_BASE_CON_IVA = {
    "repuestos": "el valor cobrado por repuestos",
    "servicio": "el servicio completo, sin fletes",
}


def regla_margen_bajo(orden: OrdenConciliada, fin: ResultadoFinanciero) -> Situacion | None:
    """Marca la orden si queda bajo el minimo en cualquiera de las dos bases, con IVA.

    Si la hoja de la aseguradora no permite reconstruir el IVA de la orden, no
    hay lectura comparable: se evalua la del archivo, que sale corta, y la
    situacion queda como informativa para no presentar como alerta un margen
    que puede estar bien.
    """
    comparable = {"repuestos": fin.margen_sobre_repuestos_con_iva,
                  "servicio": fin.margen_servicio_con_iva}
    del_archivo = {"repuestos": fin.margen_sobre_repuestos, "servicio": fin.margen_servicio}

    hay_comparable = any(v is not None for v in comparable.values())
    margenes = comparable if hay_comparable else del_archivo
    etiquetas = ETIQUETA_BASE_CON_IVA if hay_comparable else ETIQUETA_BASE

    bajos = [(base, margenes[base]) for base in BASES_MARGEN
             if margenes.get(base) is not None and margenes[base] < MARGEN_MINIMO]
    if not bajos:
        return None

    detalle = " y del ".join(f"{porcentaje(valor)} sobre {etiquetas[base]}" for base, valor in bajos)
    peor = min(valor for _, valor in bajos)

    if not hay_comparable:
        return Situacion(
            "LOW_MARGIN",
            f"Con los valores del archivo, sin IVA, la utilidad es del {detalle}, inferior al "
            f"minimo del {porcentaje(MARGEN_MINIMO)}. La hoja de la aseguradora no permite "
            f"sumarle el IVA, y sin el el margen sale subestimado: hay que confirmarlo a mano.",
            SEVERIDAD_INFO,
        )

    archivo = " y ".join(
        f"{porcentaje(del_archivo[base])} sobre {ETIQUETA_BASE[base]}"
        for base in BASES_MARGEN if del_archivo.get(base) is not None
    )
    return Situacion(
        "LOW_MARGIN",
        f"Comparando el costo del repuesto, que trae IVA, contra lo cobrado con su IVA, la "
        f"utilidad de esta orden es del {detalle}, inferior al minimo del "
        f"{porcentaje(MARGEN_MINIMO)}."
        + (f" Con los valores del archivo tal cual, sin IVA, figuraria {archivo}." if archivo else ""),
        SEVERIDAD_ALTA if peor < 0 else SEVERIDAD_MEDIA,
    )


# --- Regla 7: el valor de repuestos difiere entre los dos archivos ----------
def regla_valores_repuestos_distintos(orden: OrdenConciliada, fin: ResultadoFinanciero) -> Situacion | None:
    """Compara el valor de repuestos en los dos puntos de la cadena.

    La cadena de una orden de aseguradora es:

        costo del repuesto (hoja de repuestos, columna VALOR)
          -> valor cobrado por el repuesto (columna VALOR REPUESTO ASEGURADORA)
          -> + mano de obra                = total cobrado
          -> registrado en TECH con su desglose por item, + IVA
          -> + fletes de recoleccion y envio = total del archivo de aseguradora

    Cuando el valor cobrado inicialmente no coincide con el que quedo registrado
    en el sistema, la diferencia se arrastra a la utilidad que reporta la hoja de
    repuestos, porque esa utilidad se calcula sobre el valor inicial.
    """
    if orden.repuestos is None or orden.aseguradora is None:
        return None
    segun_repuestos = orden.repuestos.valor_reconocido_repuestos
    segun_aseguradora = orden.aseguradora.valor_repuestos
    if segun_repuestos is None or segun_aseguradora is None:
        return None
    diferencia = segun_aseguradora - segun_repuestos
    if abs(diferencia) <= TOLERANCIA_DIFERENCIA:
        return None
    return Situacion(
        "VALUE_MISMATCH",
        f"Por repuestos se cobraron inicialmente {pesos(segun_repuestos)} segun la hoja "
        f"{hoja_repuestos(orden)}, pero en la hoja {hoja_aseguradora(orden)} del archivo de "
        f"aseguradoras se facturaron {pesos(segun_aseguradora)}. Sumando la mano de obra, "
        f"{hoja_repuestos(orden)} reporta un "
        f"cobro de {pesos(segun_repuestos + (orden.aseguradora.valor_mano_obra or Decimal(0)))} "
        f"frente a {pesos(segun_aseguradora + (orden.aseguradora.valor_mano_obra or Decimal(0)))} "
        f"registrados: la diferencia de {pesos(abs(diferencia))} se arrastra a la utilidad.",
        SEVERIDAD_ALTA,
    )


# --- Regla 2: el costo supera lo reconocido --------------------------------
def regla_costo_mayor_que_valor(orden: OrdenConciliada, fin: ResultadoFinanciero) -> Situacion | None:
    if fin.costo_repuestos is None or fin.valor_aseguradora is None:
        return None
    if fin.costo_repuestos <= fin.valor_aseguradora:
        return None
    diferencia = fin.costo_repuestos - fin.valor_aseguradora
    return Situacion(
        "COST_ABOVE_REVENUE",
        f"Los repuestos de esta orden costaron {pesos(fin.costo_repuestos)}, pero el valor "
        f"reconocido es de {pesos(fin.valor_aseguradora)}. La diferencia es de {pesos(diferencia)}.",
        SEVERIDAD_ALTA,
    )


# --- Regla 3: orden sin contraparte de aseguradora -------------------------
def regla_sin_aseguradora(orden: OrdenConciliada, fin: ResultadoFinanciero) -> Situacion | None:
    if orden.tipo_coincidencia != "sin_aseguradora":
        return None
    if orden.repuestos is not None and not orden.repuestos.espera_aseguradora:
        return None  # su contraparte se resuelve contra TECH (SAMSUNG, SUPER WEGA)
    periodo = orden.repuestos.periodo if orden.repuestos else None
    if not _en_ventana(periodo):
        return None  # fuera del periodo que cubre el archivo de aseguradoras
    return Situacion(
        "ORDER_NOT_FOUND",
        f"Esta orden tiene repuestos por {pesos(fin.costo_repuestos)} registrados en {periodo}, "
        "pero no se encontro en el archivo de aseguradoras ni mediante una orden asociada.",
        SEVERIDAD_MEDIA,
    )


# --- Regla 4: orden facturada sin registro de repuestos --------------------
def regla_sin_repuestos(orden: OrdenConciliada, fin: ResultadoFinanciero) -> Situacion | None:
    """Solo es una situacion cuando la aseguradora si pago repuestos.

    Si unicamente se cobro el transporte (hoja ACOPIO) o la aseguradora reconoce
    cero por partes, no falta nada: esa orden no llevaba repuestos.
    """
    if orden.tipo_coincidencia != "sin_repuestos" or orden.aseguradora is None:
        return None

    if all(l.concepto == "transporte" for l in orden.aseguradora.lineas):
        return None

    if not orden.aseguradora.espera_repuestos:
        return None  # hojas que no discriminan repuestos: se resuelven contra TECH

    valor_partes = orden.aseguradora.valor_repuestos
    if valor_partes is not None and valor_partes == 0:
        return None

    detalle = (
        f"reconoce {pesos(valor_partes)} por repuestos" if valor_partes is not None
        else f"reconoce {pesos(fin.valor_aseguradora)} en total"
    )
    return Situacion(
        "COST_NOT_FOUND",
        f"La aseguradora {detalle} para esta orden ({', '.join(orden.aseguradora.hojas)}), "
        "pero no hay ningun repuesto registrado a nombre de ella. "
        "No es posible calcular la utilidad.",
        SEVERIDAD_INFO,
    )


# --- Regla 5: la misma orden facturada en varias hojas ---------------------
def regla_valor_en_varias_hojas(orden: OrdenConciliada, fin: ResultadoFinanciero) -> Situacion | None:
    """Dos hojas facturando LO MISMO a la aseguradora.

    Aparecer en dos hojas no basta: el reparto normal es que la reparacion se
    facture en la hoja de la aseguradora y el transporte en ACOPIO, y eso pasa
    en 113 ordenes. Lo que hay que marcar es que dos hojas cobren el mismo
    concepto, porque ahi si se estaria cobrando dos veces.
    """
    if orden.aseguradora is None or len(orden.aseguradora.hojas) < 2:
        return None

    hojas_por_concepto: dict[str, set[str]] = {}
    for linea in orden.aseguradora.lineas:
        hojas_por_concepto.setdefault(linea.concepto, set()).add(linea.hoja_origen)

    repetidos = {c: h for c, h in hojas_por_concepto.items() if len(h) > 1}
    if not repetidos:
        return None   # cada hoja cobra un concepto distinto: es el reparto normal

    concepto, hojas = next(iter(repetidos.items()))
    detalle = ", ".join(
        f"{hoja.strip()} {pesos(sum((l.valor.valor for l in orden.aseguradora.lineas if l.hoja_origen == hoja and l.concepto == concepto and l.valor.hay_dato), Decimal(0)))}"
        for hoja in sorted(hojas)
    )
    return Situacion(
        "VALUE_IN_MULTIPLE_SHEETS",
        f"Dos hojas distintas le cobran a la aseguradora el mismo concepto ({concepto}) "
        f"por esta orden: {detalle}. El valor considerado es la suma de todo lo facturado, "
        f"{pesos(fin.valor_aseguradora)}.",
        SEVERIDAD_ALTA,
    )


# --- Regla 6: faltan datos de costo ----------------------------------------
def regla_costo_incompleto(orden: OrdenConciliada, fin: ResultadoFinanciero) -> Situacion | None:
    if orden.repuestos is None or orden.repuestos.lineas_sin_costo == 0:
        return None
    faltantes = orden.repuestos.lineas_sin_costo
    total = len(orden.repuestos.lineas)
    return Situacion(
        "MISSING_COST_DATA",
        f"{faltantes} de {total} registro(s) de repuestos de esta orden no tienen valor de costo, "
        "por lo que el costo total puede estar incompleto.",
        SEVERIDAD_MEDIA,
    )


# --- Reglas que comparan contra TECH ---------------------------------------
def regla_valor_distinto_de_tech(orden: OrdenConciliada, fin: ResultadoFinanciero, tech) -> Situacion | None:
    """El valor de repuestos del archivo de aseguradora contra el presupuesto de TECH.

    Regla del negocio (confirmada con el area el 19/08/2026): el presupuesto del
    sistema es lo que cuestan los repuestos y la mano de obra, SIN utilidad. A la
    aseguradora se le cobra ese valor mas la utilidad. Por lo tanto:

        cobrado > presupuestado  ->  normal, esa diferencia es la utilidad
        cobrado < presupuestado  ->  anomalia, se cobro por debajo del costo

    Solo se señala el segundo caso.
    """
    presupuesto = getattr(tech, "presupuesto", None)
    if presupuesto is None or presupuesto.valor_repuestos is None:
        return None
    excel = fin.valor_repuestos_reconocido
    if excel is None:
        return None
    diferencia = presupuesto.valor_repuestos - excel
    if diferencia < -TOLERANCIA_DIFERENCIA:
        # Cobrado por encima del presupuesto: correcto, ahi esta la utilidad.
        return None
    if abs(diferencia) <= TOLERANCIA_DIFERENCIA:
        if not EXIGIR_UTILIDAD_SOBRE_PRESUPUESTO:
            return None
        return Situacion(
            "NO_PROFIT",
            f"A la aseguradora se le cobraron {pesos(excel)} por repuestos, exactamente lo "
            "que el sistema tiene presupuestado. La orden se cobro sin utilidad.",
            SEVERIDAD_MEDIA,
        )

    # Caso distinto: el archivo no cobro repuestos pero el sistema si los tiene
    # presupuestados. No es una diferencia de cifras sino una posible falta de cobro.
    if excel == 0:
        clasificacion = _sin_repuestos_por_clasificacion(orden)
        if clasificacion:
            return Situacion(
                "BUDGET_NOT_EXECUTED",
                f"Esta orden tiene {pesos(presupuesto.valor_repuestos)} presupuestados en repuestos "
                f"que no se cobraron, pero el archivo la clasifica como '{clasificacion}', "
                "donde no corresponde cobrarlos.",
                SEVERIDAD_INFO,
            )

        seguro = getattr(tech, "seguro", None)
        estado = seguro.estado_seguimiento if seguro else None
        if estado is not None and estado not in ESTADOS_SEGUIMIENTO_FACTURABLES:
            # La orden no siguio el flujo que termina en cobro: no cobrar los
            # repuestos presupuestados es lo esperado. Se informa sin alarmar.
            return Situacion(
                "BUDGET_NOT_EXECUTED",
                f"Esta orden tiene {pesos(presupuesto.valor_repuestos)} presupuestados en repuestos "
                "que no se cobraron. La orden no siguio el flujo normal de reparacion, "
                "asi que puede ser correcto.",
                SEVERIDAD_INFO,
            )

        facturas = orden.aseguradora.facturas if orden.aseguradora else []
        referencia = f" con la factura {', '.join(facturas)}" if facturas else ""
        return Situacion(
            "BUDGET_NOT_BILLED",
            f"El sistema tiene {pesos(presupuesto.valor_repuestos)} presupuestados y aprobados en "
            f"repuestos para esta orden, que sigue el mismo flujo que las ordenes que si se cobran, "
            f"pero lo que se cobro a la aseguradora{referencia} fue {pesos(fin.valor_aseguradora)} "
            "en total, sin ningun repuesto.",
            SEVERIDAD_ALTA,
        )

    return Situacion(
        "TECH_VALUE_MISMATCH",
        f"A la aseguradora se le cobraron {pesos(excel)} por repuestos, menos de los "
        f"{pesos(presupuesto.valor_repuestos)} que el sistema tiene presupuestados. "
        f"Se cobro {pesos(diferencia)} por debajo del costo, sin utilidad.",
        SEVERIDAD_ALTA,
    )


def regla_orden_no_esta_en_tech(orden: OrdenConciliada, fin: ResultadoFinanciero, tech) -> Situacion | None:
    if getattr(tech, "existe", False):
        return None
    origen = orden.aseguradora.hojas if orden.aseguradora else (orden.repuestos.hojas if orden.repuestos else [])
    return Situacion(
        "ORDER_NOT_IN_TECH",
        f"Esta orden aparece en {', '.join(origen) or 'los archivos'} pero no existe en el "
        "sistema. No es posible verificar su estado ni sus valores.",
        SEVERIDAD_MEDIA,
    )


def regla_siniestro_distinto(orden: OrdenConciliada, fin: ResultadoFinanciero, tech) -> Situacion | None:
    """El caso de la aseguradora del archivo contra el numero de siniestro del sistema."""
    seguro = getattr(tech, "seguro", None)
    if seguro is None or not seguro.nro_siniestro or orden.aseguradora is None:
        return None
    casos = orden.aseguradora.ordenes_aseguradora
    if not casos or seguro.nro_siniestro in casos:
        return None
    return Situacion(
        "CASE_MISMATCH",
        f"El archivo relaciona esta orden con el caso {', '.join(casos)}, pero en el sistema "
        f"figura el siniestro {seguro.nro_siniestro}.",
        SEVERIDAD_MEDIA,
    )


def regla_total_del_servicio_no_cuadra(orden: OrdenConciliada, fin: ResultadoFinanciero, tech) -> Situacion | None:
    """El total del servicio registrado en TECH contra lo facturado en el archivo.

    TECH no guarda el total: lo calcula como la suma de las lineas del presupuesto
    mas el IVA. Ese es el valor que muestra la consulta de seguros. Al archivo de
    la aseguradora se le suman ademas los fletes de recoleccion y envio, que se
    cobran al costo. Por lo tanto debe cumplirse:

        total del archivo  =  total del servicio en TECH  +  fletes

    Cuando no cuadra, una de las dos fuentes tiene un valor que la otra no conoce.
    """
    presupuesto = getattr(tech, "presupuesto", None)
    if presupuesto is None or orden.aseguradora is None:
        return None
    total_tech = presupuesto.valor_total
    total_archivo = fin.valor_aseguradora
    if total_tech is None or total_archivo is None:
        return None

    # Solo tiene sentido verificar la aritmetica cuando la orden se cobro de
    # forma normal. Si los repuestos ya difieren, o si la orden aparece en
    # varias hojas, la diferencia del total ya esta explicada por otra regla y
    # repetirla aqui solo genera ruido.
    if presupuesto.valor_repuestos is None or fin.valor_repuestos_reconocido is None:
        return None
    if abs(presupuesto.valor_repuestos - fin.valor_repuestos_reconocido) > TOLERANCIA_DIFERENCIA:
        return None
    if len(orden.aseguradora.hojas) > 1:
        return None
    fletes = orden.aseguradora.valor_transporte_cobrado or Decimal(0)
    diferencia = total_archivo - (total_tech + fletes)
    if abs(diferencia) <= TOLERANCIA_TOTAL:
        return None

    # La diferencia casi siempre viene de la mano de obra: el archivo cobra una
    # tarifa y el sistema tiene registrada otra. Se identifica para no dejarle
    # al usuario un descuadre sin causa.
    mo_sistema = presupuesto.valor_mano_obra
    mo_archivo = orden.aseguradora.valor_mano_obra
    causa = ""
    if mo_sistema is not None and mo_archivo is not None:
        diferencia_mo = mo_archivo - mo_sistema
        if abs(diferencia_mo) > TOLERANCIA_DIFERENCIA:
            causa = (f" La mano de obra explica la diferencia: el archivo cobra "
                     f"{pesos(mo_archivo)} y el sistema tiene registrados {pesos(mo_sistema)}.")

    if diferencia > 0:
        texto = (f"El servicio quedo registrado en el sistema por {pesos(total_tech)} y a la "
                 f"aseguradora se le facturaron {pesos(total_archivo)}, incluidos {pesos(fletes)} "
                 f"de fletes: {pesos(diferencia)} por encima de lo registrado.{causa}")
    else:
        texto = (f"El servicio quedo registrado en el sistema por {pesos(total_tech)} pero a la "
                 f"aseguradora solo se le facturaron {pesos(total_archivo)}, incluidos "
                 f"{pesos(fletes)} de fletes: faltan {pesos(abs(diferencia))} por cobrar.{causa}")

    return Situacion(
        "SERVICE_TOTAL_MISMATCH",
        texto,
        SEVERIDAD_MEDIA if causa else SEVERIDAD_ALTA,
    )


def regla_saldo_pendiente(orden: OrdenConciliada, fin: ResultadoFinanciero, tech) -> Situacion | None:
    """Diferencia entre lo facturado en el sistema y lo efectivamente pagado."""
    pagos = getattr(tech, "pagos", None)
    if pagos is None:
        return None
    # `ordenes.VALOR_FAC` esta practicamente sin usar en TECH (3 de las ultimas
    # 5.000 ordenes), asi que la referencia es lo cobrado a la aseguradora.
    orden_tech = getattr(tech, "orden", None)
    referencia = (orden_tech.valor_fac if orden_tech and orden_tech.valor_fac else None)
    if referencia is None:
        referencia = fin.valor_aseguradora
    if referencia is None:
        return None
    saldo = referencia - pagos.total_pagado
    if saldo <= TOLERANCIA_DIFERENCIA:
        return None
    return Situacion(
        "PAYMENT_DIFFERENCE",
        f"Esta orden tiene un valor de {pesos(referencia)} y pagos registrados por "
        f"{pesos(pagos.total_pagado)}. El saldo actual es de {pesos(saldo)}.",
        SEVERIDAD_MEDIA,
    )


# --- Regla: el total cobrado en la hoja contra el total facturado ----------
# Flujo definido por el area (25/08/2026), sobre HAROLD H.T:
#
#   1. de la hoja de repuestos se toman ORDEN, FACT, FECHA, VALOR (lo que nos
#      cobro el proveedor), VALOR REPUESTO ASEGURADORA (lo que se le cobro a la
#      aseguradora por el repuesto) y TOTAL COBRADO (repuesto + mano de obra);
#   2. se busca la misma ORDEN en el archivo de aseguradoras y se compara su
#      total (VALOR TOTAL / TOTAL, segun la hoja) contra TOTAL COBRADO:
#        - iguales                -> no hay nada que revisar;
#        - el archivo cobra mas   -> se desglosa: normalmente es IVA y fletes;
#        - el archivo cobra menos -> alerta.
#
# TOTAL COBRADO viene SIN IVA y SIN fletes; el total del archivo los incluye.
# Verificado sobre los datos de produccion (25/08/2026): en las 116 ordenes de
# H.T con contraparte se cumple total_archivo = TOTAL COBRADO x 1,19 + fletes,
# exacto en 86; las 30 restantes se explican por la mano de obra o por el valor
# del repuesto, y esta regla las nombra. El desglose usa las columnas IVA y
# TRANSPORTE del propio archivo, no una tasa asumida.
def _causa_del_residuo(orden: OrdenConciliada) -> str:
    """Que componente explica que las dos fuentes no cierren."""
    causas = []
    rep, ase = hoja_repuestos(orden), hoja_aseguradora(orden)
    mo_hoja = orden.repuestos.mano_obra_cobrada if orden.repuestos else None
    mo_archivo = orden.aseguradora.valor_mano_obra if orden.aseguradora else None
    if mo_hoja is not None and mo_archivo is not None:
        diferencia = mo_archivo - mo_hoja
        if abs(diferencia) > TOLERANCIA_DIFERENCIA:
            causas.append(
                f"la mano de obra, que {rep} registra en {pesos(mo_hoja)} "
                f"y {ase} cobra en {pesos(mo_archivo)}"
            )
    partes_hoja = orden.repuestos.valor_reconocido_repuestos if orden.repuestos else None
    partes_archivo = orden.aseguradora.valor_repuestos if orden.aseguradora else None
    if partes_hoja is not None and partes_archivo is not None:
        diferencia = partes_archivo - partes_hoja
        if abs(diferencia) > TOLERANCIA_DIFERENCIA:
            causas.append(
                f"el valor del repuesto, cobrado en {pesos(partes_hoja)} segun {rep} "
                f"y en {pesos(partes_archivo)} segun {ase}"
            )
    if not causas:
        return " No se identifica que componente lo explica: hay que revisar la orden."
    return " La diferencia esta en " + " y en ".join(causas) + "."


def _transporte_facturado(orden: OrdenConciliada) -> Decimal:
    """Fletes del archivo: la columna TRANSPORTE mas las hojas que son solo envio."""
    if orden.aseguradora is None:
        return Decimal(0)
    de_columna = orden.aseguradora.valor_transporte_cobrado or Decimal(0)
    de_hojas_de_envio = orden.aseguradora.valor_por_concepto.get("transporte", Decimal(0))
    return de_columna + de_hojas_de_envio


def regla_total_cobrado_vs_facturado(orden: OrdenConciliada, fin: ResultadoFinanciero) -> Situacion | None:
    if orden.repuestos is None or orden.aseguradora is None:
        return None
    total_cobrado = orden.repuestos.total_cobrado
    total_facturado = orden.aseguradora.valor_total
    if total_cobrado is None or total_facturado is None:
        return None

    rep, ase = hoja_repuestos(orden), hoja_aseguradora(orden)
    diferencia = total_facturado - total_cobrado
    if abs(diferencia) <= TOLERANCIA_TOTAL:
        return None  # los dos archivos dicen lo mismo

    if diferencia < 0:
        # Ni siquiera alcanza el valor nominal: el archivo cobra menos de lo que
        # la hoja dice haber cobrado, antes de sumarle IVA y fletes.
        return Situacion(
            "BILLED_BELOW_CHARGED",
            f"La hoja {rep} registra un cobro de {pesos(total_cobrado)} por esta orden, pero en "
            f"la hoja {ase} del archivo de aseguradoras se facturaron {pesos(total_facturado)}, "
            f"{pesos(abs(diferencia))} menos, sin haberle sumado todavia el IVA ni los fletes."
            + _causa_del_residuo(orden),
            SEVERIDAD_ALTA,
        )

    # El archivo cobra mas: hay que desglosar antes de decir que algo esta mal.
    iva = orden.aseguradora.valor_iva
    transporte = _transporte_facturado(orden)
    if iva is None and transporte == 0:
        return Situacion(
            "BILLING_NOT_BROKEN_DOWN",
            f"La hoja {rep} registra un cobro de {pesos(total_cobrado)} y la hoja {ase} del "
            f"archivo de aseguradoras factura {pesos(total_facturado)}, {pesos(diferencia)} mas. "
            f"{ase} no discrimina IVA ni fletes, asi que la diferencia no se puede desglosar.",
            SEVERIDAD_INFO,
        )

    residuo = diferencia - (iva or Decimal(0)) - transporte
    if abs(residuo) <= TOLERANCIA_TOTAL:
        return None  # la diferencia es exactamente IVA mas fletes: es lo esperado

    detalle = (f"La hoja {rep} registra un cobro de {pesos(total_cobrado)} y la hoja {ase} del "
               f"archivo de aseguradoras factura {pesos(total_facturado)}, de los cuales "
               f"{pesos(iva or Decimal(0))} son IVA y {pesos(transporte)} fletes.")
    if residuo < 0:
        return Situacion(
            "BILLED_BELOW_CHARGED",
            f"{detalle} Descontados esos dos conceptos quedan {pesos(total_facturado - (iva or Decimal(0)) - transporte)} "
            f"facturados contra {pesos(total_cobrado)} cobrados: faltan {pesos(abs(residuo))}."
            + _causa_del_residuo(orden),
            SEVERIDAD_ALTA,
        )
    return Situacion(
        "BILLED_ABOVE_CHARGED",
        f"{detalle} Aun descontandolos sobran {pesos(residuo)} que {rep} no registra, de modo "
        f"que la utilidad que esa hoja reporta queda por debajo de la real."
        + _causa_del_residuo(orden),
        SEVERIDAD_MEDIA,
    )


# --- Regla: la misma orden repetida dentro de una hoja de repuestos --------
# Si Harold factura dos veces la misma orden, nos esta cobrando dos veces el
# mismo repuesto. No se afirma que lo sea: se marca para que el area lo mire,
# porque una orden tambien puede llevar dos repuestos distintos en dos filas.
# Lo que inclina la balanza es que las dos filas compartan factura y valor.
def regla_orden_repetida_en_la_hoja(orden: OrdenConciliada, fin: ResultadoFinanciero) -> Situacion | None:
    if orden.repuestos is None:
        return None

    # Filas auditadas y, ademas, las de la misma hoja anteriores al corte: la
    # primera mitad de un cobro doble puede estar por encima de la fila 1719.
    por_hoja: dict[str, list[dict]] = {}
    for linea in orden.repuestos.lineas:
        # SAMSUNG lista un repuesto por fila, no una orden por fila: ahi varias
        # filas de la misma orden son lo normal y no significan cobro doble.
        if not linea.una_fila_por_orden:
            continue
        filas = por_hoja.setdefault(linea.hoja_origen, [])
        filas.append({"fila": linea.fila, "factura": linea.factura,
                      "costo": linea.costo.valor if linea.costo.hay_dato else None})
        for previa in linea.apariciones_previas:
            if all(f["fila"] != previa["fila"] for f in filas):
                filas.append(previa)

    repetidas = {h: fs for h, fs in por_hoja.items() if len(fs) > 1}
    if not repetidas:
        return None   # aparece en varias hojas, no repetida dentro de una: otra regla

    hoja, filas = next(iter(repetidas.items()))
    filas.sort(key=lambda f: f["fila"])
    facturas = {f["factura"] for f in filas if f["factura"]}
    costos = {f["costo"] for f in filas if f["costo"] is not None}
    total = sum(costos_ for costos_ in (f["costo"] for f in filas) if costos_ is not None)

    if len(facturas) == 1 and len(costos) == 1:
        detalle = (f" Las {len(filas)} filas comparten la misma factura ({next(iter(facturas))}) y el "
                   f"mismo valor ({pesos(next(iter(costos)))}): tiene toda la pinta de un cobro doble.")
        severidad = SEVERIDAD_ALTA
    else:
        detalle = (" Traen facturas o valores distintos, asi que pueden ser dos repuestos "
                   "legitimos de la misma orden; hay que confirmarlo.")
        severidad = SEVERIDAD_MEDIA

    return Situacion(
        "DUPLICATE_IN_SHEET",
        f"La orden aparece {len(filas)} veces en la hoja {hoja.strip()}, en las filas "
        f"{', '.join(str(f['fila']) for f in filas)}, por un total de {pesos(Decimal(total))}.{detalle}",
        severidad,
    )


def _veces_cobrada(orden: OrdenConciliada) -> int:
    """Cuantas filas de hojas de una-orden-por-fila tiene la orden, contando las previas al corte."""
    if orden.repuestos is None:
        return 0
    filas = set()
    for linea in orden.repuestos.lineas:
        if linea.una_fila_por_orden:
            filas.add((linea.hoja_origen, linea.fila))
            filas.update((linea.hoja_origen, p["fila"]) for p in linea.apariciones_previas)
    return len(filas)


# --- Regla: se le cobro a la aseguradora antes de que nos facturaran -------
def _meses_entre(desde: str, hasta: str) -> int:
    """'2025-11', '2026-04' -> 5."""
    a1, m1 = int(desde[:4]), int(desde[5:7])
    a2, m2 = int(hasta[:4]), int(hasta[5:7])
    return (a2 - a1) * 12 + (m2 - m1)


# Hasta este atraso se considera desfase de cierre de mes; por encima, el
# proveedor esta cobrando fuera de tiempo y es anomalia de severidad alta.
ATRASO_TOLERADO_MESES = 1


def regla_facturada_antes_de_la_compra(orden: OrdenConciliada, fin: ResultadoFinanciero) -> Situacion | None:
    """El proveedor nos cobro despues de que ya le habiamos facturado a la aseguradora.

    El orden natural es al reves: primero el proveedor factura el repuesto y
    despues se le cobra a la aseguradora. Cuando se invierte, el proveedor
    cobro fuera de tiempo. Definicion del area (13/09/2026): siempre que no sea
    un cobro repetido, es una anomalia que debe mostrarse en el analisis. Por
    eso el mensaje confirma explicitamente si la orden aparece una sola vez.
    """
    if orden.repuestos is None or orden.aseguradora is None:
        return None
    fecha_proveedor = orden.repuestos.fecha_factura
    mes_aseguradora = orden.aseguradora.periodo
    if not fecha_proveedor or not mes_aseguradora:
        return None

    atraso = _meses_entre(mes_aseguradora, fecha_proveedor[:7])
    if atraso <= 0:
        return None

    veces = _veces_cobrada(orden)
    if veces <= 1:
        repeticion = f" La orden aparece una sola vez en {hoja_repuestos(orden)}: es un unico cobro, hecho tarde."
    else:
        repeticion = (f" Ademas la orden aparece {veces} veces en {hoja_repuestos(orden)}: revisar "
                      f"primero si no es un cobro doble.")

    return Situacion(
        "BILLED_BEFORE_PURCHASE",
        f"{hoja_repuestos(orden)} nos cobro el repuesto el {fecha_proveedor} con la factura "
        f"{', '.join(orden.repuestos.facturas_proveedor) or 'sin numero'}, {atraso} "
        f"{'mes' if atraso == 1 else 'meses'} despues de que se le facturara a la aseguradora "
        f"({mes_aseguradora}, factura {', '.join(orden.aseguradora.facturas) or 'sin numero'})."
        + repeticion,
        SEVERIDAD_ALTA if (atraso > ATRASO_TOLERADO_MESES or veces > 1) else SEVERIDAD_MEDIA,
    )

REGLAS_TECH = (
    regla_valor_distinto_de_tech,
    regla_total_del_servicio_no_cuadra,
    regla_orden_no_esta_en_tech,
    regla_siniestro_distinto,
    regla_saldo_pendiente,
)


REGLAS = (
    regla_margen_bajo,
    regla_costo_mayor_que_valor,
    regla_sin_aseguradora,
    regla_sin_repuestos,
    regla_valor_en_varias_hojas,
    regla_costo_incompleto,
    regla_valores_repuestos_distintos,
    regla_total_cobrado_vs_facturado,
    regla_orden_repetida_en_la_hoja,
    regla_facturada_antes_de_la_compra,
)


def evaluar(orden: OrdenConciliada, fin: ResultadoFinanciero, tech=None) -> list[Situacion]:
    """Una misma orden puede presentar varias situaciones simultaneamente.

    Las reglas que cruzan contra TECH solo corren si TECH fue efectivamente
    consultado. No preguntarle al sistema no es una conclusion sobre la orden.
    """
    situaciones = [s for s in (regla(orden, fin) for regla in REGLAS) if s is not None]
    if tech is not None and getattr(tech, "consultado", False):
        situaciones += [s for s in (regla(orden, fin, tech) for regla in REGLAS_TECH) if s is not None]
    return situaciones
