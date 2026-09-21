"""Catalogo de aseguradoras.

TECH guarda la aseguradora como un codigo numerico en `productos_seguro.ASEGURADORA_M`
y no existe tabla de catalogo en la base: los nombres estan escritos en el PHP de
la aplicacion. Los que aparecen abajo se dedujeron cruzando las ordenes de cada
hoja del Excel contra el codigo que TECH les asigna.

La interfaz nunca debe mostrar el numero: si el codigo no esta identificado se
muestra una etiqueta legible que deja claro que falta confirmarlo.
"""

from __future__ import annotations

# codigo -> nombre. Ampliar a medida que se confirmen con el area.
ASEGURADORAS: dict[str, str] = {
    "6": "Falabella",     # 328 de 328 ordenes de la hoja FALABELLA
    "11": "Flamingo",     # 104 de 123 ordenes de la hoja FLAMINGO
}

# Codigos vistos en produccion cuyo nombre todavia no esta confirmado.
CODIGOS_CONOCIDOS = {"1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "15", "16", "18", "19", "20", "21", "22"}


def nombre_aseguradora(codigo: str | None) -> str:
    if codigo is None:
        return "Sin aseguradora registrada"
    codigo = str(codigo).strip()
    if codigo in ASEGURADORAS:
        return ASEGURADORAS[codigo]
    return f"Aseguradora sin identificar (codigo {codigo})"


def esta_identificada(codigo: str | None) -> bool:
    return codigo is not None and str(codigo).strip() in ASEGURADORAS
