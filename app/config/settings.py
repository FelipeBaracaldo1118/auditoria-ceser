"""Configuracion del sistema de auditoria CESER.

Toda la configuracion proviene de variables de entorno (opcionalmente
cargadas desde un archivo .env). No se debe hardcodear ninguna credencial.
"""

from __future__ import annotations

import re

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """Carga .env si python-dotenv esta disponible. Silencioso si no lo esta."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - depende del entorno
        return
    load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"La variable {name} debe ser un entero, se recibio: {raw!r}") from exc


class ConfigError(RuntimeError):
    """La configuracion requerida no esta disponible o es invalida."""


@dataclass(frozen=True)
class DriveFileConfig:
    """Un archivo XLSX en Google Drive y lo que esperamos encontrar dentro."""

    clave: str            # "repuestos" | "aseguradoras"
    etiqueta: str         # nombre legible para el usuario
    file_id: str | None   # ID de Drive (identificador primario, NO el nombre)
    hojas_esperadas: int


@dataclass(frozen=True)
class TechDBConfig:
    """Conexion de SOLO LECTURA a la base de TECH."""

    host: str | None
    port: int
    name: str | None
    user: str | None
    password: str | None
    connect_timeout: int = 10
    read_timeout: int = 60
    tamano_lote: int = 500          # ordenes por consulta; MyISAM bloquea por tabla

    @property
    def configurada(self) -> bool:
        return all([self.host, self.name, self.user, self.password])

    def url(self) -> str:
        if not self.configurada:
            raise ConfigError(
                "Faltan datos de conexion a TECH. Defina TECH_DB_HOST, TECH_DB_NAME, "
                "TECH_DB_USER y TECH_DB_PASSWORD en el archivo .env."
            )
        from urllib.parse import quote_plus
        return (
            f"mysql+pymysql://{quote_plus(self.user)}:{quote_plus(self.password)}"
            f"@{self.host}:{self.port}/{self.name}?charset=utf8mb4"
        )

    def describir(self) -> str:
        """Descripcion sin credenciales, apta para logs."""
        return f"{self.user}@{self.host}:{self.port}/{self.name}" if self.configurada else "(sin configurar)"


@dataclass(frozen=True)
class Settings:
    repuestos: DriveFileConfig
    aseguradoras: DriveFileConfig
    service_account_file: str | None
    oauth_client_secrets_file: str | None
    oauth_token_file: str
    data_dir: Path
    reports_dir: Path
    tech: TechDBConfig
    url_orden_tech: str | None   # plantilla con {orden}, para enlazar desde los reportes
    # Las ordenes que empiezan con ciertos prefijos se consultan en otra pagina
    # del sistema (confirmado por el area el 21/09/2026): son casi la mitad.
    url_orden_tech_alterna: str | None = None
    prefijos_orden_alterna: tuple[str, ...] = ()
    audit_db_url: str = ""       # base donde se guardan las corridas (ver _url_base_auditoria)
    web_secret_key: str = ""     # firma las sesiones de la interfaz
    web_usuarios: dict = field(default_factory=dict)   # usuario -> hash de contraseña

    def url_del_archivo(self, clave: str) -> str | None:
        """Direccion del Excel en Drive, para ir a verificar una orden a mano."""
        for cfg in self.archivos:
            if cfg.clave == clave and cfg.file_id:
                return f"https://docs.google.com/spreadsheets/d/{cfg.file_id}/edit"
        return None

    def url_de_orden(self, orden: str | None) -> str | None:
        """Direccion donde se consulta una orden, segun su prefijo."""
        numero = str(orden or "").strip()
        if not numero:
            return None
        if self.url_orden_tech_alterna and numero.startswith(self.prefijos_orden_alterna):
            return self.url_orden_tech_alterna.format(orden=numero)
        return self.url_orden_tech.format(orden=numero) if self.url_orden_tech else None

    @property
    def archivos(self) -> list[DriveFileConfig]:
        return [self.repuestos, self.aseguradoras]

    def archivo(self, clave: str) -> DriveFileConfig:
        for cfg in self.archivos:
            if cfg.clave == clave:
                return cfg
        raise ConfigError(f"Archivo desconocido: {clave!r}")

    def modo_auth(self) -> str:
        """Devuelve 'service_account', 'oauth' o 'ninguno'."""
        if self.service_account_file:
            return "service_account"
        if self.oauth_client_secrets_file:
            return "oauth"
        return "ninguno"


# En Drive lo que se copia del navegador es la URL, no el identificador. Se
# acepta cualquiera de las dos formas para que pegar la URL no rompa nada.
_PATRONES_ID = (
    re.compile(r"/d/([a-zA-Z0-9_-]{20,})"),        # .../file/d/<id>/... y .../spreadsheets/d/<id>/...
    re.compile(r"[?&]id=([a-zA-Z0-9_-]{20,})"),    # .../open?id=<id>
)


def id_de_drive(valor: str | None) -> str | None:
    """Devuelve el File ID, venga suelto o dentro de una URL de Drive."""
    if not valor:
        return None
    valor = valor.strip()
    if not valor.startswith("http"):
        return valor
    for patron in _PATRONES_ID:
        encontrado = patron.search(valor)
        if encontrado:
            return encontrado.group(1)
    raise ConfigError(
        f"No se pudo extraer el File ID de la URL de Drive: {valor!r}. "
        "Pegue el identificador o una URL con la forma .../d/<id>/..."
    )


def _ruta_del_proyecto(valor: str | None) -> str | None:
    """Rutas relativas contra la raiz del proyecto, no contra el directorio actual.

    En el servidor el proceso lo arranca cron o systemd desde otro directorio;
    una ruta relativa al cwd dejaria de encontrar las credenciales.
    """
    if not valor:
        return None
    ruta = Path(valor).expanduser()
    return str(ruta if ruta.is_absolute() else BASE_DIR / ruta)


def _usuarios_web(valor: str | None) -> dict:
    """WEB_USUARIOS = "gerente:<hash>;felipe:<hash>". Nunca contraseñas en claro.

    El hash lo genera `python -m app.main clave`, que no guarda la contraseña.
    """
    usuarios = {}
    for parte in (valor or "").split(";"):
        parte = parte.strip()
        if not parte:
            continue
        if ":" not in parte:
            raise ConfigError(f"WEB_USUARIOS mal formado en {parte!r}: falta 'usuario:hash'")
        nombre, hash_clave = parte.split(":", 1)
        usuarios[nombre.strip()] = hash_clave.strip()
    return usuarios


def _url_base_auditoria(data_dir: Path) -> str:
    """AUDIT_DB_URL si esta; si no, MariaDB con AUDIT_DB_*; si no, SQLite en data/.

    El SQLite no exige montar nada en el servidor. Nunca se escribe en TECH.
    """
    if _env("AUDIT_DB_URL"):
        return _env("AUDIT_DB_URL")
    if _env("AUDIT_DB_HOST"):
        from urllib.parse import quote_plus
        return (f"mysql+pymysql://{quote_plus(_env('AUDIT_DB_USER') or '')}:"
                f"{quote_plus(_env('AUDIT_DB_PASSWORD') or '')}@{_env('AUDIT_DB_HOST')}:"
                f"{_env_int('AUDIT_DB_PORT', 3306)}/{_env('AUDIT_DB_NAME')}?charset=utf8mb4")
    return f"sqlite:///{data_dir / 'auditoria.db'}"


def describir_url(url: str) -> str:
    """La URL sin la contraseña, para mostrarla en pantalla o en logs."""
    import re as _re
    return _re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", url)


def get_settings() -> Settings:
    _load_dotenv()

    data_dir = Path(_env("DATA_DIR") or (BASE_DIR / "data"))
    reports_dir = Path(_env("REPORTS_DIR") or (BASE_DIR / "reportes"))

    return Settings(
        repuestos=DriveFileConfig(
            clave="repuestos",
            etiqueta="Repuestos.xlsx",
            file_id=id_de_drive(_env("GOOGLE_DRIVE_REPUESTOS_FILE_ID")),
            hojas_esperadas=_env_int("REPUESTOS_HOJAS_ESPERADAS", 4),
        ),
        aseguradoras=DriveFileConfig(
            clave="aseguradoras",
            etiqueta="Aseguradoras.xlsx",
            file_id=id_de_drive(_env("GOOGLE_DRIVE_ASEGURADORAS_FILE_ID")),
            hojas_esperadas=_env_int("ASEGURADORAS_HOJAS_ESPERADAS", 6),
        ),
        service_account_file=_ruta_del_proyecto(_env("GOOGLE_SERVICE_ACCOUNT_FILE")),
        oauth_client_secrets_file=_env("GOOGLE_OAUTH_CLIENT_SECRETS_FILE"),
        oauth_token_file=_env("GOOGLE_OAUTH_TOKEN_FILE") or str(BASE_DIR / ".secrets" / "token.json"),
        data_dir=data_dir,
        reports_dir=reports_dir,
        url_orden_tech=_env("TECH_APP_URL_ORDEN"),
        url_orden_tech_alterna=_env("TECH_APP_URL_ORDEN_ALTERNA"),
        prefijos_orden_alterna=tuple(
            p.strip() for p in (_env("TECH_PREFIJOS_ORDEN_ALTERNA") or "").split(",") if p.strip()),
        audit_db_url=_url_base_auditoria(data_dir),
        web_secret_key=_env("WEB_SECRET_KEY") or "",
        web_usuarios=_usuarios_web(_env("WEB_USUARIOS")),
        tech=TechDBConfig(
            host=_env("TECH_DB_HOST"),
            port=_env_int("TECH_DB_PORT", 3306),
            name=_env("TECH_DB_NAME"),
            user=_env("TECH_DB_USER"),
            password=_env("TECH_DB_PASSWORD"),
            connect_timeout=_env_int("TECH_DB_CONNECT_TIMEOUT", 10),
            read_timeout=_env_int("TECH_DB_READ_TIMEOUT", 60),
            tamano_lote=_env_int("TECH_DB_TAMANO_LOTE", 500),
        ),
    )
