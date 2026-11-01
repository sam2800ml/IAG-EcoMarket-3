"""Pruebas de las herramientas puras (sin LLM): la lógica de negocio debe ser exacta."""

import pytest

from src import config
from src.herramientas.elegibilidad import clasificar_motivo, permite_etiqueta, verificar_elegibilidad
from src.herramientas.etiquetas import generar_etiqueta_devolucion
from src.herramientas.pedidos import consultar_pedido, normalizar_numero

CORREOS = {
    "ECO-10001": "laura.gomez@correo.example", "ECO-10002": "carlos.rodriguez@correo.example",
    "ECO-10004": "juan.perez@correo.example", "ECO-10006": "diego.torres@correo.example",
    "ECO-10007": "valentina.castro@correo.example", "ECO-10008": "mariana.lopez@correo.example",
    "ECO-10009": "andres.herrera@correo.example", "ECO-10012": "isabela.rojas@correo.example",
    "ECO-10013": "sebastian.ortiz@correo.example", "ECO-10014": "paula.jimenez@correo.example",
    "ECO-10015": "ricardo.salazar@correo.example", "ECO-10016": "natalia.ospina@correo.example",
}


@pytest.mark.parametrize("numero, producto, motivo, tipo, elegible, revision", [
    ("ECO-10006", "cubiertos", "no me gustó", "devolucion_estandar", True, False),
    ("ECO-10008", "café", "lo quería molido", "no_retornable", False, False),
    ("ECO-10004", "cepillo", "prefería otro color", "no_retornable", False, False),
    ("ECO-10012", "cepillo", "llegó con las cerdas dañadas", "defectuoso", True, True),
    ("ECO-10009", "mochila", "ya no la uso", "fuera_de_plazo", False, False),
    ("ECO-10015", "panel solar", "no lo necesito", "alto_valor", True, True),
    ("ECO-10015", "lámpara", "no la necesito", "devolucion_estandar", True, False),
    ("ECO-10016", None, "no la uso", "ya_registrada", True, False),
    ("ECO-10001", "botella", "me arrepentí", "cancelable", False, False),
    ("ECO-10002", None, "x", "no_entregado", False, False),
    ("ECO-10007", None, "x", "cancelado", False, False),
    ("ECO-10013", None, "x", "en_devolucion", False, False),
])
def test_reglas_de_elegibilidad(registro, numero, producto, motivo, tipo, elegible, revision):
    r = verificar_elegibilidad(numero, CORREOS[numero], producto, motivo)
    assert r["ok"], r
    assert (r["datos"]["tipo"], r["datos"]["elegible"], r["datos"]["requiere_revision_humana"]) == (tipo, elegible, revision)


@pytest.mark.parametrize("numero, correo, producto, codigo", [
    ("ECO-99999", "a@b.co", "x", "PEDIDO_NO_EXISTE"),
    ("ECO-10006", "otra.persona@correo.example", "cubiertos", "IDENTIDAD_NO_COINCIDE"),
    ("ECO-10006", None, "cubiertos", "DATOS_INCOMPLETOS"),
    ("ECO-10006", "diego.torres@correo.example", "televisor", "PRODUCTO_NO_EN_PEDIDO"),
    ("ECO-10008", "mariana.lopez@correo.example", None, "PRODUCTO_NO_ESPECIFICADO"),
    ("12345", "diego.torres@correo.example", "cubiertos", "FORMATO_INVALIDO"),
])
def test_errores_de_elegibilidad(registro, numero, correo, producto, codigo):
    r = verificar_elegibilidad(numero, correo, producto, "no me gustó")
    assert not r["ok"] and r["codigo_error"] == codigo


def test_motivo_y_normalizacion():
    assert clasificar_motivo("llegó roto") == "defectuoso"
    assert clasificar_motivo("No funciona") == "defectuoso"
    assert clasificar_motivo("no me gustó el color") == "cambio_de_opinion"
    assert normalizar_numero("mi pedido es eco 10005") == "ECO-10005"


def test_consultar_pedido_minimiza_datos(registro):
    r = consultar_pedido("ECO-10005", "SOFIA.RAMIREZ@correo.example")
    assert r["ok"] and r["datos"]["estado"] == "Retrasado"
    assert r["datos"]["cliente"] == "Sofía"          # solo el nombre
    assert "correo" not in r["datos"]                 # nunca se devuelve el correo


def test_flujo_etiqueta_y_idempotencia(registro):
    e = verificar_elegibilidad("ECO-10006", CORREOS["ECO-10006"], "cubiertos", "no me gustó")["datos"]
    primera = generar_etiqueta_devolucion(e, "ECO-10006", "cubiertos")
    assert primera["ok"] and not primera["datos"]["reutilizada"]
    assert primera["datos"]["codigo_devolucion"] == "DEV-2026-0002"  # la 0001 ya existía (ECO-10016)
    segunda = generar_etiqueta_devolucion(e, "ECO-10006", "cubiertos")
    assert segunda["ok"] and segunda["datos"]["reutilizada"]
    assert segunda["datos"]["codigo_devolucion"] == primera["datos"]["codigo_devolucion"]
    assert len(registro.devoluciones()) == 2


def test_candado_bloquea_sin_verificacion(registro):
    r = generar_etiqueta_devolucion(None, "ECO-10006", "cubiertos")
    assert r["codigo_error"] == "SIN_ELEGIBILIDAD_PREVIA"


def test_candado_bloquea_verificacion_de_otro_pedido(registro):
    e = verificar_elegibilidad("ECO-10006", CORREOS["ECO-10006"], "cubiertos", "no me gustó")["datos"]
    r = generar_etiqueta_devolucion(e, "ECO-10015", "lámpara")
    assert r["codigo_error"] == "SIN_ELEGIBILIDAD_PREVIA"


def test_candado_bloquea_no_elegible_y_revision(registro):
    no_elegible = verificar_elegibilidad("ECO-10008", CORREOS["ECO-10008"], "café", "x")["datos"]
    assert generar_etiqueta_devolucion(no_elegible, "ECO-10008", "café")["codigo_error"] == "SIN_ELEGIBILIDAD_PREVIA"
    defectuoso = verificar_elegibilidad("ECO-10012", CORREOS["ECO-10012"], "cepillo", "llegó roto")["datos"]
    assert generar_etiqueta_devolucion(defectuoso, "ECO-10012", "cepillo")["codigo_error"] == "REQUIERE_REVISION_HUMANA"
    assert permite_etiqueta(defectuoso, "ECO-10012", None) == (False, "REQUIERE_REVISION_HUMANA")


def test_transportadora_caida(registro):
    e = verificar_elegibilidad("ECO-10014", CORREOS["ECO-10014"], "toalla", "no me gustó")["datos"]
    r = generar_etiqueta_devolucion(e, "ECO-10014", "toalla")
    assert r["codigo_error"] == "TRANSPORTADORA_NO_DISPONIBLE" and r["datos"]["intentos"] == 2
    assert registro.buscar_devolucion("ECO-10014", "P11") is None   # no quedó nada a medias


def test_modo_solo_lectura(registro, monkeypatch):
    monkeypatch.setattr(config, "MODO_SOLO_LECTURA", True)
    e = verificar_elegibilidad("ECO-10006", CORREOS["ECO-10006"], "cubiertos", "no me gustó")["datos"]
    assert generar_etiqueta_devolucion(e, "ECO-10006", "cubiertos")["codigo_error"] == "ACCIONES_DESHABILITADAS"


def test_registro_enmascara_correos(registro):
    registro.registrar_accion("h1", "consultar_pedido", {"correo": "diego.torres@correo.example"},
                              {"ok": True, "mensaje": "ok"}, 5)
    assert "diego.torres" not in registro.acciones()[0]["argumentos"]
    assert "d***@correo.example" in registro.acciones()[0]["argumentos"]
