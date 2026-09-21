"""Calculos financieros por orden.

El costo de repuestos viene con IVA. El valor que la aseguradora reconoce por
repuestos viene SIN IVA en el archivo, asi que cada base de margen se calcula en
dos lecturas: la del archivo tal cual, y la que le suma el IVA que el archivo
cobro, que es la unica comparable contra el costo. Las dos se reportan.

Ninguna funcion inventa ceros: si falta un dato, el resultado es None.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True)
class ResultadoFinanciero:
    costo_repuestos: Decimal | None
    valor_aseguradora: Decimal | None
    utilidad_esperada: Decimal | None
    margen_esperado: Decimal | None      # porcentaje, 2 decimales
    valor_repuestos_reconocido: Decimal | None = None
    utilidad_sobre_repuestos: Decimal | None = None
    margen_sobre_repuestos: Decimal | None = None
    valor_repuestos_con_iva: Decimal | None = None
    utilidad_sobre_repuestos_con_iva: Decimal | None = None
    margen_sobre_repuestos_con_iva: Decimal | None = None
    valor_mano_obra: Decimal | None = None
    base_servicio: Decimal | None = None
    utilidad_servicio: Decimal | None = None
    margen_servicio: Decimal | None = None
    base_servicio_con_iva: Decimal | None = None
    utilidad_servicio_con_iva: Decimal | None = None
    margen_servicio_con_iva: Decimal | None = None
    total_servicio_sistema: Decimal | None = None
    utilidad_servicio_sistema: Decimal | None = None
    margen_servicio_sistema: Decimal | None = None
    total_pagado: Decimal | None = None
    saldo: Decimal | None = None
    utilidad_segun_pagos: Decimal | None = None
    margen_segun_pagos: Decimal | None = None


def utilidad(valor: Decimal | None, costo: Decimal | None) -> Decimal | None:
    if valor is None or costo is None:
        return None
    return valor - costo


def margen(utilidad_calculada: Decimal | None, valor: Decimal | None) -> Decimal | None:
    """Margen en porcentaje. None si no hay valor o si el valor es cero."""
    if utilidad_calculada is None or valor is None or valor == 0:
        return None
    return (utilidad_calculada / valor * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def saldo(valor_total: Decimal | None, total_pagado: Decimal | None) -> Decimal | None:
    if valor_total is None or total_pagado is None:
        return None
    return valor_total - total_pagado


def calcular(
    costo_repuestos: Decimal | None,
    valor_aseguradora: Decimal | None,
    valor_total_tech: Decimal | None = None,
    total_pagado: Decimal | None = None,
    valor_repuestos_reconocido: Decimal | None = None,
    valor_mano_obra: Decimal | None = None,
    total_servicio_sistema: Decimal | None = None,
    valor_repuestos_con_iva: Decimal | None = None,
    valor_mano_obra_con_iva: Decimal | None = None,
) -> ResultadoFinanciero:
    """Calcula la utilidad en tres niveles, que responden preguntas distintas.

    1. `margen_sobre_repuestos` — utilidad del repuesto solo:
       (cobrado por repuestos - costo del repuesto) / cobrado por repuestos.
       Se entrega en dos lecturas. La del archivo tal cual queda subestimada,
       porque el costo trae IVA y el valor reconocido no; `..._con_iva` le suma
       el IVA que el archivo efectivamente cobro por esa porcion y es la unica
       comparable de frente contra el costo.

    2. `margen_servicio` — utilidad de todo el servicio, sobre lo que
       efectivamente ingresa a CESER: repuestos mas mano de obra, SIN IVA, que
       se le traslada a la DIAN, y SIN fletes, que se cobran al costo.

    3. `margen_servicio_sistema` — sobre el total del servicio tal como queda
       registrado en el sistema, IVA incluido. Es la lectura que ve quien mira la
       factura completa. Sale mas alta porque el IVA no es ingreso de CESER: se
       incluye para poder explicar la diferencia entre las dos lecturas, no para
       juzgar rentabilidad con ella.

    4. `margen_esperado` — sobre el total facturado a la aseguradora. Se conserva
       porque es la definicion literal de la especificacion, pero queda inflado
       por el IVA y los fletes: sirve como referencia, no para juzgar rentabilidad.
    """
    utilidad_esperada = utilidad(valor_aseguradora, costo_repuestos)
    utilidad_pagos = utilidad(total_pagado, costo_repuestos)
    utilidad_repuestos = utilidad(valor_repuestos_reconocido, costo_repuestos)

    # La base del servicio exige conocer lo cobrado por repuestos. Con la mano de
    # obra sola el margen sale absurdo: se estaria comparando todo el costo del
    # repuesto contra un ingreso que no lo incluye.
    base_servicio = None
    if valor_repuestos_reconocido is not None:
        base_servicio = valor_repuestos_reconocido + (valor_mano_obra or Decimal(0))
    utilidad_del_servicio = utilidad(base_servicio, costo_repuestos)

    utilidad_repuestos_con_iva = utilidad(valor_repuestos_con_iva, costo_repuestos)
    base_servicio_con_iva = None
    if valor_repuestos_con_iva is not None:
        base_servicio_con_iva = valor_repuestos_con_iva + (valor_mano_obra_con_iva or Decimal(0))
    utilidad_servicio_con_iva = utilidad(base_servicio_con_iva, costo_repuestos)
    utilidad_del_sistema = utilidad(total_servicio_sistema, costo_repuestos)

    return ResultadoFinanciero(
        costo_repuestos=costo_repuestos,
        valor_aseguradora=valor_aseguradora,
        valor_repuestos_reconocido=valor_repuestos_reconocido,
        utilidad_sobre_repuestos=utilidad_repuestos,
        margen_sobre_repuestos=margen(utilidad_repuestos, valor_repuestos_reconocido),
        valor_repuestos_con_iva=valor_repuestos_con_iva,
        utilidad_sobre_repuestos_con_iva=utilidad_repuestos_con_iva,
        margen_sobre_repuestos_con_iva=margen(utilidad_repuestos_con_iva, valor_repuestos_con_iva),
        valor_mano_obra=valor_mano_obra,
        base_servicio=base_servicio,
        utilidad_servicio=utilidad_del_servicio,
        margen_servicio=margen(utilidad_del_servicio, base_servicio),
        base_servicio_con_iva=base_servicio_con_iva,
        utilidad_servicio_con_iva=utilidad_servicio_con_iva,
        margen_servicio_con_iva=margen(utilidad_servicio_con_iva, base_servicio_con_iva),
        total_servicio_sistema=total_servicio_sistema,
        utilidad_servicio_sistema=utilidad_del_sistema,
        margen_servicio_sistema=margen(utilidad_del_sistema, total_servicio_sistema),
        utilidad_esperada=utilidad_esperada,
        margen_esperado=margen(utilidad_esperada, valor_aseguradora),
        total_pagado=total_pagado,
        saldo=saldo(valor_total_tech if valor_total_tech is not None else valor_aseguradora, total_pagado),
        utilidad_segun_pagos=utilidad_pagos,
        margen_segun_pagos=margen(utilidad_pagos, total_pagado),
    )
