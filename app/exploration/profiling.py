"""Perfilado estructural de las hojas.

Este modulo describe lo que hay; no decide que significa cada columna.
Las columnas candidatas a "numero de orden" o "monetaria" se marcan como
CANDIDATAS con la evidencia que sustenta la sospecha, para que una persona
confirme antes de programar la conciliacion.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field, asdict

import pandas as pd

from app.ingestion.workbook import HojaCruda, LibroCrudo, _es_celda_vacia

MAX_EJEMPLOS = 5

PALABRAS_ORDEN = (
    "orden", "order", "ot", "no_orden", "num_orden", "numero", "nro", "folio",
    "siniestro", "reclamo", "caso", "ticket", "servicio", "remision", "referencia",
)
PALABRAS_MONETARIAS = (
    "valor", "costo", "coste", "precio", "total", "subtotal", "monto", "pago",
    "abono", "saldo", "iva", "unitario", "utilidad", "descuento", "cobrado",
    "reconocido", "facturado",
)
PALABRAS_CANTIDAD = ("cantidad", "cant", "unidades", "qty")
PALABRAS_FECHA = ("fecha", "date", "dia", "ingreso", "salida", "entrega", "creacion")

# "10582", "010582", "10582.0", "AX-44921", "OT 10582/2", "SIN-2026-0012"
RE_ORDEN = re.compile(r"^[A-Za-z]{0,6}[\s\-_/]?\d{2,12}(?:[\-_/][A-Za-z0-9]{1,6})?(?:\.0+)?$")
RE_SOLO_DIGITOS = re.compile(r"^\d+(?:\.0+)?$")
# "$1.000.000", "1.000.000,50", "1,000,000.50", "(1.000)", "1000000"
RE_MONEDA_TEXTO = re.compile(r"^[\s\$\(\-]*\d[\d\.,\s]*\)?$")


def _norm(texto: str) -> str:
    texto = texto.lower().strip()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        texto = texto.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "_", texto).strip("_")


def _contiene_palabra(nombre_norm: str, palabras: tuple[str, ...]) -> list[str]:
    """Busca palabras clave en el nombre de la columna.

    Las palabras muy cortas ("ot", "cant") solo cuentan como token completo:
    de lo contrario "valor_total" quedaria marcada como orden por contener "ot".
    """
    partes = set(nombre_norm.split("_"))
    hits = []
    for palabra in palabras:
        if palabra in partes or (len(palabra) > 3 and palabra in nombre_norm):
            hits.append(palabra)
    return hits


@dataclass
class PerfilColumna:
    nombre: str
    posicion: int
    tipo_inferido: str
    tipos_presentes: dict
    total: int
    nulos: int
    porcentaje_nulos: float
    vacios_texto: int
    valores_unicos: int
    ratio_unicidad: float
    ejemplos: list
    longitud_min: int | None
    longitud_max: int | None
    candidata_orden: dict | None = None
    candidata_monetaria: dict | None = None
    candidata_cantidad: dict | None = None
    candidata_fecha: dict | None = None
    observaciones: list[str] = field(default_factory=list)


def _tipo_de_valor(valor) -> str:
    if _es_celda_vacia(valor):
        return "vacio"
    if isinstance(valor, bool):
        return "booleano"
    if isinstance(valor, (int,)):
        return "entero"
    if isinstance(valor, float):
        return "entero" if float(valor).is_integer() else "decimal"
    if isinstance(valor, str):
        return "texto"
    if isinstance(valor, pd.Timestamp) or hasattr(valor, "isoformat"):
        return "fecha"
    return type(valor).__name__


def perfilar_columna(serie: pd.Series, nombre: str, posicion: int) -> PerfilColumna:
    valores = list(serie)
    total = len(valores)
    tipos = Counter(_tipo_de_valor(v) for v in valores)
    nulos = tipos.get("vacio", 0)
    no_vacios = [v for v in valores if not _es_celda_vacia(v)]
    vacios_texto = sum(1 for v in valores if isinstance(v, str) and not v.strip())

    textos = [str(v).strip() for v in no_vacios]
    unicos = len(set(textos))
    tipos_sin_vacio = {k: v for k, v in tipos.items() if k != "vacio"}
    tipo_inferido = max(tipos_sin_vacio, key=tipos_sin_vacio.get) if tipos_sin_vacio else "vacio"

    perfil = PerfilColumna(
        nombre=nombre,
        posicion=posicion,
        tipo_inferido=tipo_inferido,
        tipos_presentes=dict(tipos),
        total=total,
        nulos=nulos,
        porcentaje_nulos=round(nulos / total * 100, 2) if total else 0.0,
        vacios_texto=vacios_texto,
        valores_unicos=unicos,
        ratio_unicidad=round(unicos / len(no_vacios), 4) if no_vacios else 0.0,
        ejemplos=[str(v) for v in textos[:MAX_EJEMPLOS]],
        longitud_min=min((len(t) for t in textos), default=None),
        longitud_max=max((len(t) for t in textos), default=None),
    )

    crudos = [str(v) for v in no_vacios]
    _evaluar_candidaturas(perfil, textos, no_vacios, crudos)
    if len(tipos_sin_vacio) > 1:
        perfil.observaciones.append(
            "Mezcla de tipos en la misma columna: " + ", ".join(f"{k}={v}" for k, v in tipos_sin_vacio.items())
        )
    if nombre.startswith("__sin_nombre_"):
        perfil.observaciones.append("Columna sin encabezado en el archivo.")
    return perfil


def _evaluar_candidaturas(
    perfil: PerfilColumna, textos: list[str], no_vacios: list, crudos: list[str]
) -> None:
    nombre_norm = _norm(perfil.nombre)
    muestra = textos[:2000]
    n = len(muestra) or 1

    # --- numero de orden ---
    hits_nombre = _contiene_palabra(nombre_norm, PALABRAS_ORDEN)
    coinciden_patron = sum(1 for t in muestra if RE_ORDEN.match(t)) / n
    con_letras = sum(1 for t in muestra if re.search(r"[A-Za-z]", t)) / n
    razones = []
    puntaje = 0.0
    if hits_nombre:
        puntaje += 0.5
        razones.append(f"el nombre contiene {hits_nombre}")
    if coinciden_patron >= 0.8:
        puntaje += 0.25
        razones.append(f"{coinciden_patron:.0%} de los valores tienen forma de identificador")
    if perfil.ratio_unicidad >= 0.5:
        puntaje += 0.15
        razones.append(f"alta unicidad ({perfil.ratio_unicidad:.0%})")
    if perfil.porcentaje_nulos <= 5:
        puntaje += 0.1
        razones.append("casi sin vacios")
    # Una columna de puros numeros grandes tambien puede ser un valor en pesos:
    # sin una pista en el nombre o codigos alfanumericos no se marca como orden.
    evidencia_suficiente = bool(hits_nombre) or (con_letras >= 0.3 and perfil.ratio_unicidad >= 0.5)
    if puntaje >= 0.5 and evidencia_suficiente:
        perfil.candidata_orden = {
            "puntaje": round(min(puntaje, 1.0), 2),
            "razones": razones,
            "formatos_detectados": _formatos_orden(crudos[:2000]),
        }

    # --- monetaria ---
    hits_dinero = _contiene_palabra(nombre_norm, PALABRAS_MONETARIAS)
    numericos = sum(1 for v in no_vacios[:2000] if isinstance(v, (int, float)) and not isinstance(v, bool))
    con_formato_moneda = sum(1 for t in muestra if RE_MONEDA_TEXTO.match(t))
    proporcion_numerica = (numericos + con_formato_moneda) / (len(no_vacios[:2000]) or 1)
    razones = []
    puntaje = 0.0
    if hits_dinero:
        puntaje += 0.5
        razones.append(f"el nombre contiene {hits_dinero}")
    if proporcion_numerica >= 0.8:
        puntaje += 0.3
        razones.append(f"{proporcion_numerica:.0%} de los valores son numericos o con formato monetario")
    magnitudes = [abs(float(v)) for v in no_vacios[:2000] if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if magnitudes and sum(magnitudes) / len(magnitudes) >= 1000:
        puntaje += 0.2
        razones.append("magnitudes tipicas de valores en pesos")
    evidencia_dinero = bool(hits_dinero) or (
        proporcion_numerica >= 0.9 and bool(magnitudes) and not hits_nombre
    )
    if puntaje >= 0.5 and evidencia_dinero:
        perfil.candidata_monetaria = {
            "puntaje": round(min(puntaje, 1.0), 2),
            "razones": razones,
            "texto_con_simbolos": sum(1 for t in muestra if any(c in t for c in "$.,")),
            "negativos_o_parentesis": sum(1 for t in muestra if t.startswith("-") or t.startswith("(")),
        }

    # --- cantidad ---
    if _contiene_palabra(nombre_norm, PALABRAS_CANTIDAD):
        perfil.candidata_cantidad = {
            "puntaje": 0.8,
            "razones": ["el nombre sugiere cantidad"],
            "enteros": sum(1 for t in muestra if RE_SOLO_DIGITOS.match(t)),
        }

    # --- fecha ---
    fechas = sum(1 for v in no_vacios[:2000] if isinstance(v, pd.Timestamp))
    if _contiene_palabra(nombre_norm, PALABRAS_FECHA) or fechas / (len(no_vacios[:2000]) or 1) >= 0.8:
        perfil.candidata_fecha = {
            "puntaje": 0.8 if fechas else 0.5,
            "razones": ["valores de tipo fecha" if fechas else "el nombre sugiere fecha"],
        }


def _formatos_orden(muestra: list[str]) -> dict:
    solo_digitos = sum(1 for t in muestra if RE_SOLO_DIGITOS.match(t))
    con_ceros = sum(1 for t in muestra if RE_SOLO_DIGITOS.match(t) and t.startswith("0"))
    con_decimal = sum(1 for t in muestra if t.endswith(".0"))
    con_letras = sum(1 for t in muestra if re.search(r"[A-Za-z]", t))
    con_espacios = sum(1 for t in muestra if t != t.strip() or "  " in t)
    return {
        "solo_digitos": solo_digitos,
        "con_ceros_a_la_izquierda": con_ceros,
        "con_sufijo_decimal": con_decimal,
        "con_letras": con_letras,
        "con_espacios_sobrantes": con_espacios,
    }


@dataclass
class PerfilHoja:
    nombre: str
    indice: int
    filas_crudas: int
    fila_encabezado: int | None
    filas_datos: int
    columnas: int
    columnas_detalle: list[PerfilColumna]
    columnas_sin_nombre: list[str]
    columnas_totalmente_vacias: list[str]
    filas_duplicadas: int
    observaciones: list[str]


def perfilar_hoja(hoja: HojaCruda) -> PerfilHoja:
    datos = hoja.datos
    observaciones: list[str] = []

    if datos.empty:
        observaciones.append("La hoja no contiene datos legibles o esta vacia.")
        return PerfilHoja(
            nombre=hoja.nombre,
            indice=hoja.indice,
            filas_crudas=len(hoja.crudo),
            fila_encabezado=hoja.fila_encabezado,
            filas_datos=0,
            columnas=int(hoja.crudo.shape[1]) if len(hoja.crudo) else 0,
            columnas_detalle=[],
            columnas_sin_nombre=[],
            columnas_totalmente_vacias=[],
            filas_duplicadas=0,
            observaciones=observaciones,
        )

    columnas_detalle = [
        perfilar_columna(datos[col], str(col), pos) for pos, col in enumerate(datos.columns)
    ]

    sin_nombre = [c.nombre for c in columnas_detalle if c.nombre.startswith("__sin_nombre_")]
    vacias = [c.nombre for c in columnas_detalle if c.nulos == c.total]

    if hoja.fila_encabezado not in (0, None):
        observaciones.append(
            f"El encabezado no esta en la primera fila (se detecto en la fila {hoja.fila_encabezado + 1})."
        )
    if sin_nombre:
        observaciones.append(f"{len(sin_nombre)} columna(s) sin encabezado.")
    if vacias:
        observaciones.append(f"{len(vacias)} columna(s) completamente vacias.")
    if not any(c.candidata_orden for c in columnas_detalle):
        observaciones.append("No se identifico ninguna columna candidata a numero de orden.")

    try:
        duplicadas = int(datos.astype(str).duplicated().sum())
    except Exception:  # pragma: no cover - datos exoticos
        duplicadas = 0

    return PerfilHoja(
        nombre=hoja.nombre,
        indice=hoja.indice,
        filas_crudas=len(hoja.crudo),
        fila_encabezado=hoja.fila_encabezado,
        filas_datos=len(datos),
        columnas=len(datos.columns),
        columnas_detalle=columnas_detalle,
        columnas_sin_nombre=sin_nombre,
        columnas_totalmente_vacias=vacias,
        filas_duplicadas=duplicadas,
        observaciones=observaciones,
    )


def perfilar_libro(libro: LibroCrudo) -> list[PerfilHoja]:
    return [perfilar_hoja(hoja) for hoja in libro.hojas]


def inconsistencias_entre_hojas(perfiles: list[PerfilHoja]) -> list[str]:
    """Diferencias estructurales entre hojas del mismo archivo."""
    hallazgos: list[str] = []
    conjuntos = {p.nombre: {_norm(c.nombre) for c in p.columnas_detalle} for p in perfiles if p.columnas_detalle}
    if len(conjuntos) < 2:
        return hallazgos

    comunes = set.intersection(*conjuntos.values())
    todas = set.union(*conjuntos.values())
    if comunes:
        hallazgos.append(f"Columnas presentes en todas las hojas ({len(comunes)}): {sorted(comunes)}")
    else:
        hallazgos.append("Ninguna columna es comun a todas las hojas: se requieren adaptadores por hoja.")

    for nombre, cols in conjuntos.items():
        faltantes = comunes - cols
        propias = cols - set.union(*[c for n, c in conjuntos.items() if n != nombre])
        if propias:
            hallazgos.append(f"Hoja '{nombre}' tiene columnas que no aparecen en ninguna otra: {sorted(propias)}")
        if faltantes:
            hallazgos.append(f"Hoja '{nombre}' no tiene: {sorted(faltantes)}")

    anchos = {p.nombre: p.columnas for p in perfiles}
    if len(set(anchos.values())) > 1:
        hallazgos.append(f"Las hojas tienen distinto numero de columnas: {anchos}")
    return hallazgos


def a_dict(perfil: PerfilHoja) -> dict:
    return asdict(perfil)
