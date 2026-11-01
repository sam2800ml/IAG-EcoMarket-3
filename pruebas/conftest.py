"""Configuración común de las pruebas: todo en memoria y con modelos simulados."""

import os
import tempfile
import uuid

import pytest

# Datos de ejecución en una carpeta temporal y umbral acorde a los embeddings simulados.
os.environ["CARPETA_EJECUCION"] = tempfile.mkdtemp(prefix="ecomarket_pruebas_")
os.environ["RAG_UMBRAL"] = "0.15"
os.environ.pop("QDRANT_URL", None)


@pytest.fixture()
def registro():
    from src.herramientas.registro import Registro, usar_registro
    r = Registro(":memory:")
    usar_registro(r)
    return r


@pytest.fixture(scope="session")
def almacen():
    from qdrant_client import QdrantClient

    from pruebas.modelo_simulado import EmbeddingsSimulados
    from src.rag.extraccion import cargar_todo
    from src.rag.indexacion import crear_indice
    from src.rag.segmentacion import segmentar_todo
    almacen, _ = crear_indice(segmentar_todo(cargar_todo()), coleccion="pruebas",
                              cliente=QdrantClient(":memory:"), embeddings=EmbeddingsSimulados())
    return almacen


@pytest.fixture()
def grafo(registro, almacen):
    from langgraph.checkpoint.memory import InMemorySaver

    from pruebas.modelo_simulado import ModeloAgenteSimulado, ModeloJSONSimulado, ModeloTextoSimulado
    from src.agente.grafo import construir_grafo
    return construir_grafo(llm_agente=ModeloAgenteSimulado(), llm_texto=ModeloTextoSimulado(),
                           llm_json=ModeloJSONSimulado(), almacen=almacen, checkpointer=InMemorySaver())


@pytest.fixture()
def hilo():
    return f"prueba-{uuid.uuid4().hex[:8]}"
