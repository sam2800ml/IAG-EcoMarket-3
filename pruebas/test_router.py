"""Pruebas de las reglas del router."""

import pytest

from src.agente.router import decidir_por_reglas


@pytest.mark.parametrize("mensaje, ruta_anterior, esperado", [
    ("Hola", None, "directo"),
    ("muchas gracias", None, "directo"),
    ("Quiero hablar con una persona", None, "escalamiento"),
    ("Me cobraron dos veces la compra", None, "escalamiento"),
    ("¿Cómo va mi pedido ECO-10005?", None, "agente"),
    ("Quiero devolver los cubiertos", None, "agente"),
    ("Quisiera devolver un shampoo, ¿cuáles son las políticas de devolución?", None, "rag"),
    ("¿Se puede devolver un champú?", None, "rag"),
    ("diego.torres@correo.example", "agente", "agente"),
    ("sí", "agente", "agente"),
    ("¿dónde está mi pedido?", None, "agente"),
])
def test_reglas(mensaje, ruta_anterior, esperado):
    assert decidir_por_reglas(mensaje, ruta_anterior)[0] == esperado


def test_sin_regla_decide_el_modelo():
    # Una pregunta informativa sin palabras clave no la decide ninguna regla.
    assert decidir_por_reglas("¿Cuánto cuesta el envío a Pasto?", None) == (None, "")
