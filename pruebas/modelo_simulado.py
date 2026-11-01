"""Modelos simulados para probar el grafo sin Ollama.

NO evalúan la calidad del modelo real (para eso está evaluacion/evaluar.py con Ollama):
sirven para verificar que las piezas están bien conectadas (router, herramientas,
candado, confirmación, memoria, registro, interfaz) de forma rápida y repetible.

- ModeloAgenteSimulado: decide llamadas a herramientas con reglas fijas, como lo haría
  un agente que sigue bien el prompt. Si el cliente exige "genera ya la etiqueta", la
  llama SIN verificar, imitando a un modelo manipulado (para probar el candado).
- ModeloTextoSimulado: responde con un texto que resume el contexto recibido.
- ModeloJSONSimulado: clasifica para el router con palabras clave.
- EmbeddingsSimulados: bolsa de palabras con hash (sin modelo de embeddings).
"""

import hashlib
import json
import math
import re
import unicodedata
import uuid

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

RE_NUMERO = re.compile(r"\bECO[-\s]?(\d{5})\b", re.IGNORECASE)
RE_CORREO = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _sin_tildes(t: str) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _llamada(nombre: str, **args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": nombre, "args": args, "id": f"sim-{uuid.uuid4().hex[:8]}",
                                              "type": "tool_call"}])


class _Base(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "simulado"

    def bind_tools(self, tools, **kwargs):
        return self

    def _responder(self, mensajes) -> AIMessage:  # pragma: no cover - lo implementa cada subclase
        raise NotImplementedError

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self._responder(messages))])


class ModeloAgenteSimulado(_Base):
    def _responder(self, mensajes) -> AIMessage:
        humanos = [m.content for m in mensajes if isinstance(m, HumanMessage)]
        todo = " ".join(humanos)
        numero = (f"ECO-{RE_NUMERO.findall(todo)[-1]}" if RE_NUMERO.findall(todo) else None)
        correos = RE_CORREO.findall(todo)
        correo = correos[-1] if correos else None
        texto_devolucion = next((h for h in reversed(humanos) if "devol" in _sin_tildes(h)
                                 or "etiqueta" in _sin_tildes(h)), humanos[-1] if humanos else "")
        ultimo = mensajes[-1]

        if isinstance(ultimo, HumanMessage):
            t = _sin_tildes(ultimo.content)
            if ("genera" in t and "etiqueta" in t) and numero:
                return _llamada("generar_etiqueta_devolucion", numero_seguimiento=numero, producto=texto_devolucion)
            if not numero or not correo:
                return AIMessage("Para ayudarte necesito el número de seguimiento (ECO-XXXXX) y el correo con el "
                                 "que hiciste la compra.")
            if "devol" in _sin_tildes(todo):
                return _llamada("verificar_elegibilidad_producto", numero_seguimiento=numero, correo=correo,
                                producto=texto_devolucion, motivo=texto_devolucion)
            return _llamada("consultar_pedido", numero_seguimiento=numero, correo=correo)

        if isinstance(ultimo, ToolMessage):
            try:
                r = json.loads(ultimo.content)
            except json.JSONDecodeError:
                return AIMessage("Tuve un problema técnico. ¿Quieres que te comunique con una persona?")
            datos, nombre = r.get("datos") or {}, ultimo.name
            if r.get("codigo_error") == "CANCELADO_POR_CLIENTE":
                return AIMessage("Entendido, no generé la etiqueta. ¿Te ayudo con algo más?")
            if nombre == "verificar_elegibilidad_producto" and r.get("ok"):
                if datos.get("elegible") and not datos.get("requiere_revision_humana"):
                    return _llamada("generar_etiqueta_devolucion", numero_seguimiento=datos["numero_seguimiento"],
                                    producto=datos["producto"])
                if datos.get("requiere_revision_humana"):
                    return _llamada("escalar_a_humano", motivo=datos["tipo"], resumen=datos["razon"],
                                    prioridad="media", numero_seguimiento=datos["numero_seguimiento"])
                return AIMessage(f"Lo siento, la devolución no procede: {datos.get('razon')}")
            if nombre == "generar_etiqueta_devolucion":
                if r.get("ok"):
                    return AIMessage(f"¡Listo! Tu devolución es {datos['codigo_devolucion']} con la guía "
                                     f"{datos['guia']}. Puedes descargar la etiqueta debajo del chat.")
                if r.get("codigo_error") in ("TRANSPORTADORA_NO_DISPONIBLE", "REQUIERE_REVISION_HUMANA"):
                    return _llamada("escalar_a_humano", motivo=r["codigo_error"], resumen=r["mensaje"],
                                    prioridad="media", numero_seguimiento=numero)
                return AIMessage(f"No pude generar la etiqueta: {r.get('mensaje')}")
            if nombre == "escalar_a_humano" and r.get("ok"):
                return AIMessage(f"Escalé tu caso con el ticket {datos['codigo_ticket']}. Una persona lo revisará.")
            if nombre == "consultar_pedido" and r.get("ok"):
                return AIMessage(f"Tu pedido {datos['numero_seguimiento']} está: {datos['estado']}.")
            return AIMessage(f"No pude completar la solicitud: {r.get('mensaje')}")
        return AIMessage("¿En qué te ayudo?")


class ModeloTextoSimulado(_Base):
    def _responder(self, mensajes) -> AIMessage:
        sistema = next((m.content for m in mensajes if isinstance(m, SystemMessage)), "")
        contexto = re.search(r"<contexto>\n(.*?)\n</contexto>", sistema, re.DOTALL)
        if contexto:
            return AIMessage("[simulado] Según la información de EcoMarket: " + contexto.group(1)[:160])
        return AIMessage("¡Hola! Soy Eco, el asistente virtual de EcoMarket. ¿En qué te ayudo?")


class ModeloJSONSimulado(_Base):
    def _responder(self, mensajes) -> AIMessage:
        texto = _sin_tildes(mensajes[-1].content)
        m = re.search(r"<ultimo_mensaje>\n(.*)\n</ultimo_mensaje>", texto, re.DOTALL)
        ultimo = m.group(1) if m else texto
        if any(p in ultimo for p in ("pelicula", "capital de", "futbol")):
            ruta = "directo"
        else:
            ruta = "informacion"
        return AIMessage(json.dumps({"ruta": ruta}))


class EmbeddingsSimulados(Embeddings):
    def _vector(self, texto: str) -> list[float]:
        v = [0.0] * 256
        for palabra in re.findall(r"[a-z0-9]+", _sin_tildes(texto)):
            if len(palabra) > 2:
                v[int(hashlib.md5(palabra[:6].encode()).hexdigest(), 16) % 256] += 1
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norma for x in v]

    def embed_documents(self, textos):
        return [self._vector(t) for t in textos]

    def embed_query(self, texto):
        return self._vector(texto)
