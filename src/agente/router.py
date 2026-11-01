"""Router de entrada: decide la ruta de cada mensaje (arquitectura router).

    rag           -> preguntas informativas (políticas, productos, tienda): RAG del Taller 2
    agente        -> estado de un pedido o devolución: agente con herramientas
    escalamiento  -> pide una persona, queja fuerte o cobros: ticket a un humano
    directo       -> saludos y temas fuera de alcance

Primero se aplican REGLAS deterministas (rápidas, predecibles y fáciles de probar).
Solo si ninguna regla decide, se consulta al modelo. Es la lección de los Talleres 1 y
2: con un modelo pequeño, la clasificación libre es inestable, así que el modelo es el
último recurso y no el primero.
"""

import re

from src.agente.prompts import ROUTER
from src.herramientas.pedidos import RE_CORREO, RE_SEGUIMIENTO, _sin_tildes
from src.llm import a_json

PIDE_HUMANO = ("hablar con una persona", "hablar con alguien", "persona real", "agente humano", "un humano",
               "un asesor", "con un asesor", "supervisor", "comunicame con", "comuniquenme", "quiero hablar con")
RIESGO = ("me cobraron", "cobro doble", "doble cobro", "cobraron dos", "fraude", "estafa", "no reconozco",
          "demanda", "abogado", "superintendencia")
QUEJA_FUERTE = ("es el colmo", "pesimo", "verguenza", "indignad", "horrible servicio", "nunca mas",
                "tercera vez", "estoy harto", "estoy harta")
INICIAR_DEVOLUCION = ("quiero devolver", "necesito devolver", "deseo devolver", "quisiera devolver",
                      "voy a devolver", "hacer una devolucion", "hacer la devolucion", "iniciar la devolucion",
                      "iniciar una devolucion", "solicitar una devolucion", "solicitar la devolucion",
                      "la etiqueta", "etiqueta de devolucion", "devolverlo", "devolverla", "devolucion de mi",
                      "cambiar el producto", "reembolso de mi", "llego danado", "llego roto", "me llego mal")
PREGUNTA_REGLAS = ("se puede devolver", "puedo devolver", "se pueden devolver", "politica", "cuantos dias",
                   "cuales son", "como funciona", "que productos", "que pasa si")
SOBRE_PEDIDO = ("mi pedido", "mi compra", "mi orden", "donde esta mi", "cuando llega mi", "estado de mi",
                "mi paquete")
SALUDOS = {"hola", "buenas", "buenos", "buen", "dia", "dias", "tardes", "noches", "gracias", "muchas", "adios",
           "chao", "hasta", "luego", "que", "tal", "eco", "ok", "listo", "perfecto"}
AFIRMACION = {"si", "no", "dale", "ok", "listo", "claro", "confirmo", "cancelar", "cancela"}


def _contiene(texto: str, frases: tuple[str, ...]) -> bool:
    return any(f in texto for f in frases)


def decidir_por_reglas(mensaje: str, ruta_anterior: str | None) -> tuple[str | None, str]:
    """Devuelve (ruta, motivo) o (None, '') si las reglas no alcanzan a decidir."""
    t = _sin_tildes(mensaje)
    palabras = re.findall(r"[a-z0-9]+", t)
    tiene_numero = bool(RE_SEGUIMIENTO.search(mensaje))

    if _contiene(t, PIDE_HUMANO) or _contiene(t, RIESGO) or _contiene(t, QUEJA_FUERTE):
        return "escalamiento", "regla: pide una persona, queja o tema de pagos"
    if tiene_numero:
        return "agente", "regla: incluye un número de seguimiento"
    if _contiene(t, INICIAR_DEVOLUCION):
        if _contiene(t, PREGUNTA_REGLAS):
            return "rag", "regla: pregunta por las reglas de devolución, no inicia una"
        return "agente", "regla: quiere iniciar una devolución"
    if ruta_anterior == "agente" and (RE_CORREO.search(mensaje) or len(palabras) <= 8
                                      or set(palabras) & AFIRMACION):
        return "agente", "regla: continúa el trámite en curso (dato corto o correo)"
    if _contiene(t, PREGUNTA_REGLAS):
        return "rag", "regla: pregunta por una política"
    if _contiene(t, SOBRE_PEDIDO):
        return "agente", "regla: pregunta por su pedido"
    if palabras and len(palabras) <= 5 and set(palabras) <= SALUDOS:
        return "directo", "regla: saludo"
    return None, ""


def decidir_con_modelo(llm_json, mensaje: str, historial: str) -> tuple[str, str]:
    try:
        respuesta = llm_json.invoke(ROUTER.format_messages(historial=historial or "(sin historial)",
                                                           mensaje=mensaje))
        ruta = a_json(respuesta.content).get("ruta", "")
    except Exception:  # noqa: BLE001 - si el modelo falla, la base de conocimiento es la opción segura
        return "rag", "modelo no disponible: se consulta la base de conocimiento"
    mapa = {"informacion": "rag", "agente": "agente", "humano": "escalamiento", "directo": "directo"}
    if ruta in mapa:
        return mapa[ruta], f"modelo: {ruta}"
    return "rag", "modelo: respuesta no válida, se consulta la base de conocimiento"
