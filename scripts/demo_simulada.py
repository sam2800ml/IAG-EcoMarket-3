"""Lanza la interfaz con modelos SIMULADOS (sin Ollama), para revisar la interfaz o hacer pruebas.

    python scripts/demo_simulada.py

Las decisiones del agente las toman reglas fijas (pruebas/modelo_simulado.py), no un LLM.
Para la demostración real usa `python -m app.main` con Ollama.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("RAG_UMBRAL", "0.15")
os.environ.setdefault("CARPETA_EJECUCION", str(Path(__file__).resolve().parent.parent / "var_simulada"))

import gradio as gr  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from qdrant_client import QdrantClient  # noqa: E402

from app import panel  # noqa: E402
from app.interfaz import CSS, crear_interfaz  # noqa: E402
from pruebas.modelo_simulado import (EmbeddingsSimulados, ModeloAgenteSimulado, ModeloJSONSimulado,  # noqa: E402
                                     ModeloTextoSimulado)
from src import config  # noqa: E402
from src.agente.grafo import construir_grafo  # noqa: E402
from src.rag.extraccion import cargar_todo  # noqa: E402
from src.rag.indexacion import crear_indice  # noqa: E402
from src.rag.segmentacion import segmentar_todo  # noqa: E402

if __name__ == "__main__":
    almacen, _ = crear_indice(segmentar_todo(cargar_todo()), coleccion="simulada", cliente=QdrantClient(":memory:"),
                              embeddings=EmbeddingsSimulados())
    grafo = construir_grafo(ModeloAgenteSimulado(), ModeloTextoSimulado(), ModeloJSONSimulado(), almacen,
                            InMemorySaver())
    panel.diagnostico_ollama = lambda: (False, "Modo simulado: las respuestas vienen de reglas fijas, no de un LLM.")
    crear_interfaz(grafo).launch(server_name=config.HOST, server_port=config.PUERTO, css=CSS,
                                 theme=gr.themes.Soft(primary_hue="green", secondary_hue="emerald"),
                                 allowed_paths=[str(config.CARPETA_ETIQUETAS)])
