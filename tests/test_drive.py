"""Configuracion de Google Drive: ruta de credenciales y errores entendibles."""

import json

import httplib2
import pytest
from googleapiclient.errors import HttpError

from app.config.settings import BASE_DIR, ConfigError, _ruta_del_proyecto
from app.drive.client import explicar_error_drive, leer_service_account

CORREO = "auditoria-ceser@proyecto.iam.gserviceaccount.com"


def _http_error(status, contenido=b"{}"):
    return HttpError(httplib2.Response({"status": status}), contenido)


def test_ruta_relativa_se_resuelve_contra_el_proyecto():
    """En el servidor el proceso arranca desde otro directorio."""
    assert _ruta_del_proyecto(".secrets/sa.json") == str(BASE_DIR / ".secrets" / "sa.json")
    assert _ruta_del_proyecto("/etc/ceser/sa.json") == "/etc/ceser/sa.json"
    assert _ruta_del_proyecto("") is None


def test_404_se_explica_como_archivo_no_compartido():
    texto = explicar_error_drive(_http_error(404), CORREO)
    assert "compartido" in texto and CORREO in texto


def test_api_deshabilitada_da_el_enlace_para_habilitarla():
    cuerpo = json.dumps({"error": {"errors": [{"reason": "accessNotConfigured"}]}}).encode()
    texto = explicar_error_drive(_http_error(403, cuerpo), CORREO, "mi-proyecto")
    assert "no esta habilitada" in texto
    assert "drive.googleapis.com?project=mi-proyecto" in texto


def test_403_sin_api_deshabilitada_es_permiso_del_archivo():
    texto = explicar_error_drive(_http_error(403), CORREO)
    assert "permiso" in texto and CORREO in texto


def test_credenciales_rechazadas():
    assert "clave" in explicar_error_drive(Exception("invalid_grant: account not found"))


def test_json_de_cuenta_de_servicio_valido(tmp_path):
    f = tmp_path / "sa.json"
    f.write_text(json.dumps({"type": "service_account", "client_email": CORREO,
                             "private_key": "x", "project_id": "p"}))
    assert leer_service_account(f)["client_email"] == CORREO


def test_json_de_oauth_se_rechaza_con_indicacion(tmp_path):
    """Error tipico: descargar el client secret de OAuth en vez de la clave."""
    f = tmp_path / "client_secret.json"
    f.write_text(json.dumps({"installed": {"client_id": "x"}}))
    with pytest.raises(ConfigError, match="no una cuenta de servicio"):
        leer_service_account(f)


def test_json_inexistente_o_corrupto(tmp_path):
    with pytest.raises(ConfigError, match="No existe"):
        leer_service_account(tmp_path / "nada.json")
    roto = tmp_path / "roto.json"
    roto.write_text("{no es json")
    with pytest.raises(ConfigError, match="JSON valido"):
        leer_service_account(roto)
