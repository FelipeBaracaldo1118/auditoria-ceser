"""Normalizacion de numeros de orden.

Funcion central del sistema: toda comparacion entre archivos y con TECH pasa
por aqui. Se conserva siempre el valor original junto al normalizado para poder
auditar la transformacion.

Reglas derivadas de los datos reales (ver reportes/reporte_estructural_*.md):
  - las ordenes validas son de 7 u 8 digitos;
  - Excel entrega algunos valores como "10582.0" -> se quita el sufijo decimal;
  - hay sufijos tipo "1362246-R" -> se conserva la base numerica y el sufijo aparte;
  - hay texto libre en la columna ("REPARACION RELOJ GIOVANNI", "?") -> no es orden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

LONGITUDES_VALIDAS = (7, 8)

RE_SUFIJO_DECIMAL = re.compile(r"\.0+$")
RE_SOLO_DIGITOS = re.compile(r"^\d+$")
RE_CON_SUFIJO = re.compile(r"^(\d+)[\-/_\s]+([A-Za-z0-9]{1,4})$")


@dataclass(frozen=True)
class OrdenNormalizada:
    original: str | None
    normalizada: str | None
    sufijo: str | None
    valida: bool
    motivo: str | None

    def __bool__(self) -> bool:
        return self.valida


def _vacio(valor) -> bool:
    if valor is None:
        return True
    if isinstance(valor, float) and pd.isna(valor):
        return True
    try:
        if pd.isna(valor):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(valor, str) and not valor.strip()


def normalizar_orden(valor) -> OrdenNormalizada:
    """Convierte un valor de celda en un identificador comparable."""
    if _vacio(valor):
        return OrdenNormalizada(None, None, None, False, "valor vacio")

    original = str(valor).strip()
    texto = re.sub(r"\s+", " ", original).upper()
    texto = RE_SUFIJO_DECIMAL.sub("", texto)
    texto = texto.replace(" ", "")

    if RE_SOLO_DIGITOS.match(texto):
        texto = texto.lstrip("0") or "0"
        if len(texto) in LONGITUDES_VALIDAS:
            return OrdenNormalizada(original, texto, None, True, None)
        return OrdenNormalizada(
            original, texto, None, False,
            f"tiene {len(texto)} digitos y se esperaban {' o '.join(map(str, LONGITUDES_VALIDAS))}",
        )

    con_sufijo = RE_CON_SUFIJO.match(texto)
    if con_sufijo:
        base, sufijo = con_sufijo.group(1).lstrip("0") or "0", con_sufijo.group(2)
        if len(base) in LONGITUDES_VALIDAS:
            return OrdenNormalizada(original, base, sufijo, True, f"se separo el sufijo '{sufijo}'")
        return OrdenNormalizada(original, base, sufijo, False, f"la base '{base}' no tiene 7 u 8 digitos")

    return OrdenNormalizada(original, None, None, False, "el valor no parece un numero de orden")


def normalizar_serie(serie) -> list[OrdenNormalizada]:
    return [normalizar_orden(v) for v in serie]
