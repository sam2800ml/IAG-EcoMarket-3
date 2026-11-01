"""Router de entrada: decide la ruta de cada mensaje (arquitectura router).

    rag           -> preguntas informativas (políticas, productos, tienda): RAG del Taller 2
    agente        -> estado de un pedido o devolución: agente con herramientas
    escalamiento  -> pide una persona, queja fuerte o cobros: ticket a un humano
    directo       -> saludos y temas fuera de alcance

La ruta la decide SIEMPRE el modelo (ver prompts.ROUTER), con el historial reciente como
contexto. Si el modelo no está disponible o responde algo que no es una ruta válida, el
error se propaga igual que en los demás nodos (rag, directo, agente): no hay una regla de
respaldo que decida en su lugar.
"""

from src.agente.prompts import ROUTER
from src.llm import ErrorModelo, a_json, traducir_error

# Palabras que indican una queja grave o un posible fraude: fijan la PRIORIDAD del ticket en
# nodo_escalamiento (ver src/agente/nodos.py). La ruta misma la decide el modelo, no esta lista.
RIESGO = ("me cobraron", "cobro doble", "doble cobro", "cobraron dos", "fraude", "estafa", "no reconozco",
          "demanda", "abogado", "superintendencia")

_MAPA_RUTAS = {"informacion": "rag", "agente": "agente", "humano": "escalamiento", "directo": "directo"}


def decidir_con_modelo(llm_json, mensaje: str, historial: str) -> tuple[str, str]:
    """Clasifica el mensaje con el modelo. Lanza ErrorModelo si falla o si la respuesta no
    es una de las rutas válidas (sin caer a una ruta por defecto)."""
    try:
        respuesta = llm_json.invoke(ROUTER.format_messages(historial=historial or "(sin historial)",
                                                            mensaje=mensaje))
    except Exception as e:  # noqa: BLE001 - mismo tratamiento que _invocar en los demás nodos
        raise traducir_error(e) from e
    ruta = a_json(respuesta.content).get("ruta", "")
    if ruta not in _MAPA_RUTAS:
        raise ErrorModelo(f"El router respondió una ruta no válida: {ruta!r}")
    return _MAPA_RUTAS[ruta], f"modelo: {ruta}"
