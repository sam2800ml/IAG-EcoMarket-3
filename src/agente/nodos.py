"""Nodos del grafo. Cada nodo es una función: recibe el estado y devuelve lo que cambia."""

import json
import re
import uuid
from datetime import datetime
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from langgraph.types import Command, interrupt

from src import config as cfg
from src.agente import prompts
from src.agente.herramientas_agente import NOMBRES
from src.agente.router import RIESGO, decidir_con_modelo, decidir_por_reglas
from src.herramientas.elegibilidad import permite_etiqueta
from src.herramientas.escalamiento import escalar_a_humano
from src.herramientas.pedidos import _sin_tildes, normalizar_correo, normalizar_numero
from src.llm import limpiar, traducir_error
from src.rag.recuperacion import formatear_contexto, recuperar_multiconsulta, temas_por_palabras


# ---------------------------------------------------------------- utilidades
def ultimo_mensaje_humano(estado: dict) -> str:
    for m in reversed(estado.get("messages", [])):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


def historial_texto(estado: dict, ultimos: int = 6) -> str:
    lineas = []
    for m in estado.get("messages", [])[:-1]:
        if isinstance(m, HumanMessage):
            lineas.append(f"Cliente: {m.content}")
        elif isinstance(m, AIMessage) and m.content and not m.tool_calls:
            lineas.append(f"Eco: {m.content}")
    return "\n".join(lineas[-ultimos:])


def _invocar(llm, mensajes):
    try:
        return llm.invoke(mensajes)
    except Exception as e:  # noqa: BLE001
        raise traducir_error(e) from e


# ---------------------------------------------------------------- router
def crear_nodo_router(llm_json):
    def router(estado: dict) -> dict:
        mensaje = ultimo_mensaje_humano(estado)
        # Datos que escribe el cliente: se extraen con reglas, nunca los inventa el modelo.
        datos = dict(estado.get("datos_cliente") or {})
        if numero := normalizar_numero(mensaje):
            datos["numero_seguimiento"] = numero
        if correo := normalizar_correo(mensaje):
            datos["correo"] = correo

        ruta, motivo = decidir_por_reglas(mensaje, estado.get("ruta"))
        if ruta is None:
            ruta, motivo = decidir_con_modelo(llm_json, mensaje, historial_texto(estado))
        # Se limpian los campos "de este turno".
        return {"ruta": ruta, "motivo_ruta": motivo, "datos_cliente": datos, "fuentes": [], "etiqueta_turno": None}

    return router


def elegir_ruta(estado: dict) -> str:
    return estado.get("ruta", "rag")


# ---------------------------------------------------------------- ruta RAG (Taller 2)
def crear_nodo_rag(almacen, llm_texto):
    cadena_prompt = prompts.RAG_RESPUESTA

    def rag(estado: dict) -> dict:
        mensaje = ultimo_mensaje_humano(estado)
        if almacen is None:
            return {"messages": [AIMessage(prompts.SIN_INFORMACION)], "fuentes": []}
        # Pregunta actual + la anterior: resuelve seguimientos como "¿y desde qué valor es gratis?".
        humanos = [m.content for m in estado.get("messages", []) if isinstance(m, HumanMessage)]
        consultas = [mensaje] + ([f"{humanos[-2]} {mensaje}"] if len(humanos) > 1 else [])
        temas = temas_por_palabras(" ".join(consultas)) or None
        fuentes = recuperar_multiconsulta(almacen, consultas, temas=temas)
        if not fuentes:
            return {"messages": [AIMessage(prompts.SIN_INFORMACION)], "fuentes": []}
        respuesta = _invocar(llm_texto, cadena_prompt.format_messages(contexto=formatear_contexto(fuentes),
                                                                       pregunta=mensaje))
        return {"messages": [AIMessage(limpiar(respuesta.content))], "fuentes": [f.cita for f in fuentes]}

    return rag


# ---------------------------------------------------------------- ruta directa
def crear_nodo_directo(llm_texto):
    def directo(estado: dict) -> dict:
        respuesta = _invocar(llm_texto, prompts.GENERAL.format_messages(pregunta=ultimo_mensaje_humano(estado)))
        return {"messages": [AIMessage(limpiar(respuesta.content))]}

    return directo


# ---------------------------------------------------------------- ruta de escalamiento
def nodo_escalamiento(estado: dict, config: RunnableConfig) -> dict:
    """Escalamiento determinista: no depende del modelo, así que siempre funciona."""
    mensaje = ultimo_mensaje_humano(estado)
    hilo = ((config or {}).get("configurable") or {}).get("thread_id", "")
    prioridad = "alta" if any(p in _sin_tildes(mensaje) for p in RIESGO) else "media"
    resumen = (historial_texto(estado, 8) + f"\nCliente: {mensaje}").strip()
    r = escalar_a_humano(motivo=mensaje[:200], resumen=resumen, prioridad=prioridad,
                         numero_seguimiento=(estado.get("datos_cliente") or {}).get("numero_seguimiento"),
                         hilo=hilo)
    from src.herramientas.registro import obtener_registro
    obtener_registro().registrar_accion(hilo, "escalar_a_humano (router)", {"prioridad": prioridad}, r, 0)
    texto = prompts.ESCALAMIENTO_CLIENTE.format(codigo=r["datos"]["codigo_ticket"])
    return {"messages": [AIMessage(texto)], "ticket": r["datos"]}


# ---------------------------------------------------------------- agente
_RE_LLAMADA_TEXTO = re.compile(r"\{.*\}", re.DOTALL)


def _rescatar_llamada_en_texto(mensaje: AIMessage) -> AIMessage:
    """Algunos modelos pequeños escriben la llamada como texto JSON en vez de hacerla.

    Si el texto es exactamente una llamada a una herramienta conocida, se convierte en una
    llamada real. Si no, el mensaje se deja como está.
    """
    if mensaje.tool_calls or not isinstance(mensaje.content, str):
        return mensaje
    coincidencia = _RE_LLAMADA_TEXTO.search(mensaje.content)
    if not coincidencia:
        return mensaje
    try:
        datos = json.loads(coincidencia.group(0))
    except json.JSONDecodeError:
        return mensaje
    nombre = datos.get("name") or datos.get("tool") or datos.get("function")
    argumentos = datos.get("arguments") or datos.get("parameters") or datos.get("args") or {}
    if nombre in NOMBRES and isinstance(argumentos, dict):
        return AIMessage(content="", tool_calls=[{"name": nombre, "args": argumentos,
                                                  "id": f"rescate-{uuid.uuid4().hex[:8]}", "type": "tool_call"}])
    return mensaje


def _mensajes_validos(mensajes: list) -> list:
    """Quita llamadas a herramientas que quedaron sin respuesta (por ejemplo, tras un error).

    Los modelos esperan que cada llamada tenga su resultado; una llamada huérfana en el
    historial puede hacer fallar o confundir la siguiente respuesta.
    """
    respondidas = {m.tool_call_id for m in mensajes if isinstance(m, ToolMessage)}
    resultado = []
    for m in mensajes:
        if isinstance(m, AIMessage) and m.tool_calls:
            if all(tc["id"] in respondidas for tc in m.tool_calls):
                resultado.append(m)
            elif m.content:
                resultado.append(AIMessage(m.content))
        else:
            resultado.append(m)
    return resultado


def crear_nodo_agente(llm_con_herramientas):
    def agente(estado: dict) -> dict:
        datos = estado.get("datos_cliente") or {}
        conocidos = ", ".join(f"{k}: {v}" for k, v in datos.items()) or "ninguno todavía"
        sistema = SystemMessage(prompts.AGENTE.format(fecha=cfg.FECHA_HOY.isoformat(), datos_conocidos=conocidos))
        respuesta = _invocar(llm_con_herramientas, [sistema] + _mensajes_validos(estado.get("messages", [])))
        respuesta = _rescatar_llamada_en_texto(respuesta)
        if isinstance(respuesta.content, str) and not respuesta.tool_calls:
            respuesta = AIMessage(limpiar(respuesta.content), id=respuesta.id)
        return {"messages": [respuesta]}

    return agente


def despues_del_agente(estado: dict) -> Literal["herramientas", "confirmar", "__end__"]:
    ultimo = estado["messages"][-1]
    llamadas = getattr(ultimo, "tool_calls", None) or []
    if not llamadas:
        return END
    if any(tc["name"] == "generar_etiqueta_devolucion" for tc in llamadas):
        return "confirmar"   # acción con efecto real: primero se pide confirmación al cliente
    return "herramientas"


# ---------------------------------------------------------------- confirmación humana
def nodo_confirmar(estado: dict) -> Command[Literal["herramientas", "agente"]]:
    """Pausa el grafo (interrupt) para que el cliente confirme antes de generar la etiqueta.

    IMPORTANTE: al reanudar, LangGraph vuelve a ejecutar este nodo desde el principio. Por eso
    aquí no hay efectos secundarios antes de interrupt(): solo lecturas del estado.
    """
    ultimo = estado["messages"][-1]
    llamada = next(tc for tc in ultimo.tool_calls if tc["name"] == "generar_etiqueta_devolucion")
    elegibilidad = estado.get("elegibilidad") or {}
    numero = normalizar_numero(llamada["args"].get("numero_seguimiento")) or elegibilidad.get("numero_seguimiento")

    autorizado, _ = permite_etiqueta(elegibilidad, numero, None)
    if not autorizado or cfg.MODO_SOLO_LECTURA:
        # No tiene sentido pedir confirmación: la herramienta responderá el error (el candado).
        return Command(goto="herramientas")

    respuesta = interrupt({
        "tipo": "confirmar_etiqueta",
        "numero_seguimiento": numero,
        "producto": elegibilidad.get("producto"),
        "transportadora": elegibilidad.get("transportadora"),
        "devolucion_existente": elegibilidad.get("devolucion_existente"),
    })
    aprobado = respuesta.get("aprobado") if isinstance(respuesta, dict) else \
        _sin_tildes(str(respuesta)).strip() in {"si", "confirmo", "dale", "ok"}
    if aprobado:
        return Command(goto="herramientas")
    cancelaciones = [ToolMessage(json.dumps({"herramienta": tc["name"], "ok": False,
                                             "codigo_error": "CANCELADO_POR_CLIENTE",
                                             "mensaje": "El cliente decidió NO generar la etiqueta. No la generes; "
                                                        "pregúntale si necesita algo más."}, ensure_ascii=False),
                                 tool_call_id=tc["id"], name=tc["name"]) for tc in ultimo.tool_calls]
    return Command(goto="agente", update={"messages": cancelaciones})


def marca_tiempo() -> str:
    return datetime.now().isoformat(timespec="seconds")
