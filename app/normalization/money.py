"""Normalizacion de valores monetarios.

Distingue explicitamente entre "no hay dato" (None) y "el valor es cero":
una celda vacia NO es cero. Los errores de formula de Excel (#VALUE!, #N/A,
#¡DIV/0!) se tratan como dato ausente y se deja constancia del motivo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import pandas as pd

ERRORES_EXCEL = ("#VALUE!", "#¡VALOR!", "#N/A", "#DIV/0!", "#¡DIV/0!", "#REF!", "#NAME?", "#NUM!", "#NULL!")

RE_LIMPIEZA = re.compile(r"[^\d,.\-]")


@dataclass(frozen=True)
class MontoNormalizado:
    original: object
    valor: Decimal | None
    motivo: str | None

    @property
    def hay_dato(self) -> bool:
        return self.valor is not None


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


def _separar_decimales(texto: str) -> str:
    """Resuelve el formato colombiano ('1.000.000,50') y el ingles ('1,000,000.50')."""
    tiene_punto, tiene_coma = "." in texto, "," in texto
    if tiene_punto and tiene_coma:
        decimal = "," if texto.rfind(",") > texto.rfind(".") else "."
        miles = "." if decimal == "," else ","
        return texto.replace(miles, "").replace(decimal, ".")
    if tiene_coma:
        entero, _, resto = texto.rpartition(",")
        # "1,50" es decimal; "1,000" son miles.
        return f"{entero}.{resto}" if len(resto) in (1, 2) else texto.replace(",", "")
    if tiene_punto:
        entero, _, resto = texto.rpartition(".")
        return texto if len(resto) in (1, 2) else texto.replace(".", "")
    return texto


def normalizar_monto(valor) -> MontoNormalizado:
    if _vacio(valor):
        return MontoNormalizado(valor, None, "sin dato")

    if isinstance(valor, bool):
        return MontoNormalizado(valor, None, "valor booleano, no monetario")

    if isinstance(valor, (int, float)):
        return MontoNormalizado(valor, Decimal(str(valor)), None)

    texto = str(valor).strip()
    if texto.upper() in ERRORES_EXCEL:
        return MontoNormalizado(valor, None, f"error de formula en el archivo ({texto})")

    negativo = texto.startswith("(") and texto.endswith(")")
    limpio = RE_LIMPIEZA.sub("", texto)
    if not limpio or limpio in ("-", ".", ","):
        return MontoNormalizado(valor, None, f"no es un valor monetario ({texto[:40]})")

    try:
        numero = Decimal(_separar_decimales(limpio))
    except InvalidOperation:
        return MontoNormalizado(valor, None, f"no se pudo interpretar el valor ({texto[:40]})")

    return MontoNormalizado(valor, -numero if negativo else numero, None)


def sumar(montos: list[MontoNormalizado]) -> tuple[Decimal | None, int]:
    """Suma los montos con dato. Devuelve (suma, cuantos aportaron).

    Si ninguno tiene dato devuelve (None, 0): no se inventa un cero.
    """
    con_dato = [m.valor for m in montos if m.valor is not None]
    if not con_dato:
        return None, 0
    return sum(con_dato, Decimal(0)), len(con_dato)
