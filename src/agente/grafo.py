"""Construcción del grafo de LangGraph.

                    ┌───────────┐
          START ──> │  router   │  reglas + modelo
                    └─────┬─────┘
       ┌──────────────┬───┴──────────┬──────────────┐
       ▼              ▼              ▼              ▼
     [rag]       [directo]    [escalamiento]    [agente] <──────────────┐
       │              │              │              │                   │
       ▼              ▼              ▼     ¿pidió herramientas?         │
      END            END            END    ├─ no ──> END                │
                                           ├─ generar etiqueta ─> [confirmar] ── interrupt ─┐
                                           └─ otras ─────────────> [herramientas] ──────────┤
                                                                        ▲  (si aprueba)     │
                                                                        └───────────────────┘
"""

import sqlite3

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from src import config
from src.agente import nodos
from src.agente.estado import EstadoAgente
from src.agente.herramientas_agente import HERRAMIENTAS, configurar_rag


def crear_checkpointer(persistente: bool = True):
    """Memoria de las conversaciones. SQLite en disco para que sobreviva a reinicios."""
    if not persistente:
        return InMemorySaver()
    config.CARPETA_EJECUCION.mkdir(parents=True, exist_ok=True)
    conexion = sqlite3.connect(str(config.BD_MEMORIA), check_same_thread=False)
    return SqliteSaver(conexion)


def construir_grafo(llm_agente=None, llm_texto=None, llm_json=None, almacen=None, checkpointer=None):
    """Ensambla el grafo. Los modelos se pueden inyectar (por ejemplo, simulados en las pruebas)."""
    if llm_agente is None or llm_texto is None or llm_json is None:
        from src.llm import crear_llm, crear_llm_agente
        llm_agente = llm_agente or crear_llm_agente()
        llm_texto = llm_texto or crear_llm(temperatura=0.2)
        llm_json = llm_json or crear_llm(temperatura=0.0, json_mode=True)
    configurar_rag(almacen)

    g = StateGraph(EstadoAgente)
    g.add_node("router", nodos.crear_nodo_router(llm_json))
    g.add_node("rag", nodos.crear_nodo_rag(almacen, llm_texto))
    g.add_node("directo", nodos.crear_nodo_directo(llm_texto))
    g.add_node("escalamiento", nodos.nodo_escalamiento)
    g.add_node("agente", nodos.crear_nodo_agente(llm_agente.bind_tools(HERRAMIENTAS)))
    g.add_node("confirmar", nodos.nodo_confirmar)
    # handle_tool_errors: si una herramienta lanza una excepción, el agente recibe el error como
    # resultado (y puede explicarlo) en lugar de que se caiga todo el grafo.
    g.add_node("herramientas", ToolNode(HERRAMIENTAS, handle_tool_errors=True))

    g.add_edge(START, "router")
    g.add_conditional_edges("router", nodos.elegir_ruta,
                            {"rag": "rag", "directo": "directo", "escalamiento": "escalamiento", "agente": "agente"})
    g.add_edge("rag", END)
    g.add_edge("directo", END)
    g.add_edge("escalamiento", END)
    g.add_conditional_edges("agente", nodos.despues_del_agente)
    g.add_edge("herramientas", "agente")
    # "confirmar" decide su destino con Command(goto=...): herramientas o agente.

    if checkpointer is False:          # LangGraph Studio pone su propia memoria
        return g.compile()
    return g.compile(checkpointer=checkpointer if checkpointer is not None else crear_checkpointer())


def cargar_almacen_seguro():
    """Abre la base de conocimiento. Si no está disponible, la app funciona sin RAG y lo avisa."""
    from src.rag.indexacion import ErrorBaseConocimiento, cargar_almacen
    try:
        return cargar_almacen(), None
    except ErrorBaseConocimiento as e:
        return None, str(e)
    except Exception as e:  # noqa: BLE001
        return None, f"No se pudo abrir la base de conocimiento: {e}"
