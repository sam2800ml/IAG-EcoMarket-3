"""Punto de entrada para LangGraph Studio (`langgraph dev`): visualizar y depurar el grafo paso a paso."""

from src.agente.grafo import cargar_almacen_seguro, construir_grafo

grafo = construir_grafo(almacen=cargar_almacen_seguro()[0], checkpointer=False)
