"""Punto de entrada de la aplicación web.

    python -m app.main
"""

import logging

import gradio as gr

from app.interfaz import CSS, crear_interfaz
from src import config
from src.agente.grafo import cargar_almacen_seguro, construir_grafo
from src.herramientas.registro import obtener_registro

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ecomarket")


def main() -> None:
    obtener_registro()  # crea la base de operación y carga las devoluciones iniciales
    almacen, aviso_rag = cargar_almacen_seguro()
    if aviso_rag:
        log.warning("RAG no disponible: %s", aviso_rag)
    grafo = construir_grafo(almacen=almacen)
    demo = crear_interfaz(grafo, aviso_rag)
    log.info("Modelo del agente: %s · Embeddings: %s · Qdrant: %s", config.MODELO_AGENTE, config.MODELO_EMBEDDINGS,
             config.QDRANT_URL or config.CARPETA_QDRANT)
    demo.queue(default_concurrency_limit=4).launch(
        server_name=config.HOST, server_port=config.PUERTO,
        theme=gr.themes.Soft(primary_hue="green", secondary_hue="emerald"), css=CSS,
        allowed_paths=[str(config.CARPETA_ETIQUETAS)],
    )


if __name__ == "__main__":
    main()
