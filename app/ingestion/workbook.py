"""Lectura cruda de libros XLSX.

Regla de esta etapa: NO se asume que columna representa que concepto y NO se
convierten tipos. Todo se lee como objeto para poder observar los datos tal
como estan en el archivo original.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

MAX_FILAS_ESCANEO_ENCABEZADO = 15


@dataclass
class HojaCruda:
    nombre: str
    indice: int
    crudo: pd.DataFrame          # sin encabezado, tal como viene el archivo
    fila_encabezado: int | None  # indice (0-based) de la fila usada como encabezado
    datos: pd.DataFrame          # con encabezado promovido, valores sin convertir


@dataclass
class LibroCrudo:
    ruta: Path
    hojas: list[HojaCruda]

    @property
    def nombres_hojas(self) -> list[str]:
        return [h.nombre for h in self.hojas]

    @property
    def cantidad_hojas(self) -> int:
        return len(self.hojas)


def _es_celda_vacia(valor) -> bool:
    if valor is None:
        return True
    if isinstance(valor, float) and pd.isna(valor):
        return True
    if isinstance(valor, str) and not valor.strip():
        return True
    return pd.isna(valor) if not isinstance(valor, (list, dict)) else False


def detectar_fila_encabezado(crudo: pd.DataFrame, max_escaneo: int = MAX_FILAS_ESCANEO_ENCABEZADO) -> int | None:
    """Heuristica: primera fila con mayoria de celdas de texto no vacias.

    Muchos reportes traen titulos o filas en blanco antes de la tabla real.
    Devuelve None si no se encuentra una fila plausible (hoja vacia).
    """
    limite = min(max_escaneo, len(crudo))
    minimo_celdas = min(2, crudo.shape[1] if len(crudo) else 2)
    mejor_idx: int | None = None
    mejor_score = 0.0

    for idx in range(limite):
        fila = crudo.iloc[idx]
        no_vacias = [v for v in fila if not _es_celda_vacia(v)]
        if len(no_vacias) < minimo_celdas:
            continue
        textos = [v for v in no_vacias if isinstance(v, str)]
        cobertura = len(no_vacias) / max(len(fila), 1)
        proporcion_texto = len(textos) / len(no_vacias)
        unicos = len({str(v).strip().lower() for v in no_vacias}) / len(no_vacias)
        score = cobertura * 0.4 + proporcion_texto * 0.4 + unicos * 0.2
        if score > mejor_score:
            mejor_score, mejor_idx = score, idx
        # Una fila casi completa y totalmente textual es un encabezado claro.
        if cobertura >= 0.8 and proporcion_texto == 1.0:
            return idx

    return mejor_idx


def _nombres_columnas(fila: pd.Series, ancho: int) -> list[str]:
    nombres: list[str] = []
    vistos: dict[str, int] = {}
    for pos in range(ancho):
        valor = fila.iloc[pos] if pos < len(fila) else None
        if _es_celda_vacia(valor):
            nombre = f"__sin_nombre_{pos}"
        else:
            nombre = str(valor).strip()
        if nombre in vistos:
            vistos[nombre] += 1
            nombre = f"{nombre}__{vistos[nombre]}"
        else:
            vistos[nombre] = 0
        nombres.append(nombre)
    return nombres


def leer_libro(ruta: Path) -> LibroCrudo:
    """Lee todas las hojas del XLSX sin conversion de tipos."""
    ruta = Path(ruta)
    hojas_crudas = pd.read_excel(
        ruta,
        sheet_name=None,      # todas las hojas
        header=None,          # sin asumir encabezado
        dtype=object,         # sin conversion silenciosa
        engine="openpyxl",
    )

    hojas: list[HojaCruda] = []
    for indice, (nombre, crudo) in enumerate(hojas_crudas.items()):
        fila_encabezado = detectar_fila_encabezado(crudo)
        if fila_encabezado is None:
            datos = pd.DataFrame()
        else:
            ancho = crudo.shape[1]
            columnas = _nombres_columnas(crudo.iloc[fila_encabezado], ancho)
            datos = crudo.iloc[fila_encabezado + 1 :].copy()
            datos.columns = columnas
            datos = datos.reset_index(drop=True)
            # Se descartan solo las filas completamente vacias (no se toca contenido).
            datos = datos.dropna(how="all").reset_index(drop=True)

        logger.info(
            "Hoja '%s': %s filas crudas, encabezado en fila %s, %s filas de datos",
            nombre,
            len(crudo),
            fila_encabezado,
            len(datos),
        )
        hojas.append(
            HojaCruda(
                nombre=str(nombre),
                indice=indice,
                crudo=crudo,
                fila_encabezado=fila_encabezado,
                datos=datos,
            )
        )

    return LibroCrudo(ruta=ruta, hojas=hojas)


def validar_cantidad_hojas(libro: LibroCrudo, esperadas: int) -> dict:
    """Valida el numero de hojas. No interrumpe: se reporta como hallazgo."""
    encontradas = libro.cantidad_hojas
    return {
        "hojas_esperadas": esperadas,
        "hojas_encontradas": encontradas,
        "cumple": encontradas == esperadas,
        "mensaje": (
            f"Se esperaban {esperadas} hojas y se encontraron {encontradas}."
            if encontradas != esperadas
            else f"El archivo contiene las {esperadas} hojas esperadas."
        ),
    }
