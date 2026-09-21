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
    motor = base.motor(settings.audit_db_url)
    app.jinja_env.filters["pesos"] = pesos
    app.jinja_env.filters["porcentaje"] = porcentaje

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
                dinero=base.dinero_por_mes(motor, corrida["id"], hoja_repuestos="HAROLD H.T"),
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
        try:
            pagina = max(1, int(request.args.get("pagina", 1)))
        except ValueError:
            pagina = 1
        with motor.connect() as con:
            corrida = _corrida(con)
            filas, total = consultas.listar_ordenes(con, corrida["id"], veredicto, estado,
                                                    buscar, pagina, desde=desde, hasta=hasta,
                                                    direccion=direccion)
            primera, ultima = consultas.rango_de_fechas(con, corrida["id"])
            paginas = max(1, -(-total // consultas.POR_PAGINA))
            detalle = consultas.situaciones_de(con, corrida["id"], [f["orden_ceser"] for f in filas])
            return render_template(
                "ordenes.html", corrida=corrida, filas=filas, detalle=detalle,
                veredicto=veredicto, estado=estado, buscar=buscar,
                desde=desde, hasta=hasta, primera=primera, ultima=ultima, direccion=direccion,
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
