"""Interfaz de revision sobre la base de auditoria.

Muestra siempre la ultima corrida guardada por la tarea de la manana. No
recalcula nada: lee lo que la auditoria dejo escrito, de modo que lo que se ve
en pantalla es exactamente lo que quedo registrado.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from functools import wraps

from flask import (Flask, abort, flash, redirect, render_template, request, session, url_for)
from werkzeug.security import check_password_hash

from app.config.settings import Settings, get_settings
from app.database import auditoria as base
from app.web import consultas

logger = logging.getLogger(__name__)

# El panel resume la hoja del flujo definido con el area; al pinchar un mes se
# filtra la lista por la misma hoja, para que los numeros coincidan.
HOJA_DEL_PANEL = "HAROLD H.T"

INTENTOS_MAXIMOS = 8            # por usuario y ventana
VENTANA_SEGUNDOS = 300
_intentos: dict[str, list[float]] = {}


def _demasiados_intentos(quien: str) -> bool:
    ahora = time.time()
    recientes = [t for t in _intentos.get(quien, []) if ahora - t < VENTANA_SEGUNDOS]
    _intentos[quien] = recientes
    return len(recientes) >= INTENTOS_MAXIMOS


def _registrar_intento(quien: str) -> None:
    _intentos.setdefault(quien, []).append(time.time())


def pesos(valor) -> str:
    if valor is None:
        return "—"
    valor = Decimal(str(valor))
    entero = f"{abs(valor):,.0f}".replace(",", ".")
    return f"{'−' if valor < 0 else ''}${entero}"


MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio",
         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")


def mes_largo(periodo: str | None) -> str:
    """'2026-04' -> 'abril 2026'."""
    partes = str(periodo or "").split("-")
    if len(partes) != 2 or not partes[1].isdigit() or not 1 <= int(partes[1]) <= 12:
        return periodo or "—"
    return f"{MESES[int(partes[1]) - 1]} {partes[0]}"


def limites_del_mes(periodo: str) -> tuple[str, str]:
    """Primer y ultimo dia de 'AAAA-MM', para enlazar al filtro por fechas."""
    from calendar import monthrange
    anio, mes = int(periodo[:4]), int(periodo[5:7])
    return f"{periodo}-01", f"{periodo}-{monthrange(anio, mes)[1]:02d}"


def partes_de_ubicacion(ubicacion: str | None) -> list[tuple[str, int | None]]:
    """'HAROLD H.T fila 1806; SAMSUNG fila 12' -> [('HAROLD H.T', 1806), ('SAMSUNG', 12)]."""
    partes = []
    for trozo in str(ubicacion or "").split(";"):
        trozo = trozo.strip()
        if not trozo:
            continue
        hoja, separador, fila = trozo.rpartition(" fila ")
        if separador and fila.strip().isdigit():
            partes.append((hoja.strip(), int(fila.strip())))
        else:
            partes.append((trozo, None))
    return partes


def enlaces_a_la_hoja(url_archivo: str | None, ubicacion: str | None,
                      gids: dict | None) -> list[dict]:
    """Enlace a la pestaña y fila donde esta la orden dentro del Excel.

    Con el gid de la pestaña se llega a la celda exacta; sin el, al archivo.
    """
    if not url_archivo:
        return []
    # Los nombres no coinciden exactamente: la hoja se llama " HAROLD H.T", con
    # un espacio al inicio, y la ubicacion lo recorta. Se comparan normalizados.
    por_nombre = {str(k).strip().casefold(): v for k, v in (gids or {}).items()}
    enlaces = []
    for hoja, fila in partes_de_ubicacion(ubicacion):
        gid = por_nombre.get(hoja.strip().casefold())
        destino = url_archivo
        if gid is not None:
            destino += f"#gid={gid}" + (f"&range=A{fila}" if fila else "")
        enlaces.append({
            "texto": f"{hoja} fila {fila}" if fila else hoja,
            "url": destino,
            "exacto": gid is not None and fila is not None,
        })
    return enlaces


def porcentaje(valor) -> str:
    return "—" if valor is None else f"{Decimal(str(valor)):.2f}".replace(".", ",") + " %"


def crear_app(settings: Settings | None = None) -> Flask:
    settings = settings or get_settings()
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=settings.web_secret_key or "desarrollo-sin-secreto",
        SESSION_COOKIE_HTTPONLY=True,      # el navegador no deja leer la cookie por script
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=8 * 3600,
        USUARIOS=settings.web_usuarios,
    )
    app.jinja_env.globals["url_de_orden"] = settings.url_de_orden
    app.jinja_env.globals["url_del_archivo"] = settings.url_del_archivo
    app.jinja_env.globals["enlaces_a_la_hoja"] = enlaces_a_la_hoja
    motor = base.motor(settings.audit_db_url)
    # La interfaz consulta columnas que puede haber agregado una version nueva
    # del reporte. Asegurar el esquema aqui evita que la pantalla falle cuando
    # se despliega codigo nuevo antes de que corra la auditoria de la manana.
    try:
        base.crear_esquema(motor)
    except Exception:
        logger.exception("No se pudo verificar el esquema de la base de auditoria")
    app.jinja_env.filters["pesos"] = pesos
    app.jinja_env.filters["porcentaje"] = porcentaje
    app.jinja_env.filters["mes_largo"] = mes_largo

    def requiere_sesion(vista):
        @wraps(vista)
        def envoltura(*a, **kw):
            if not session.get("usuario"):
                return redirect(url_for("entrar", siguiente=request.path))
            return vista(*a, **kw)
        return envoltura

    @app.get("/entrar")
    def entrar():
        if session.get("usuario"):
            return redirect(url_for("panel"))
        return render_template("entrar.html")

    @app.post("/entrar")
    def entrar_post():
        usuario = (request.form.get("usuario") or "").strip()
        clave = request.form.get("clave") or ""
        if _demasiados_intentos(usuario or request.remote_addr or "?"):
            flash("Demasiados intentos. Espere unos minutos.")
            return render_template("entrar.html"), 429
        hash_guardado = app.config["USUARIOS"].get(usuario)
        if not hash_guardado or not check_password_hash(hash_guardado, clave):
            _registrar_intento(usuario or request.remote_addr or "?")
            logger.warning("Intento de ingreso fallido para %r desde %s", usuario, request.remote_addr)
            flash("Usuario o contraseña incorrectos.")
            return render_template("entrar.html"), 401
        session.clear()
        session["usuario"] = usuario
        session.permanent = True
        siguiente = request.args.get("siguiente") or ""
        return redirect(siguiente if siguiente.startswith("/") else url_for("panel"))

    @app.get("/salir")
    def salir():
        session.clear()
        return redirect(url_for("entrar"))

    def _corrida(con):
        corrida = consultas.ultima_corrida(con)
        if corrida is None:
            abort(503, "Todavia no hay ninguna corrida guardada.")
        return corrida

    @app.get("/")
    @requiere_sesion
    def panel():
        with motor.connect() as con:
            corrida = _corrida(con)
            return render_template(
                "panel.html", corrida=corrida,
                resumen=consultas.resumen(con, corrida["id"]),
                situaciones=consultas.conteo_situaciones(con, corrida["id"]),
                veredictos=consultas.conteo_veredictos(con, corrida["id"]),
                estados=consultas.conteo_estados(con, corrida["id"]),
                dinero=[dict(m, desde=limites_del_mes(m["mes"])[0],
                             hasta=limites_del_mes(m["mes"])[1])
                        for m in base.dinero_por_mes(motor, corrida["id"],
                                                     hoja_repuestos=HOJA_DEL_PANEL)],
                hoja_panel=HOJA_DEL_PANEL,
                VEREDICTOS=consultas.VEREDICTOS, NOMBRE_ESTADO=consultas.NOMBRE_ESTADO)

    @app.get("/ordenes")
    @requiere_sesion
    def lista_ordenes():
        veredicto = request.args.get("veredicto", "todas")
        estado = request.args.get("estado", "pendiente")
        buscar = (request.args.get("buscar") or "").strip()
        desde = (request.args.get("desde") or "").strip()
        hasta = (request.args.get("hasta") or "").strip()
        direccion = "asc" if request.args.get("dir") == "asc" else "desc"
        hoja = (request.args.get("hoja") or "").strip()
        atencion = "todas" if request.args.get("atencion") == "todas" else "si"
        try:
            pagina = max(1, int(request.args.get("pagina", 1)))
        except ValueError:
            pagina = 1
        with motor.connect() as con:
            corrida = _corrida(con)
            import json as _json
            gids = _json.loads(corrida["gids_hojas"]) if corrida["gids_hojas"] else {}
            filas, total = consultas.listar_ordenes(con, corrida["id"], veredicto, estado,
                                                    buscar, pagina, desde=desde, hasta=hasta,
                                                    direccion=direccion, hoja=hoja,
                                                    atencion=atencion)
            primera, ultima = consultas.rango_de_fechas(con, corrida["id"])
            paginas = max(1, -(-total // consultas.POR_PAGINA))
            detalle = consultas.situaciones_de(con, corrida["id"], [f["orden_ceser"] for f in filas])
            return render_template(
                "ordenes.html", corrida=corrida, filas=filas, detalle=detalle,
                veredicto=veredicto, estado=estado, buscar=buscar,
                desde=desde, hasta=hasta, primera=primera, ultima=ultima, direccion=direccion,
                hoja=hoja, atencion=atencion, gids=gids,
                cuenta_atencion=consultas.cuenta_por_atencion(con, corrida["id"]),
                pagina=min(pagina, paginas), paginas=paginas, total=total,
                veredictos=consultas.conteo_veredictos(con, corrida["id"]),
                estados=consultas.conteo_estados(con, corrida["id"]),
                VEREDICTOS=consultas.VEREDICTOS, NOMBRE_ESTADO=consultas.NOMBRE_ESTADO)

    @app.post("/revision")
    @requiere_sesion
    def marcar_revision():
        orden = (request.form.get("orden") or "").strip()
        estado = (request.form.get("estado") or "").strip()
        if not orden or estado not in base.ESTADOS_REVISION:
            abort(400, "Orden o estado invalido.")
        with motor.connect() as con:
            corrida = _corrida(con)
        base.registrar_revision(motor, orden, estado, por=session["usuario"],
                                nota=(request.form.get("nota") or "").strip() or None,
                                corrida_id=corrida["id"])
        destino = request.form.get("volver") or url_for("lista_ordenes")
        return redirect(destino if destino.startswith("/") else url_for("lista_ordenes"))

    @app.get("/historial")
    @requiere_sesion
    def ver_historial():
        with motor.connect() as con:
            return render_template("historial.html", corrida=_corrida(con),
                                   eventos=consultas.historial(con),
                                   NOMBRE_ESTADO=consultas.NOMBRE_ESTADO)

    @app.get("/salud")
    def salud():
        """Para comprobar desde fuera que la aplicacion responde."""
        with motor.connect() as con:
            corrida = consultas.ultima_corrida(con)
        return {"estado": "ok", "ultima_corrida": corrida["fecha_analisis"] if corrida else None}

    return app


app = None


def crear() -> Flask:      # punto de entrada de gunicorn: app.web.app:crear()
    return crear_app()
