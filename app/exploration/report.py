"""Generacion y exportacion del reporte exploratorio.

Salidas:
  - reporte_estructural_<timestamp>.json  (completo, para maquina)
  - reporte_estructural_<timestamp>.md    (legible, para revisar con el negocio)
  - columnas_<timestamp>.csv              (una fila por columna, para anotar)
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from app.drive.client import ArchivoDescargado
from app.exploration.profiling import PerfilHoja, inconsistencias_entre_hojas


def construir_reporte(
    fecha_analisis: str,
    archivos: list[tuple[str, ArchivoDescargado, dict, list[PerfilHoja]]],
) -> dict:
    """archivos: lista de (etiqueta, archivo, validacion_hojas, perfiles)."""
    bloques = []
    for etiqueta, archivo, validacion, perfiles in archivos:
        bloques.append(
            {
                "archivo": etiqueta,
                "version_utilizada": archivo.as_dict(),
                "validacion_hojas": validacion,
                "resumen": {
                    "hojas": len(perfiles),
                    "nombres_hojas": [p.nombre for p in perfiles],
                    "filas_datos_totales": sum(p.filas_datos for p in perfiles),
                    "columnas_por_hoja": {p.nombre: p.columnas for p in perfiles},
                },
                "hojas": [asdict(p) for p in perfiles],
                "inconsistencias_estructurales": inconsistencias_entre_hojas(perfiles),
            }
        )
    return {"fecha_analisis": fecha_analisis, "etapa": "exploracion_estructural", "archivos": bloques}


def _fmt_miles(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _tabla(filas: list[list[str]], encabezado: list[str]) -> str:
    lineas = ["| " + " | ".join(encabezado) + " |", "|" + "|".join(["---"] * len(encabezado)) + "|"]
    for fila in filas:
        lineas.append("| " + " | ".join(str(c) for c in fila) + " |")
    return "\n".join(lineas)


def a_markdown(reporte: dict) -> str:
    out: list[str] = ["# Reporte estructural de archivos CESER", ""]
    out.append(f"Fecha del analisis: {reporte['fecha_analisis']}")
    out.append("")
    out.append(
        "> Etapa exploratoria. No se aplican reglas financieras ni se asume el significado "
        "de las columnas. Las marcas *candidata* indican sospecha con evidencia, no una decision."
    )
    out.append("")

    for bloque in reporte["archivos"]:
        v = bloque["version_utilizada"]
        val = bloque["validacion_hojas"]
        out.append(f"## Archivo: {bloque['archivo']}")
        out.append("")
        out.append(
            _tabla(
                [
                    ["Nombre en origen", v["nombre_archivo"]],
                    ["Drive file id", v["drive_file_id"] or "(archivo local)"],
                    ["Modificado", v["modified_time"] or "-"],
                    ["Tamano (bytes)", _fmt_miles(v["size_bytes"] or 0)],
                    ["md5 (Drive)", v["md5_checksum_drive"] or "-"],
                    ["sha256 (local)", v["sha256_local"][:16] + "..."],
                    ["Descargado", v["fecha_descarga"]],
                    ["Origen", v["origen"]],
                ],
                ["Dato", "Valor"],
            )
        )
        out.append("")
        marca = "OK" if val["cumple"] else "REVISAR"
        out.append(f"**Validacion de hojas [{marca}]:** {val['mensaje']}")
        out.append("")
        out.append(
            _tabla(
                [
                    [
                        h["nombre"],
                        _fmt_miles(h["filas_datos"]),
                        h["columnas"],
                        (h["fila_encabezado"] + 1) if h["fila_encabezado"] is not None else "-",
                        sum(1 for c in h["columnas_detalle"] if c["candidata_orden"]),
                        sum(1 for c in h["columnas_detalle"] if c["candidata_monetaria"]),
                    ]
                    for h in bloque["hojas"]
                ],
                ["Hoja", "Filas", "Columnas", "Fila encabezado", "Cand. orden", "Cand. monetarias"],
            )
        )
        out.append("")

        for h in bloque["hojas"]:
            out.append(f"### Hoja: {h['nombre']}")
            out.append("")
            if not h["columnas_detalle"]:
                out.append("_Sin datos legibles._")
                out.append("")
                continue
            filas = []
            for c in h["columnas_detalle"]:
                marcas = []
                if c["candidata_orden"]:
                    marcas.append(f"orden ({c['candidata_orden']['puntaje']})")
                if c["candidata_monetaria"]:
                    marcas.append(f"monetaria ({c['candidata_monetaria']['puntaje']})")
                if c["candidata_cantidad"]:
                    marcas.append("cantidad")
                if c["candidata_fecha"]:
                    marcas.append("fecha")
                filas.append(
                    [
                        c["posicion"],
                        c["nombre"],
                        c["tipo_inferido"],
                        f"{c['porcentaje_nulos']}%",
                        _fmt_miles(c["valores_unicos"]),
                        ", ".join(marcas) or "-",
                        "; ".join(c["ejemplos"][:3]).replace("|", "/")[:70],
                    ]
                )
            out.append(
                _tabla(filas, ["#", "Columna", "Tipo", "% nulos", "Unicos", "Candidata a", "Ejemplos"])
            )
            out.append("")
            if h["observaciones"]:
                out.append("Observaciones de la hoja:")
                out.extend(f"- {o}" for o in h["observaciones"])
                out.append("")
            detalles_orden = [c for c in h["columnas_detalle"] if c["candidata_orden"]]
            if detalles_orden:
                out.append("Formatos encontrados en columnas candidatas a orden:")
                for c in detalles_orden:
                    f = c["candidata_orden"]["formatos_detectados"]
                    out.append(
                        f"- `{c['nombre']}`: solo digitos={f['solo_digitos']}, "
                        f"ceros a la izquierda={f['con_ceros_a_la_izquierda']}, "
                        f"sufijo .0={f['con_sufijo_decimal']}, con letras={f['con_letras']}, "
                        f"espacios sobrantes={f['con_espacios_sobrantes']}"
                    )
                out.append("")

        if bloque["inconsistencias_estructurales"]:
            out.append("### Inconsistencias estructurales entre hojas")
            out.append("")
            out.extend(f"- {i}" for i in bloque["inconsistencias_estructurales"])
            out.append("")

    return "\n".join(out)


def exportar(reporte: dict, directorio: Path, sello: str | None = None) -> dict[str, Path]:
    directorio.mkdir(parents=True, exist_ok=True)
    sello = sello or datetime.now().strftime("%Y%m%d_%H%M%S")

    ruta_json = directorio / f"reporte_estructural_{sello}.json"
    ruta_json.write_text(json.dumps(reporte, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    ruta_md = directorio / f"reporte_estructural_{sello}.md"
    ruta_md.write_text(a_markdown(reporte), encoding="utf-8")

    ruta_csv = directorio / f"columnas_{sello}.csv"
    with open(ruta_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "archivo", "hoja", "posicion", "columna", "tipo_inferido", "filas",
                "nulos", "porcentaje_nulos", "valores_unicos", "candidata_orden",
                "candidata_monetaria", "candidata_cantidad", "candidata_fecha",
                "ejemplos", "observaciones", "significado_confirmado_por_negocio",
            ]
        )
        for bloque in reporte["archivos"]:
            for h in bloque["hojas"]:
                for c in h["columnas_detalle"]:
                    w.writerow(
                        [
                            bloque["archivo"], h["nombre"], c["posicion"], c["nombre"],
                            c["tipo_inferido"], c["total"], c["nulos"], c["porcentaje_nulos"],
                            c["valores_unicos"],
                            c["candidata_orden"]["puntaje"] if c["candidata_orden"] else "",
                            c["candidata_monetaria"]["puntaje"] if c["candidata_monetaria"] else "",
                            "si" if c["candidata_cantidad"] else "",
                            "si" if c["candidata_fecha"] else "",
                            " | ".join(c["ejemplos"][:3]),
                            " | ".join(c["observaciones"]),
                            "",  # columna en blanco para que el negocio confirme
                        ]
                    )
    return {"json": ruta_json, "markdown": ruta_md, "csv": ruta_csv}
