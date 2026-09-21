"""CLI de la etapa exploratoria.

Uso tipico (con Google Drive configurado):
    python -m app.main explorar

Uso con archivos ya descargados (util para validar sin credenciales):
    python -m app.main explorar --repuestos-local data/Repuestos.xlsx \
                                --aseguradoras-local data/Aseguradoras.xlsx

Esta etapa NO conecta MariaDB y NO aplica reglas financieras.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from app.config.settings import ConfigError, DriveFileConfig, Settings, get_settings
from app.drive.client import (
    ArchivoDescargado,
    construir_servicio,
    descargar_archivo,
    registrar_archivo_local,
)
from app.exploration.profiling import perfilar_libro
from app.exploration.report import construir_reporte, exportar
from app.ingestion.workbook import leer_libro, validar_cantidad_hojas
from app.reconciliation import audit
from app.rules.situations import MARGEN_MINIMO, pesos, porcentaje

logger = logging.getLogger("ceser.auditoria")


def configurar_logging(verboso: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verboso else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _obtener_archivo(
    cfg: DriveFileConfig, settings: Settings, ruta_local: str | None, servicio_factory
) -> ArchivoDescargado:
    if ruta_local:
        logger.info("[%s] usando archivo local: %s", cfg.clave, ruta_local)
        return registrar_archivo_local(Path(ruta_local))

    if not cfg.file_id:
        raise ConfigError(
            f"No hay File ID configurado para {cfg.etiqueta}. "
            f"Defina GOOGLE_DRIVE_{cfg.clave.upper()}_FILE_ID o use --{cfg.clave}-local."
        )
    servicio = servicio_factory()
    destino = settings.data_dir / f"{cfg.clave}.xlsx"
    logger.info("[%s] descargando desde Drive (file_id=%s)", cfg.clave, cfg.file_id)
    return descargar_archivo(servicio, cfg.file_id, destino)


def comando_explorar(args) -> int:
    inicio = datetime.now()
    settings = get_settings()
    logger.info("Inicio de ejecucion exploratoria")
    logger.info("Modo de autenticacion Drive: %s", settings.modo_auth())

    _servicio: list = []

    def servicio_factory():
        if not _servicio:
            _servicio.append(construir_servicio(settings))
        return _servicio[0]

    entradas = [
        (settings.repuestos, args.repuestos_local),
        (settings.aseguradoras, args.aseguradoras_local),
    ]

    bloques = []
    hubo_hallazgos = False
    for cfg, ruta_local in entradas:
        archivo = _obtener_archivo(cfg, settings, ruta_local, servicio_factory)
        logger.info("[%s] leyendo hojas de %s", cfg.clave, archivo.ruta_local)
        libro = leer_libro(Path(archivo.ruta_local))
        validacion = validar_cantidad_hojas(libro, cfg.hojas_esperadas)
        if not validacion["cumple"]:
            hubo_hallazgos = True
            logger.warning("[%s] %s", cfg.clave, validacion["mensaje"])
        perfiles = perfilar_libro(libro)
        logger.info(
            "[%s] %s hojas, %s filas de datos en total",
            cfg.clave,
            len(perfiles),
            sum(p.filas_datos for p in perfiles),
        )
        bloques.append((cfg.etiqueta, archivo, validacion, perfiles))

    fecha = inicio.isoformat(timespec="seconds")
    reporte = construir_reporte(fecha, bloques)
    salida = Path(args.salida) if args.salida else settings.reports_dir
    rutas = exportar(reporte, salida, sello=inicio.strftime("%Y%m%d_%H%M%S"))

    _imprimir_resumen(reporte, rutas)
    duracion = (datetime.now() - inicio).total_seconds()
    logger.info("Fin de ejecucion exploratoria (%.2fs)", duracion)
    return 0 if not hubo_hallazgos else 0  # los hallazgos se reportan, no fallan la ejecucion


def _imprimir_resumen(reporte: dict, rutas: dict[str, Path]) -> None:
    print()
    print("=" * 62)
    print("EXPLORACION ESTRUCTURAL - AUDITORIA CESER")
    print("=" * 62)
    for bloque in reporte["archivos"]:
        val = bloque["validacion_hojas"]
        res = bloque["resumen"]
        print()
        print(f"Archivo: {bloque['archivo']}  ({bloque['version_utilizada']['nombre_archivo']})")
        print(f"  Hojas: {res['hojas']}  ->  {'OK' if val['cumple'] else 'REVISAR: ' + val['mensaje']}")
        print(f"  Filas de datos: {res['filas_datos_totales']:,}".replace(",", "."))
        for hoja in bloque["hojas"]:
            cand_orden = [c["nombre"] for c in hoja["columnas_detalle"] if c["candidata_orden"]]
            cand_dinero = [c["nombre"] for c in hoja["columnas_detalle"] if c["candidata_monetaria"]]
            print(f"    - {hoja['nombre']}: {hoja['filas_datos']} filas x {hoja['columnas']} columnas")
            print(f"        posible orden    : {', '.join(cand_orden) or '(ninguna identificada)'}")
            print(f"        posible monetaria: {', '.join(cand_dinero) or '(ninguna identificada)'}")
        for inc in bloque["inconsistencias_estructurales"]:
            print(f"  ! {inc}")
    print()
    print("Reportes generados:")
    for clave, ruta in rutas.items():
        print(f"  {clave:8s} {ruta}")
    print()


def comando_conciliar(args) -> int:
    inicio = datetime.now()
    settings = get_settings()
    logger.info("Inicio de conciliacion")

    _servicio: list = []

    def servicio_factory():
        if not _servicio:
            _servicio.append(construir_servicio(settings))
        return _servicio[0]

    archivo_rep = _obtener_archivo(settings.repuestos, settings, args.repuestos_local, servicio_factory)
    archivo_ase = _obtener_archivo(settings.aseguradoras, settings, args.aseguradoras_local, servicio_factory)

    libro_rep = leer_libro(Path(archivo_rep.ruta_local))
    libro_ase = leer_libro(Path(archivo_ase.ruta_local))

    cliente_tech = None
    if not args.sin_tech and settings.tech.configurada:
        from app.database.tech import TechReadOnly, buscador_orden_asociada
        cliente_tech = TechReadOnly(settings.tech)
        logger.info("Conciliacion con datos de TECH: %s", settings.tech.describir())
    elif not args.sin_tech:
        logger.warning("TECH no esta configurado: la conciliacion usara solo los archivos")

    resultado = audit.ejecutar_auditoria(
        libro_rep, libro_ase, [archivo_rep, archivo_ase],
        cliente_tech=cliente_tech,
        desde_pagos=args.pagos_desde,
    )
    salida = Path(args.salida) if args.salida else settings.reports_dir
    rutas = audit.exportar(resultado, salida, sello=inicio.strftime("%Y%m%d_%H%M%S"))

    if args.informe:
        from app.reporting import informe
        rutas["informe"] = informe.generar(
            resultado, salida, audit.ETIQUETAS_SITUACION,
            verificaciones=VERIFICACIONES_REALIZADAS,
            sello=inicio.strftime("%Y%m%d_%H%M%S"),
            url_orden=settings.url_orden_tech,
        )

    if args.tabla:
        from app.reporting import tabla
        rutas["tabla"] = tabla.generar(resultado, salida,
                                       sello=inicio.strftime("%Y%m%d_%H%M%S"),
                                       url_orden=settings.url_orden_tech)

    if args.desglose:
        from app.reporting import desglose
        rutas.update(desglose.generar(resultado, cliente_tech, salida,
                                      sello=inicio.strftime("%Y%m%d_%H%M%S"),
                                      url_orden=settings.url_orden_tech))

    if args.revision_facturacion:
        from app.reconciliation import revision
        rutas.update(revision.generar(resultado, libro_ase, salida,
                                      sello=inicio.strftime("%Y%m%d_%H%M%S")))

    if not args.sin_base:
        from app.config.settings import describir_url
        from app.database import auditoria as base

        periodos = sorted({l.periodo for l in resultado.aseguradoras.lineas if l.periodo})
        engine = base.motor(settings.audit_db_url)
        base.crear_esquema(engine)
        corrida_id = base.guardar_corrida(
            engine, resultado, tech_consultado=cliente_tech is not None,
            cobertura=(periodos[0], periodos[-1]) if periodos else (None, None),
            gids=_gids_de_los_archivos(settings, _servicio),
        )
        logger.info("Corrida %s guardada en %s", corrida_id, describir_url(settings.audit_db_url))

    _imprimir_conciliacion(resultado, rutas, args.detalle)
    logger.info("Fin de conciliacion (%.2fs)", (datetime.now() - inicio).total_seconds())
    return 0


def _imprimir_conciliacion(resultado, rutas: dict[str, Path], detalle: int) -> None:
    r = resultado.resumen
    print()
    print("=" * 62)
    print("AUDITORIA DE ORDENES CESER")
    print("=" * 62)
    print(f"Ultimo analisis: {resultado.fecha_analisis}")
    print()
    print(f"  Ordenes analizadas : {r['ordenes_analizadas']:>6}")
    print(f"  Sin novedades      : {r['sin_novedades']:>6}")
    print(f"  Para revisar       : {r['para_revisar']:>6}")
    print(f"  Pendientes de TECH : {r['pendientes_de_revisar_con_tech']:>6}"
          "   (SAMSUNG, SUPER WEGA y ordenes fuera del periodo)")
    print()
    print("  Cruce de ordenes:")
    for tipo, cant in sorted(r["por_tipo_coincidencia"].items(), key=lambda x: -x[1]):
        print(f"    {tipo:20s} {cant:>6}")
    print()
    print("  Situaciones encontradas:")
    for tipo, cant in sorted(r["por_situacion"].items(), key=lambda x: -x[1]):
        print(f"    {audit.etiqueta(tipo):38s} {cant:>6}")
    if r["sin_aseguradora_en_periodo_por_hoja"]:
        print()
        print("  Ordenes sin contraparte de aseguradora, por hoja de origen:")
        for hoja, cant in sorted(r["sin_aseguradora_en_periodo_por_hoja"].items(), key=lambda x: -x[1]):
            print(f"    {hoja:38s} {cant:>6}")
    print()
    print(f"  Lineas leidas: repuestos {r['lineas_repuestos']}, aseguradoras {r['lineas_aseguradoras']}")
    print(f"  Filas descartadas por numero de orden invalido: "
          f"{r['descartes_repuestos'] + r['descartes_aseguradoras']}")

    if detalle:
        revisar = [f for f in resultado.fotografias if f.requiere_revision]
        revisar.sort(key=lambda f: (f.margen_esperado is None, f.margen_esperado))
        print()
        print(f"  SITUACIONES PARA REVISAR (primeras {min(detalle, len(revisar))} de {len(revisar)})")
        for f in revisar[:detalle]:
            print()
            print(f"    Orden {f.orden_ceser}   aseguradora: {f.orden_aseguradora or '-'}   "
                  f"({f.hojas_repuestos or 'sin repuestos'} / {f.hojas_aseguradora or 'sin aseguradora'})")
            print(f"      costo {pesos(f.costo_total_repuestos):>14}   "
                  f"valor {pesos(f.valor_aseguradora):>14}   margen {porcentaje(f.margen_esperado):>9}")
            for s in f.situaciones:
                print(f"      - {s.descripcion}")
    print()
    print("Archivos generados:")
    for clave, ruta in rutas.items():
        print(f"  {clave:10s} {ruta}")
    print()


VERIFICACIONES_REALIZADAS = [
    "Los numeros de orden se normalizan antes de comparar: 010582, 10582.0 y "
    "\"10582 \" se tratan como la misma orden.",
    "Una celda vacia no se cuenta como cero: se distingue entre no hay dato, "
    "el valor es cero y el registro no existe.",
    "Antes de señalar que falta un cobro se verifica si esta en otra orden "
    "vinculada, en otra orden con el mismo equipo o en el flujo de cambio.",
    "Las ordenes clasificadas como dano total u objetado no se señalan por no "
    "cobrar repuestos, porque en esos casos no corresponde cobrarlos.",
    "Las ordenes del mes en curso sin factura emitida no se señalan: estan "
    "pendientes de facturar.",
    "Solo se comparan periodos que ambas fuentes cubren; el historico anterior "
    "queda fuera del alcance.",
]


def comando_clave(args) -> int:
    """Genera el hash de una contraseña para WEB_USUARIOS. No guarda la contraseña."""
    import getpass
    from werkzeug.security import generate_password_hash

    usuario = args.usuario or input("Usuario: ").strip()
    clave = getpass.getpass("Contraseña: ")
    if len(clave) < 10:
        print("La contraseña debe tener al menos 10 caracteres.")
        return 2
    if clave != getpass.getpass("Repita la contraseña: "):
        print("Las contraseñas no coinciden.")
        return 2
    print()
    print("Agregue esta linea a WEB_USUARIOS en el .env del servidor")
    print("(separe varios usuarios con ; ):")
    print()
    print(f"{usuario}:{generate_password_hash(clave)}")
    return 0


def _gids_de_los_archivos(settings, servicio_cacheado: list) -> dict:
    """Identificador de cada pestaña de los dos Excel, para enlazar a la fila exacta.

    Solo se consigue cuando se trabaja contra Drive; con archivos locales no hay
    a que enlazar y los reportes caen al enlace del archivo completo.
    """
    if not servicio_cacheado:
        return {}
    from app.drive.client import gids_de_hojas

    credenciales = getattr(servicio_cacheado[0], "_credenciales_ceser", None)
    if credenciales is None:
        return {}
    gids = {}
    for cfg in settings.archivos:
        if cfg.file_id:
            encontrados = gids_de_hojas(credenciales, cfg.file_id)
            if encontrados:
                gids[cfg.clave] = encontrados
    return gids


def comando_probar_drive(args) -> int:
    """Verifica credenciales y acceso a los dos archivos, sin descargarlos."""
    from app.drive.client import explicar_error_drive, leer_service_account, obtener_metadata

    settings = get_settings()
    print()
    print("=" * 62)
    print("ACCESO A GOOGLE DRIVE (solo lectura)")
    print("=" * 62)

    modo = settings.modo_auth()
    if modo == "ninguno":
        print("  Sin credenciales. Defina GOOGLE_SERVICE_ACCOUNT_FILE en .env.")
        return 2
    print(f"  Modo: {modo}")

    client_email = project_id = None
    if modo == "service_account":
        cuenta = leer_service_account(Path(settings.service_account_file))
        client_email, project_id = cuenta["client_email"], cuenta["project_id"]
        print(f"  Archivo:  {settings.service_account_file}")
        print(f"  Proyecto: {project_id}")
        print(f"  Cuenta:   {client_email}")
        print(f"            ^ los dos archivos deben estar compartidos con este correo")

    try:
        servicio = construir_servicio(settings)
    except Exception as exc:  # credenciales rechazadas antes de tocar un archivo
        print(f"\n  NO SE PUDO AUTENTICAR\n  {explicar_error_drive(exc, client_email, project_id)}")
        return 1

    fallos = 0
    for cfg in settings.archivos:
        print()
        print(f"  [{cfg.clave}]  File ID {cfg.file_id}")
        if not cfg.file_id:
            print("    FALTA el File ID en .env")
            fallos += 1
            continue
        try:
            meta = obtener_metadata(servicio, cfg.file_id)
        except Exception as exc:
            print(f"    SIN ACCESO: {explicar_error_drive(exc, client_email, project_id)}")
            fallos += 1
            continue
        tipo = ("Google Sheet (se exporta a XLSX)"
                if meta.get("mimeType") == "application/vnd.google-apps.spreadsheet" else "XLSX")
        tamano = f"{int(meta['size']) / 1024:,.0f} KB" if meta.get("size") else "—"
        print(f"    OK  {meta.get('name')}")
        print(f"        tipo {tipo} · {tamano} · modificado {meta.get('modifiedTime')}")

    print()
    if fallos:
        print(f"  {fallos} de {len(settings.archivos)} archivos sin acceso.")
        return 1
    print("  Todo listo. Siguiente paso: python -m app.main explorar")
    return 0


def comando_probar_tech(args) -> int:
    """Diagnostico de la conexion a TECH y muestra de los datos reales."""
    from app.database.tech import TechReadOnly

    settings = get_settings()
    cliente = TechReadOnly(settings.tech)

    print()
    print("=" * 62)
    print("CONEXION A TECH (solo lectura)")
    print("=" * 62)
    print(f"  Destino: {settings.tech.describir()}")

    info = cliente.probar()
    print(f"  Version: {info['version']}")
    print(f"  Usuario: {info['usuario']}   Base: {info['base']}")
    print(f"  Hora del servidor: {info['ahora']}")
    print(f"  Permisos: {'SOLO LECTURA' if info['solo_lectura'] else 'ATENCION: tiene permisos de escritura'}")
    for permiso in info["permisos"]:
        print(f"    {permiso}")

    if not args.muestra:
        return 0

    print()
    print("-" * 62)
    print("MUESTRA DE DATOS")
    print("-" * 62)

    consultas = [
        # Ordenar por la clave unica usa el indice; ordenar por fecha puede
        # obligar a recorrer la tabla completa, y `ordenes` es MyISAM: un
        # escaneo bloquea las escrituras de TECH.
        ("Ultimas ordenes", """
            SELECT ORDEN, PREFIJO_ORDEN, ORDEN_ANTERIOR, ORDEN_FABRICANTE, ESTADO,
                   VALOR_FAC, IVA_FAC, VR_PARTES, VALOR_LABOR, COBRADA, FECHA_INGRESO
            FROM ordenes ORDER BY ORDEN DESC LIMIT 5"""),
        ("Ordenes de la auditoria", """
            SELECT ORDEN, ESTADO, VALOR_FAC, IVA_FAC, VR_PARTES, VALOR_LABOR, ORDEN_FABRICANTE
            FROM ordenes WHERE ORDEN IN (1017550, 1017621, 1017811, 12788905, 11953441)"""),
        ("productos_seguro", """
            SELECT CONSECUTIVO_M, PREFIJO_CONSECUTIVO_M, NRO_SINIESTRO_M, ASEGURADORA_M,
                   ESTADO_SEGUIMIENTO_M, FECHA_INGRESO_M
            FROM productos_seguro ORDER BY FECHA_INGRESO_M DESC LIMIT 5"""),
        ("Abonos de esas ordenes", """
            SELECT ORDEN, ORDEN_2, FECHA, VALOR, TIPO_PAGO, RECIBO_NRO
            FROM abonos WHERE ORDEN IN (1017550, 1017621) ORDER BY FECHA LIMIT 10"""),
        # Sin GROUP BY sobre toda la tabla: se toman los estados de un rango
        # acotado por la clave unica.
        ("Estados en las ultimas 2.000 ordenes", """
            SELECT ESTADO, COUNT(*) AS ordenes FROM (
                SELECT ESTADO FROM ordenes ORDER BY ORDEN DESC LIMIT 2000
            ) AS ultimas GROUP BY ESTADO ORDER BY ordenes DESC LIMIT 15"""),
        ("Estructura de productos_seguro", "SHOW COLUMNS FROM productos_seguro"),
    ]

    for titulo, sql in consultas:
        print()
        print(f"### {titulo}")
        try:
            filas = cliente.consultar(sql)
        except Exception as exc:  # la estructura real puede diferir
            print(f"  ERROR: {exc}")
            continue
        if not filas:
            print("  (sin resultados)")
            continue
        columnas = list(filas[0].keys())
        print("  " + " | ".join(columnas))
        for fila in filas:
            print("  " + " | ".join("" if fila[c] is None else str(fila[c])[:22] for c in columnas))
    return 0


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="auditoria-ceser", description=__doc__)
    parser.add_argument("-v", "--verboso", action="store_true", help="log detallado")
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("explorar", aliases=["explore"], help="reporte estructural de los dos XLSX")
    p.add_argument("--repuestos-local", help="ruta a un Repuestos.xlsx ya descargado")
    p.add_argument("--aseguradoras-local", help="ruta a un Aseguradoras.xlsx ya descargado")
    p.add_argument("--salida", help="directorio de reportes (por defecto ./reportes)")
    p.set_defaults(func=comando_explorar)

    c = sub.add_parser("conciliar", help="cruza repuestos con aseguradoras y calcula margenes")
    c.add_argument("--repuestos-local", help="ruta a un Repuestos.xlsx ya descargado")
    c.add_argument("--aseguradoras-local", help="ruta a un Aseguradoras.xlsx ya descargado")
    c.add_argument("--salida", help="directorio de reportes (por defecto ./reportes)")
    c.add_argument("--detalle", type=int, default=0, metavar="N",
                   help="muestra las N ordenes con situaciones mas criticas")
    c.add_argument("--sin-base", action="store_true",
                   help="no guarda la corrida en la base de auditoria")
    c.add_argument("--sin-tech", action="store_true",
                   help="no consulta MariaDB: concilia solo con los archivos")
    c.add_argument("--informe", action="store_true",
                   help="genera un informe visual en HTML para presentar a gerencia")
    c.add_argument("--tabla", action="store_true",
                   help="genera la tabla de utilidad orden por orden en HTML")
    c.add_argument("--desglose", action="store_true",
                   help="genera el desglose orden por orden: proveedor, aseguradora y utilidad")
    c.add_argument("--revision-facturacion", action="store_true",
                   help="genera el archivo para revisar con el area las ordenes con "
                        "repuestos aprobados que no se cobraron")
    c.add_argument("--pagos-desde", default="2025-01-01", metavar="AAAA-MM-DD",
                   help="ignora abonos anteriores a esta fecha (numeros de orden reutilizados)")
    c.set_defaults(func=comando_conciliar)

    k = sub.add_parser("clave", help="genera el hash de una contraseña para la interfaz web")
    k.add_argument("usuario", nargs="?", help="nombre de usuario")
    k.set_defaults(func=comando_clave)

    d = sub.add_parser("probar-drive", help="verifica credenciales y acceso a los dos archivos de Drive")
    d.set_defaults(func=comando_probar_drive)

    t = sub.add_parser("probar-tech", help="verifica la conexion de solo lectura a TECH")
    t.add_argument("--muestra", action="store_true",
                   help="ademas del diagnostico, trae unas pocas filas de ejemplo")
    t.set_defaults(func=comando_probar_tech)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    configurar_logging(args.verboso)
    try:
        return args.func(args)
    except ConfigError as exc:
        logger.error("Configuracion: %s", exc)
        return 2
    except FileNotFoundError as exc:
        logger.error("Archivo no encontrado: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
