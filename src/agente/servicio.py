"""Ejecuta turnos del grafo y traduce su stream a eventos simples.

La interfaz (Gradio) y la evaluación usan esta capa, así ninguna depende de los
detalles internos de LangGraph. Eventos que produce `ejecutar_turno`:

    {"tipo": "ruta", "ruta": ..., "motivo": ...}
    {"tipo": "llamada", "id": ..., "herramienta": ..., "argumentos": {...}}
    {"tipo": "resultado", "id": ..., "herramienta": ..., "ok": bool, "codigo_error": ..., "mensaje": ...}
    {"tipo": "respuesta", "texto": ..., "fuentes": [...]}
    {"tipo": "confirmacion", "datos": {...}}   -> el grafo quedó en pausa esperando al cliente
    {"tipo": "etiqueta", "datos": {...}}      -> se generó una etiqueta (incluye ruta_pdf)
    {"tipo": "ticket", "datos": {...}}
    {"tipo": "error", "mensaje": ...}
"""

import json
import logging
from collections.abc import Iterator

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from src import config
from src.llm import ErrorModelo

log = logging.getLogger("ecomarket.agente")


def configuracion(hilo: str) -> dict:
    return {"configurable": {"thread_id": hilo}, "recursion_limit": config.MAX_PASOS_AGENTE}


def confirmacion_pendiente(grafo, hilo: str) -> dict | None:
    """Si el grafo está pausado esperando confirmación, devuelve los datos de la pausa."""
    estado = grafo.get_state(configuracion(hilo))
    return estado.interrupts[0].value if estado.interrupts else None


def _leer_resultado(mensaje: ToolMessage) -> dict:
    try:
        datos = json.loads(mensaje.content)
        if isinstance(datos, dict):
            return datos
    except (json.JSONDecodeError, TypeError):
        pass
    # handle_tool_errors de ToolNode devuelve texto plano cuando una herramienta lanza una excepción.
    return {"ok": False, "codigo_error": "ERROR_INTERNO", "mensaje": str(mensaje.content)[:300]}


def _actualizaciones(valor) -> list[dict]:
    if isinstance(valor, dict):
        return [valor]
    if isinstance(valor, (list, tuple)):
        return [v for v in valor if isinstance(v, dict)]
    return []


def _eventos_de_actualizacion(nodo: str, valor) -> Iterator[dict]:
    for act in _actualizaciones(valor):
        if nodo == "router":
            yield {"tipo": "ruta", "ruta": act.get("ruta"), "motivo": act.get("motivo_ruta")}
        for m in act.get("messages", []) or []:
            if isinstance(m, AIMessage) and m.tool_calls:
                for tc in m.tool_calls:
                    yield {"tipo": "llamada", "id": tc["id"], "herramienta": tc["name"], "argumentos": tc["args"]}
            elif isinstance(m, AIMessage) and m.content:
                yield {"tipo": "respuesta", "texto": m.content, "fuentes": act.get("fuentes") or []}
            elif isinstance(m, ToolMessage):
                r = _leer_resultado(m)
                yield {"tipo": "resultado", "id": m.tool_call_id, "herramienta": m.name, "ok": bool(r.get("ok")),
                       "codigo_error": r.get("codigo_error"), "mensaje": r.get("mensaje", "")}
        if act.get("etiqueta_turno"):
            yield {"tipo": "etiqueta", "datos": act["etiqueta_turno"]}
        if act.get("ticket") and nodo in ("herramientas", "escalamiento"):
            yield {"tipo": "ticket", "datos": act["ticket"]}


def ejecutar_turno(grafo, hilo: str, mensaje: str | None = None, reanudar: dict | None = None) -> Iterator[dict]:
    """Ejecuta un turno: un mensaje nuevo del cliente, o la respuesta a una confirmación pendiente."""
    if reanudar is not None:
        entrada = Command(resume=reanudar)
    else:
        entrada = {"messages": [HumanMessage(mensaje or "")]}
    try:
        for paso in grafo.stream(entrada, configuracion(hilo), stream_mode="updates"):
            for nodo, valor in paso.items():
                if nodo == "__interrupt__":
                    yield {"tipo": "confirmacion", "datos": valor[0].value}
                else:
                    yield from _eventos_de_actualizacion(nodo, valor)
    except ErrorModelo as e:
        yield {"tipo": "error", "mensaje": str(e)}
    except GraphRecursionError:
        yield {"tipo": "error", "mensaje": "El proceso tomó demasiados pasos y lo detuve por seguridad. "
                                           "Intenta de nuevo con otra forma de pedirlo, o pide hablar con una persona."}
    except Exception as e:  # noqa: BLE001 - la interfaz nunca debe caerse
        log.exception("Error inesperado en el turno")
        yield {"tipo": "error", "mensaje": f"Ocurrió un error inesperado: {e}"}


def conversar(grafo, hilo: str, mensaje: str, aprobar: bool | None = True) -> list[dict]:
    """Ejecuta un turno completo, resolviendo automáticamente las confirmaciones (evaluación y pruebas)."""
    eventos = list(ejecutar_turno(grafo, hilo, mensaje))
    while eventos and eventos[-1]["tipo"] == "confirmacion" and aprobar is not None:
        eventos += list(ejecutar_turno(grafo, hilo, reanudar={"aprobado": aprobar}))
    return eventos
