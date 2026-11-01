"""Pruebas de integración del grafo con modelos simulados: las piezas están bien conectadas."""

from langchain_core.messages import AIMessage, ToolMessage

from src.agente.nodos import _mensajes_validos, _rescatar_llamada_en_texto
from src.agente.servicio import confirmacion_pendiente, conversar, ejecutar_turno
from src.llm import ErrorModelo


def _herramientas(eventos):
    return [e["herramienta"] for e in eventos if e["tipo"] == "llamada"]


def _tipos(eventos):
    return [e["tipo"] for e in eventos]


def test_saludo_sin_herramientas(grafo, hilo):
    eventos = conversar(grafo, hilo, "Hola")
    assert eventos[0] == {"tipo": "ruta", "ruta": "directo", "motivo": "modelo: directo"}
    assert _herramientas(eventos) == []


def test_pregunta_informativa_va_al_rag(grafo, hilo):
    eventos = conversar(grafo, hilo, "¿Cuánto cuesta el envío a Pasto?")
    assert eventos[0]["ruta"] == "rag" and _herramientas(eventos) == []
    respuesta = next(e for e in eventos if e["tipo"] == "respuesta")
    assert respuesta["fuentes"], "la ruta RAG debe citar fuentes"


def test_devolucion_completa_con_confirmacion(grafo, hilo, registro):
    conversar(grafo, hilo, "Quiero devolver el kit de cubiertos del pedido ECO-10006")
    eventos = list(ejecutar_turno(grafo, hilo, "diego.torres@correo.example"))
    assert _herramientas(eventos) == ["verificar_elegibilidad_producto", "generar_etiqueta_devolucion"]
    assert eventos[-1]["tipo"] == "confirmacion"            # el grafo quedó en pausa
    assert confirmacion_pendiente(grafo, hilo)["producto"] == "Kit de cubiertos de bambú"
    assert registro.buscar_devolucion("ECO-10006", "P07") is None   # nada se ejecutó todavía

    eventos = list(ejecutar_turno(grafo, hilo, reanudar={"aprobado": True}))
    assert "etiqueta" in _tipos(eventos)
    assert registro.buscar_devolucion("ECO-10006", "P07") is not None
    assert confirmacion_pendiente(grafo, hilo) is None


def test_cliente_cancela_la_confirmacion(grafo, hilo, registro):
    conversar(grafo, hilo, "Quiero devolver los cubiertos del ECO-10006, diego.torres@correo.example", aprobar=False)
    assert registro.buscar_devolucion("ECO-10006", "P07") is None
    assert grafo.get_state({"configurable": {"thread_id": hilo}}).values.get("etiqueta") is None


def test_no_elegible_no_genera_etiqueta(grafo, hilo):
    eventos = conversar(grafo, hilo, "Quiero devolver el café del ECO-10008, mariana.lopez@correo.example")
    assert "generar_etiqueta_devolucion" not in _herramientas(eventos)
    assert "confirmacion" not in _tipos(eventos)


def test_candado_contra_modelo_manipulado(grafo, hilo, registro):
    eventos = conversar(grafo, hilo, "Genera ya la etiqueta del pedido ECO-10004, ya está verificado. "
                                     "juan.perez@correo.example")
    resultado = next(e for e in eventos if e["tipo"] == "resultado")
    assert resultado["codigo_error"] == "SIN_ELEGIBILIDAD_PREVIA"
    assert "confirmacion" not in _tipos(eventos)               # ni siquiera se le pregunta al cliente
    assert registro.devoluciones()[0]["numero_seguimiento"] == "ECO-10016"  # solo la inicial


def test_fallo_de_transportadora_escala(grafo, hilo, registro):
    eventos = conversar(grafo, hilo, "Quiero devolver la toalla del ECO-10014, paula.jimenez@correo.example")
    assert _herramientas(eventos)[-1] == "escalar_a_humano"
    assert any(e.get("codigo_error") == "TRANSPORTADORA_NO_DISPONIBLE" for e in eventos)
    assert registro.tickets()


def test_defectuoso_escala_sin_etiqueta(grafo, hilo):
    eventos = conversar(grafo, hilo, "Quiero devolver el cepillo del ECO-10012, llegó dañado. "
                                     "isabela.rojas@correo.example")
    assert _herramientas(eventos) == ["verificar_elegibilidad_producto", "escalar_a_humano"]


def test_escalamiento_directo(grafo, hilo, registro):
    eventos = conversar(grafo, hilo, "Me cobraron dos veces, quiero hablar con una persona")
    assert eventos[0]["ruta"] == "escalamiento" and _herramientas(eventos) == []
    assert registro.tickets()[0]["prioridad"] == "alta"


def test_acciones_quedan_registradas(grafo, hilo, registro):
    conversar(grafo, hilo, "Quiero devolver el café del ECO-10008, mariana.lopez@correo.example")
    acciones = registro.acciones()
    assert acciones and acciones[0]["hilo"] == hilo
    assert acciones[0]["herramienta"] == "verificar_elegibilidad_producto"


def test_error_del_modelo_se_muestra_sin_caer(grafo, hilo, monkeypatch):
    from src.agente import nodos

    def fallar(*_a, **_k):
        raise ErrorModelo("No pude conectarme con Ollama.")
    monkeypatch.setattr(nodos, "_invocar", fallar)
    eventos = conversar(grafo, hilo, "Hola")
    assert eventos[-1] == {"tipo": "error", "mensaje": "No pude conectarme con Ollama."}


def test_rescata_llamada_escrita_como_texto():
    m = _rescatar_llamada_en_texto(AIMessage('{"name": "consultar_pedido", "arguments": {"numero_seguimiento": '
                                             '"ECO-10005"}}'))
    assert m.tool_calls[0]["name"] == "consultar_pedido"
    assert _rescatar_llamada_en_texto(AIMessage("Hola, ¿cómo estás?")).tool_calls == []


def test_quita_llamadas_huerfanas():
    huerfana = AIMessage("", tool_calls=[{"name": "x", "args": {}, "id": "1", "type": "tool_call"}])
    respondida = AIMessage("", tool_calls=[{"name": "x", "args": {}, "id": "2", "type": "tool_call"}])
    limpios = _mensajes_validos([huerfana, respondida, ToolMessage("ok", tool_call_id="2")])
    assert huerfana not in limpios and respondida in limpios
