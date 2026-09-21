"""Pruebas del cliente de TECH. No requieren base de datos."""

from decimal import Decimal

import pytest

from app.config.settings import ConfigError, TechDBConfig
from app.database.tech import (
    ConsultaNoPermitida,
    TechReadOnly,
    _clave_orden,
    _parametros_lote,
    _validar,
    buscador_orden_asociada,
)


def _config(**kwargs) -> TechDBConfig:
    base = dict(host="10.0.0.1", port=3306, name="ceser", user="auditoria_ro", password="x")
    base.update(kwargs)
    return TechDBConfig(**base)


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select ORDEN from ordenes where ORDEN = :o",
    "SHOW GRANTS FOR CURRENT_USER()",
])
def test_consultas_de_lectura_se_permiten(sql):
    assert _validar(sql) == sql


@pytest.mark.parametrize("sql", [
    "UPDATE ordenes SET ESTADO = 'X'",
    "DELETE FROM abonos",
    "DROP TABLE ordenes",
    "INSERT INTO abonos VALUES (1)",
    "TRUNCATE TABLE ordenes",
    "ALTER TABLE ordenes ADD INDEX idx (ORDEN)",
    "SELECT 1; DROP TABLE ordenes",
])
def test_sentencias_de_escritura_se_rechazan(sql):
    with pytest.raises(ConsultaNoPermitida):
        _validar(sql)


def test_la_url_no_se_arma_sin_credenciales():
    with pytest.raises(ConfigError):
        _config(password=None).url()


def test_la_descripcion_no_expone_la_clave():
    descripcion = _config(password="secreta").describir()
    assert "secreta" not in descripcion
    assert "auditoria_ro@10.0.0.1:3306/ceser" == descripcion


def test_clave_de_orden_compatible_con_el_excel():
    assert _clave_orden(1017550) == "1017550"
    assert _clave_orden("01017550") == "1017550"
    assert _clave_orden("1017550.0") == "1017550"
    assert _clave_orden(" 1017550 ") == "1017550"


def test_los_valores_van_como_parametros_no_dentro_del_sql():
    marcadores, parametros = _parametros_lote(["1017550", "12788905"])
    assert marcadores == ":o0, :o1"
    assert parametros == {"o0": 1017550, "o1": 12788905}
    assert "1017550" not in marcadores


def test_las_ordenes_se_consultan_por_lotes():
    cliente = TechReadOnly(_config(tamano_lote=2))
    lotes = list(cliente._lotes(["3", "1", "2", "1", "5"]))
    assert lotes == [["1", "2"], ["3", "5"]]   # unicos y ordenados


class _ClienteFalso(TechReadOnly):
    """Cliente con respuestas fijas para probar la busqueda de orden asociada."""

    def __init__(self, seguros=None, ordenes=None):
        super().__init__(_config())
        self._seguros = seguros or {}
        self._ordenes = ordenes or {}
        self.llamadas = 0

    def obtener_seguro(self, numeros, columna_orden="CONSECUTIVO_M"):
        self.llamadas += 1
        return {n: self._seguros[n] for n in numeros if n in self._seguros}

    def obtener_ordenes(self, numeros):
        self.llamadas += 1
        return {n: self._ordenes[n] for n in numeros if n in self._ordenes}


class _Seguro:
    def __init__(self, nro_siniestro):
        self.nro_siniestro = nro_siniestro


class _Orden:
    def __init__(self, orden_fabricante):
        self.orden_fabricante = orden_fabricante


def test_orden_asociada_desde_el_numero_de_siniestro():
    cliente = _ClienteFalso(seguros={"1017550": _Seguro("60817")})
    assert buscador_orden_asociada(cliente)("1017550") == "60817"


def test_orden_asociada_cae_a_la_orden_de_fabricante():
    cliente = _ClienteFalso(ordenes={"11953441": _Orden("4175060498")})
    assert buscador_orden_asociada(cliente)("11953441") == "4175060498"


def test_sin_relacion_devuelve_none():
    assert buscador_orden_asociada(_ClienteFalso())("9999999") is None


def test_el_buscador_no_repite_consultas():
    cliente = _ClienteFalso(seguros={"1017550": _Seguro("60817")})
    buscar = buscador_orden_asociada(cliente)
    buscar("1017550"); buscar("1017550"); buscar("1017550")
    assert cliente.llamadas == 1


def test_el_historial_de_estados_no_escanea_la_tabla_por_accidente():
    """est_orden no tiene indice por ORDEN: 1,1 millones de filas y MyISAM."""
    cliente = TechReadOnly(_config())
    with pytest.raises(ConsultaNoPermitida) as exc:
        cliente.historial_estados("1017550")
    assert "indice" in str(exc.value)
