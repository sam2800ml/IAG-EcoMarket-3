"""Pruebas del router: la ruta siempre la decide el modelo, sin una regla de respaldo."""

import json

import pytest
from langchain_core.messages import AIMessage

from src.agente.router import decidir_con_modelo
from src.llm import ErrorModelo


class _ModeloFijo:
    """Modelo de prueba: responde la ruta que se le indique, o lanza el error dado."""

    def __init__(self, ruta: str | None = None, error: Exception | None = None):
        self._ruta, self._error = ruta, error

    def invoke(self, _mensajes):
        if self._error:
            raise self._error
        return AIMessage(json.dumps({"ruta": self._ruta}))


@pytest.mark.parametrize("ruta_modelo, esperada", [
    ("informacion", "rag"),
    ("agente", "agente"),
    ("humano", "escalamiento"),
    ("directo", "directo"),
])
def test_mapea_la_ruta_del_modelo(ruta_modelo, esperada):
    ruta, motivo = decidir_con_modelo(_ModeloFijo(ruta_modelo), "mensaje", "")
    assert ruta == esperada
    assert motivo == f"modelo: {ruta_modelo}"


def test_ruta_no_valida_lanza_error_sin_regla_de_respaldo():
    with pytest.raises(ErrorModelo):
        decidir_con_modelo(_ModeloFijo(ruta="algo_que_no_existe"), "mensaje", "")


def test_fallo_de_conexion_se_traduce_y_se_propaga():
    with pytest.raises(ErrorModelo):
        decidir_con_modelo(_ModeloFijo(error=ConnectionError("Connection refused")), "mensaje", "")
