"""Modelos de lenguaje servidos por Ollama (adaptado del Taller 2).

Cambios respecto al Taller 2:
- Los errores de conexión ya no terminan el programa (`sys.exit`): lanzan
  `ErrorModelo`, que la interfaz muestra como un mensaje amable.
- Se agrega el modelo del agente, que necesita soporte de herramientas.
- Se fija una semilla para que la misma entrada dé la misma salida (pruebas comparables).
"""

import json
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama

from src import config

_RE_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


class ErrorModelo(RuntimeError):
    """El modelo de lenguaje no está disponible o falló."""


def crear_llm(temperatura: float = 0.2, json_mode: bool = False, modelo: str | None = None) -> BaseChatModel:
    nombre = modelo or config.MODELO_LLM
    parametros = {"model": nombre, "base_url": config.OLLAMA_URL, "temperature": temperatura,
                  "seed": config.SEMILLA}
    if json_mode:
        parametros["format"] = "json"
    if any(familia in nombre for familia in ("qwen3", "deepseek-r1", "gpt-oss")):
        parametros["reasoning"] = config.RAZONAMIENTO  # sin "pensar" en voz alta: más rápido
    return ChatOllama(**parametros)


def crear_llm_agente() -> BaseChatModel:
    # Temperatura 0: en el agente queremos decisiones estables, no creatividad.
    return crear_llm(temperatura=0.0, modelo=config.MODELO_AGENTE)


def limpiar(texto: str) -> str:
    return _RE_THINK.sub("", texto or "").strip()


def a_json(texto: str) -> dict:
    texto = limpiar(texto)
    for candidato in (texto, (re.search(r"\{.*\}", texto, re.DOTALL) or [None])[0]):
        if candidato:
            try:
                valor = json.loads(candidato)
                return valor if isinstance(valor, dict) else {}
            except json.JSONDecodeError:
                pass
    return {}


def traducir_error(error: Exception) -> ErrorModelo:
    mensaje = str(error).lower()
    if "not found" in mensaje or "404" in mensaje:
        return ErrorModelo(f"El modelo no está descargado en Ollama. Ejecuta: ollama pull {config.MODELO_AGENTE}")
    if "does not support tools" in mensaje:
        return ErrorModelo(f"El modelo {config.MODELO_AGENTE} no soporta herramientas. Usa uno con 'tools' en "
                           "`ollama show`, por ejemplo qwen3:4b.")
    if any(p in mensaje for p in ("connect", "refused", "unreachable", "name or service")):
        return ErrorModelo(f"No pude conectarme con Ollama en {config.OLLAMA_URL}. Verifica que esté abierto.")
    return ErrorModelo(f"El modelo de lenguaje falló: {error}")
