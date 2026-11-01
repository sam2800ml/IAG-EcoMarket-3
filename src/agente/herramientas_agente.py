"""Herramientas del agente: las funciones puras de src/herramientas expuestas a LangGraph.

Cada herramienta:
- tiene una descripción clara, porque es lo que el modelo lee para decidir cuándo usarla;
- acepta argumentos opcionales: si el modelo olvida uno, la función responde
  DATOS_INCOMPLETOS en vez de romper la validación;
- completa el número y el correo con los que el CLIENTE escribió (extraídos con reglas),
  si el modelo no los pasó: ayuda a los modelos pequeños sin inventar datos;
- registra la llamada en el registro de operación (monitoreo);
- devuelve el contrato común en JSON.

Dos usan inyecciones de LangGraph que el modelo no ve ni puede falsificar:
- InjectedState: generar_etiqueta_devolucion lee la elegibilidad del ESTADO (el candado).
- Command: verificar_elegibilidad_producto ESCRIBE su resultado en el estado.
"""

import json
import time
from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from src.herramientas import escalamiento as svc_escalamiento
from src.herramientas import etiquetas as svc_etiquetas
from src.herramientas.contrato import error
from src.herramientas.elegibilidad import verificar_elegibilidad
from src.herramientas.pedidos import consultar_pedido as svc_consultar_pedido
from src.herramientas.registro import obtener_registro

# El almacén vectorial del RAG lo configura el constructor del grafo.
_rag = {"almacen": None}


def configurar_rag(almacen) -> None:
    _rag["almacen"] = almacen


def _hilo(config: RunnableConfig | None) -> str:
    return ((config or {}).get("configurable") or {}).get("thread_id", "")


def _completar(estado: dict | None, numero: str | None, correo: str | None) -> tuple[str | None, str | None]:
    datos = (estado or {}).get("datos_cliente") or {}
    return numero or datos.get("numero_seguimiento"), correo or datos.get("correo")


def _ejecutar(nombre: str, config, argumentos: dict, funcion) -> dict:
    """Ejecuta la función pura, mide el tiempo y registra la acción (también si falla)."""
    inicio = time.perf_counter()
    try:
        resultado = funcion()
    except Exception as e:  # noqa: BLE001 - nunca se propaga al cliente una excepción cruda
        resultado = error("ERROR_INTERNO", f"Error inesperado en {nombre}: {e}")
    duracion = int((time.perf_counter() - inicio) * 1000)
    obtener_registro().registrar_accion(_hilo(config), nombre, argumentos, resultado, duracion)
    return resultado


def _para_modelo(nombre: str, resultado: dict) -> str:
    """Contenido del ToolMessage: el contrato (sin rutas internas de archivos) + el nombre de la herramienta.

    El nombre va DENTRO del contenido porque el cliente de Ollama descarta el tool_call_id:
    sin esto, el modelo recibe los resultados sin saber qué herramienta produjo cada uno.
    """
    datos = {k: v for k, v in (resultado.get("datos") or {}).items() if k != "ruta_pdf"}
    return json.dumps({"herramienta": nombre, **resultado, "datos": datos}, ensure_ascii=False)


@tool
def consultar_pedido(numero_seguimiento: str | None = None, correo: str | None = None,
                     estado: Annotated[dict, InjectedState] = None, config: RunnableConfig = None) -> str:
    """Consulta el estado, las fechas y los productos de un pedido del cliente.

    Requiere el número de seguimiento (formato ECO-XXXXX) y el correo con el que se hizo
    la compra (se verifica la identidad). Úsala cuando el cliente pregunta dónde está su
    pedido, o para ver qué productos tiene antes de una devolución.
    """
    numero, correo = _completar(estado, numero_seguimiento, correo)
    r = _ejecutar("consultar_pedido", config, {"numero_seguimiento": numero, "correo": correo},
                  lambda: svc_consultar_pedido(numero, correo))
    return _para_modelo("consultar_pedido", r)


@tool
def verificar_elegibilidad_producto(numero_seguimiento: str | None = None, correo: str | None = None,
                                    producto: str | None = None, motivo: str | None = None,
                                    estado: Annotated[dict, InjectedState] = None,
                                    tool_call_id: Annotated[str, InjectedToolCallId] = "",
                                    config: RunnableConfig = None) -> Command:
    """Verifica si un producto de un pedido se puede devolver según la política de EcoMarket.

    Úsala SIEMPRE antes de generar una etiqueta. Recibe el número de seguimiento, el correo
    de la compra, el producto (nombre o parte del nombre, por ejemplo "cubiertos") y el motivo
    de la devolución en palabras del cliente (por ejemplo "llegó roto" o "no me gustó").
    Responde si PROCEDE o NO PROCEDE, el motivo y si requiere revisión humana.
    """
    numero, correo = _completar(estado, numero_seguimiento, correo)
    r = _ejecutar("verificar_elegibilidad_producto", config,
                  {"numero_seguimiento": numero, "correo": correo, "producto": producto, "motivo": motivo},
                  lambda: verificar_elegibilidad(numero, correo, producto, motivo))
    mensaje = ToolMessage(_para_modelo("verificar_elegibilidad_producto", r), tool_call_id=tool_call_id,
                          name="verificar_elegibilidad_producto")
    # El resultado se guarda en el ESTADO: es lo que después revisa el candado.
    return Command(update={"elegibilidad": r["datos"] if r["ok"] else None, "messages": [mensaje]})


@tool
def generar_etiqueta_devolucion(numero_seguimiento: str | None = None, producto: str | None = None,
                                estado: Annotated[dict, InjectedState] = None,
                                tool_call_id: Annotated[str, InjectedToolCallId] = "",
                                config: RunnableConfig = None) -> Command:
    """Genera la etiqueta de envío prepagada para devolver un producto.

    Solo funciona si antes verificar_elegibilidad_producto respondió que la devolución PROCEDE
    para ese mismo pedido y producto, sin revisión humana. Antes de ejecutarse, el sistema le
    pide confirmación al cliente. Recibe el número de seguimiento y el producto.
    """
    elegibilidad = (estado or {}).get("elegibilidad")
    numero = numero_seguimiento or (elegibilidad or {}).get("numero_seguimiento")
    r = _ejecutar("generar_etiqueta_devolucion", config, {"numero_seguimiento": numero, "producto": producto},
                  lambda: svc_etiquetas.generar_etiqueta_devolucion(elegibilidad, numero, producto))
    mensaje = ToolMessage(_para_modelo("generar_etiqueta_devolucion", r), tool_call_id=tool_call_id,
                          name="generar_etiqueta_devolucion")
    actualizacion = {"messages": [mensaje]}
    if r["ok"]:
        actualizacion.update({"etiqueta": r["datos"], "etiqueta_turno": r["datos"]})
    return Command(update=actualizacion)


@tool
def escalar_a_humano(motivo: str | None = None, resumen: str | None = None, prioridad: str | None = "media",
                     numero_seguimiento: str | None = None, estado: Annotated[dict, InjectedState] = None,
                     tool_call_id: Annotated[str, InjectedToolCallId] = "",
                     config: RunnableConfig = None) -> Command:
    """Crea un ticket para que un agente humano continúe el caso.

    Úsala cuando la devolución requiere revisión humana (producto defectuoso, garantía o valor
    alto), cuando falla la transportadora, o cuando el cliente lo pide. Recibe el motivo, un
    resumen breve del caso, la prioridad (alta, media o baja) y el número de seguimiento si lo hay.
    """
    numero, _ = _completar(estado, numero_seguimiento, None)
    r = _ejecutar("escalar_a_humano", config, {"motivo": motivo, "prioridad": prioridad, "numero_seguimiento": numero},
                  lambda: svc_escalamiento.escalar_a_humano(motivo, resumen, prioridad, numero, _hilo(config)))
    mensaje = ToolMessage(_para_modelo("escalar_a_humano", r), tool_call_id=tool_call_id, name="escalar_a_humano")
    return Command(update={"messages": [mensaje], "ticket": r.get("datos")})


@tool
def buscar_politica(pregunta: str | None = None, config: RunnableConfig = None) -> str:
    """Busca en las políticas oficiales de EcoMarket (devoluciones, envíos, garantías, pagos, privacidad).

    Úsala cuando necesites citar una regla o el cliente pregunte por una política.
    Recibe la pregunta en lenguaje natural.
    """
    def buscar() -> dict:
        from src.herramientas.contrato import exito
        from src.rag.recuperacion import recuperar, temas_por_palabras
        if _rag["almacen"] is None:
            return error("BASE_CONOCIMIENTO_NO_DISPONIBLE")
        if not pregunta:
            return error("DATOS_INCOMPLETOS", "Falta la pregunta.")
        resultados = recuperar(_rag["almacen"], pregunta, temas=temas_por_palabras(pregunta) or None, k=3)
        if not resultados:
            return error("BASE_CONOCIMIENTO_NO_DISPONIBLE", "No encontré información sobre eso en las políticas.")
        return exito({"fragmentos": [{"fuente": r.cita, "texto": r.documento.page_content} for r in resultados]},
                     f"{len(resultados)} fragmentos encontrados.")

    return _para_modelo("buscar_politica", _ejecutar("buscar_politica", config, {"pregunta": pregunta}, buscar))


HERRAMIENTAS = [consultar_pedido, verificar_elegibilidad_producto, generar_etiqueta_devolucion,
                escalar_a_humano, buscar_politica]
NOMBRES = {h.name for h in HERRAMIENTAS}
