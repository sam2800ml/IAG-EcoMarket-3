"""Exporta el diagrama del grafo REAL (generado por LangGraph desde el código) a docs/grafo.mmd.

    python scripts/exportar_grafo.py
"""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from pruebas.modelo_simulado import ModeloAgenteSimulado, ModeloJSONSimulado, ModeloTextoSimulado  # noqa: E402
from src.agente.grafo import construir_grafo  # noqa: E402

if __name__ == "__main__":
    grafo = construir_grafo(ModeloAgenteSimulado(), ModeloTextoSimulado(), ModeloJSONSimulado(), None, InMemorySaver())
    destino = RAIZ / "docs" / "grafo.mmd"
    destino.write_text(grafo.get_graph().draw_mermaid(), encoding="utf-8")
    print(f"Diagrama exportado a {destino.relative_to(RAIZ)}")
