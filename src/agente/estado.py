"""Estado del grafo: lo que LangGraph guarda entre nodos y entre turnos (vía checkpointer)."""

from typing import Annotated

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class EstadoAgente(TypedDict, total=False):
    # Conversación. El reductor add_messages AGREGA mensajes en vez de reemplazar la lista.
    messages: Annotated[list[AnyMessage], add_messages]

    # Decisión del router en el turno actual (y motivo, para depurar y mostrar en la interfaz).
    ruta: str
    motivo_ruta: str

    # Resultado de la última verificación de elegibilidad. Lo escribe SOLO la herramienta
    # verificar_elegibilidad_producto, y lo lee el CANDADO de generar_etiqueta_devolucion.
    elegibilidad: dict | None

    # Resultados de acciones (para la interfaz y la auditoría).
    etiqueta: dict | None          # última etiqueta generada en la conversación
    etiqueta_turno: dict | None    # etiqueta generada en ESTE turno (para mostrar la descarga)
    ticket: dict | None            # último ticket de escalamiento

    # Fuentes consultadas por el RAG en este turno.
    fuentes: list[str]

    # Datos que el cliente escribió (extraídos con reglas, no por el modelo).
    datos_cliente: dict
