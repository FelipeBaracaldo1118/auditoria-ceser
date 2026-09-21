"""Cliente de Google Drive: autenticacion, metadatos y descarga por File ID.

La autenticacion se mantiene separada de la logica de procesamiento: este
modulo solo sabe traer bytes y metadatos desde Drive.

Los archivos se identifican SIEMPRE por file_id, nunca por nombre.
"""

from __future__ import annotations

import hashlib
import json
import io
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config.settings import ConfigError, Settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly",
          # Solo para saber el identificador de cada pestaña y poder enlazar a
          # la fila exacta; tambien es de solo lectura.
          "https://www.googleapis.com/auth/spreadsheets.readonly"]

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"

CAMPOS_METADATA = "id,name,mimeType,modifiedTime,size,md5Checksum,version,webViewLink"


@dataclass(frozen=True)
class ArchivoDescargado:
    """Version concreta de un archivo usada por un analisis."""

    drive_file_id: str | None
    nombre_archivo: str
    mime_type: str | None
    modified_time: str | None
    size_bytes: int | None
    md5_checksum_drive: str | None
    sha256_local: str
    fecha_descarga: str
    ruta_local: str
    origen: str  # "drive" | "local"

    def as_dict(self) -> dict:
        return {
            "drive_file_id": self.drive_file_id,
            "nombre_archivo": self.nombre_archivo,
            "mime_type": self.mime_type,
            "modified_time": self.modified_time,
            "size_bytes": self.size_bytes,
            "md5_checksum_drive": self.md5_checksum_drive,
            "sha256_local": self.sha256_local,
            "fecha_descarga": self.fecha_descarga,
            "ruta_local": self.ruta_local,
            "origen": self.origen,
        }


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_de_archivo(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for bloque in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def construir_servicio(settings: Settings):
    """Crea el servicio de Drive segun el modo de autenticacion configurado."""
    modo = settings.modo_auth()
    if modo == "ninguno":
        raise ConfigError(
            "No hay credenciales de Google Drive configuradas. "
            "Defina GOOGLE_SERVICE_ACCOUNT_FILE (produccion) o "
            "GOOGLE_OAUTH_CLIENT_SECRETS_FILE (desarrollo local)."
        )

    try:
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise ConfigError(
            "Falta la dependencia google-api-python-client. Ejecute: pip install -r requirements.txt"
        ) from exc

    if modo == "service_account":
        from google.oauth2 import service_account

        ruta = Path(settings.service_account_file)  # type: ignore[arg-type]
        if not ruta.exists():
            raise ConfigError(f"No existe el archivo de service account: {ruta}")
        creds = service_account.Credentials.from_service_account_file(str(ruta), scopes=SCOPES)
        logger.info("Autenticacion Drive: service account (%s)", ruta.name)
    else:
        creds = _credenciales_oauth(settings)
        logger.info("Autenticacion Drive: OAuth local")

    servicio = build("drive", "v3", credentials=creds, cache_discovery=False)
    servicio._credenciales_ceser = creds     # se reutilizan para la API de Hojas
    return servicio


def _credenciales_oauth(settings: Settings):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = Path(settings.oauth_token_file)
    secrets_path = Path(settings.oauth_client_secrets_file)  # type: ignore[arg-type]
    if not secrets_path.exists():
        raise ConfigError(f"No existe el archivo de client secrets OAuth: {secrets_path}")

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
        creds = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    token_path.chmod(0o600)
    return creds


def obtener_metadata(servicio, file_id: str) -> dict:
    return (
        servicio.files()
        .get(fileId=file_id, fields=CAMPOS_METADATA, supportsAllDrives=True)
        .execute()
    )


def descargar_archivo(servicio, file_id: str, destino: Path) -> ArchivoDescargado:
    """Descarga la version actual del archivo. Nunca escribe en Drive."""
    from googleapiclient.http import MediaIoBaseDownload

    meta = obtener_metadata(servicio, file_id)
    mime = meta.get("mimeType")

    if mime == GOOGLE_SHEET_MIME:
        # El archivo vive como Google Sheet: se exporta a XLSX para leerlo igual.
        peticion = servicio.files().export_media(fileId=file_id, mimeType=XLSX_MIME)
        logger.info("Archivo %s es Google Sheet, se exporta a XLSX", file_id)
    else:
        peticion = servicio.files().get_media(fileId=file_id, supportsAllDrives=True)

    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, peticion)
    hecho = False
    while not hecho:
        _, hecho = downloader.next_chunk()

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(buffer.getvalue())
    logger.info("Descargado %s -> %s (%s bytes)", file_id, destino, destino.stat().st_size)

    return ArchivoDescargado(
        drive_file_id=meta.get("id"),
        nombre_archivo=meta.get("name") or destino.name,
        mime_type=mime,
        modified_time=meta.get("modifiedTime"),
        size_bytes=destino.stat().st_size,
        md5_checksum_drive=meta.get("md5Checksum"),
        sha256_local=sha256_de_archivo(destino),
        fecha_descarga=_ahora_iso(),
        ruta_local=str(destino),
        origen="drive",
    )


def registrar_archivo_local(path: Path) -> ArchivoDescargado:
    """Registra un XLSX ya presente en disco con la misma trazabilidad."""
    path = path.resolve()
    if not path.exists():
        raise ConfigError(f"No existe el archivo local: {path}")
    stat = path.stat()
    return ArchivoDescargado(
        drive_file_id=None,
        nombre_archivo=path.name,
        mime_type=XLSX_MIME,
        modified_time=datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        .astimezone()
        .isoformat(timespec="seconds"),
        size_bytes=stat.st_size,
        md5_checksum_drive=None,
        sha256_local=sha256_de_archivo(path),
        fecha_descarga=_ahora_iso(),
        ruta_local=str(path),
        origen="local",
    )


def leer_service_account(ruta: Path) -> dict:
    """Lee el JSON y valida que sea de una cuenta de servicio, sin autenticar todavia."""
    try:
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"No existe el archivo de service account: {ruta}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ConfigError(f"El archivo {ruta} no es un JSON valido. Vuelva a descargar la clave.")
    if datos.get("type") != "service_account":
        raise ConfigError(
            f"El archivo {Path(ruta).name} es de tipo '{datos.get('type')}', no una cuenta de "
            "servicio. En la consola: Credenciales > Cuentas de servicio > Claves > Crear clave JSON."
        )
    faltan = [c for c in ("client_email", "private_key", "project_id") if not datos.get(c)]
    if faltan:
        raise ConfigError(f"Al JSON de la cuenta de servicio le faltan campos: {', '.join(faltan)}")
    return datos


def explicar_error_drive(exc: Exception, client_email: str | None = None,
                         project_id: str | None = None) -> str:
    """Convierte un error de la API en la accion concreta que lo resuelve.

    El caso mas frecuente, un archivo no compartido con la cuenta de servicio,
    llega como un 404 'File not found' que no dice nada util por si solo.
    """
    quien = client_email or "el correo de la cuenta de servicio (client_email del JSON)"
    texto = str(exc)
    contenido = getattr(exc, "content", b"")
    if isinstance(contenido, bytes):
        contenido = contenido.decode("utf-8", "replace")
    todo = f"{texto} {contenido}"
    estado = getattr(getattr(exc, "resp", None), "status", None)

    if "accessNotConfigured" in todo or "SERVICE_DISABLED" in todo or "has not been used in project" in todo:
        enlace = ("https://console.cloud.google.com/apis/library/drive.googleapis.com"
                  + (f"?project={project_id}" if project_id else ""))
        return f"La Google Drive API no esta habilitada en el proyecto. Habilitela aqui: {enlace}"
    if estado == 404:
        return (f"Drive no encuentra el archivo para esta cuenta. Casi siempre es que no esta "
                f"compartido: compartalo como Lector con {quien}. Si esta en una unidad "
                f"compartida, agregue ese correo como miembro de la unidad.")
    if estado == 403:
        return (f"La cuenta {quien} ve el archivo pero no tiene permiso para descargarlo. "
                f"Compartalo como Lector y revise que el propietario no haya bloqueado la descarga.")
    if estado == 401 or "invalid_grant" in todo:
        return ("Google rechazo las credenciales: la clave pudo ser eliminada en la consola, o el "
                "reloj de este equipo esta desfasado. Genere una clave nueva o sincronice la hora.")
    return f"Error inesperado de Drive: {texto[:300]}"


def gids_de_hojas(credenciales, file_id: str) -> dict[str, int]:
    """Identificador de cada pestaña, para enlazar a una hoja y fila concretas.

    Google le asigna un `gid` a cada pestaña incluso cuando el archivo es un
    XLSX abierto en Hojas de calculo. Sin esto solo se puede enlazar al archivo.
    Si la API no esta habilitada o el archivo no se puede leer asi, devuelve un
    diccionario vacio y los enlaces caen al archivo completo.
    """
    from googleapiclient.discovery import build

    try:
        api = build("sheets", "v4", credentials=credenciales, cache_discovery=False)
        meta = api.spreadsheets().get(
            spreadsheetId=file_id, fields="sheets.properties(sheetId,title)").execute()
    except Exception as exc:
        logger.warning("No se pudieron leer las pestañas de %s: %s", file_id, str(exc)[:120])
        return {}
    return {h["properties"]["title"]: h["properties"]["sheetId"] for h in meta.get("sheets", [])}
