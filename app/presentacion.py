"""Convierte los eventos del agente en mensajes del chat de Gradio.

Separado de la interfaz para poder probarlo sin abrir el navegador. Los pasos
intermedios (ruta, herramientas, fuentes) se muestran como bloques desplegables
usando `metadata` de Gradio: así se VE cómo decide el agente.
"""

import json

NOMBRES_AMIGABLES = {
    "consultar_pedido": "Consultar pedido",
    "verificar_elegibilidad_producto": "Verificar elegibilidad",
    "generar_etiqueta_devolucion": "Generar etiqueta",
    "escalar_a_humano": "Escalar a un humano",
    "buscar_politica": "Buscar en las políticas",
}
RUTAS = {"rag": "Base de conocimiento (RAG)", "agente": "Agente con herramientas",
         "escalamiento": "Escalamiento a una persona", "directo": "Respuesta directa"}


def _formatear_argumentos(argumentos: dict) -> str:
    lineas = []
    for clave, valor in (argumentos or {}).items():
        if valor in (None, ""):
            continue
        texto = valor if isinstance(valor, str) else json.dumps(valor, ensure_ascii=False)
        lineas.append(f"- **{clave}**: {texto[:120]}")
    return "\n".join(lineas) or "_(sin argumentos)_"


def texto_confirmacion(datos: dict) -> str:
    if datos.get("devolucion_existente"):
        cuerpo = (f"Ya existe la devolución **{datos['devolucion_existente']}** para este producto. "
                  "¿Quieres que te entregue de nuevo la misma etiqueta?")
    else:
        cuerpo = "¿Confirmas que quieres generar la etiqueta de devolución?"
    return (f"**Confirmación requerida** 🔐\n\n"
            f"- Pedido: **{datos.get('numero_seguimiento')}**\n"
            f"- Producto: **{datos.get('producto')}**\n"
            f"- Transportadora: **{datos.get('transportadora')}**\n\n{cuerpo}")


class Presentador:
    """Acumula el historial del chat a medida que llegan eventos."""

    def __init__(self, historial: list, mostrar_pasos: bool = True):
        self.historial = list(historial)
        self.mostrar_pasos = mostrar_pasos
        self.posicion_llamada: dict[str, int] = {}
        self.confirmacion: dict | None = None
        self.etiqueta: dict | None = None

    def _agregar(self, contenido: str, titulo: str | None = None, estado: str | None = None, ident: str | None = None):
        mensaje = {"role": "assistant", "content": contenido}
        if titulo:
            mensaje["metadata"] = {"title": titulo, **({"status": estado} if estado else {}),
                                   **({"id": ident} if ident else {})}
        self.historial.append(mensaje)
        return len(self.historial) - 1

    def procesar(self, evento: dict) -> None:
        tipo = evento["tipo"]
        if tipo == "ruta" and self.mostrar_pasos:
            self._agregar(evento.get("motivo") or "", f"🧭 Ruta: {RUTAS.get(evento['ruta'], evento['ruta'])}", "done")
        elif tipo == "llamada" and self.mostrar_pasos:
            nombre = NOMBRES_AMIGABLES.get(evento["herramienta"], evento["herramienta"])
            self.posicion_llamada[evento["id"]] = self._agregar(
                _formatear_argumentos(evento["argumentos"]), f"🛠️ {nombre}", "pending", evento["id"])
        elif tipo == "resultado" and self.mostrar_pasos:
            icono = "✅" if evento["ok"] else "❌"
            detalle = f"\n\n**{icono} Resultado:** {evento['mensaje']}"
            if evento.get("codigo_error"):
                detalle += f"  \n`{evento['codigo_error']}`"
            pos = self.posicion_llamada.get(evento["id"])
            if pos is not None:
                m = self.historial[pos]
                m["content"] += detalle
                m["metadata"] = {**m["metadata"], "status": "done", "title": f"{icono} {m['metadata']['title'][2:]}"}
            else:
                nombre = NOMBRES_AMIGABLES.get(evento["herramienta"], evento["herramienta"])
                self._agregar(detalle.strip(), f"{icono} {nombre}", "done")
        elif tipo == "respuesta":
            self._agregar(evento["texto"])
            if evento.get("fuentes") and self.mostrar_pasos:
                self._agregar("\n".join(f"- {f}" for f in evento["fuentes"]), "📚 Fuentes consultadas", "done")
        elif tipo == "confirmacion":
            self.confirmacion = evento["datos"]
            self._agregar(texto_confirmacion(evento["datos"]) +
                          "\n\nUsa los botones **Confirmar** o **Cancelar** (o responde *sí* / *no*).")
        elif tipo == "etiqueta":
            self.etiqueta = evento["datos"]
        elif tipo == "ticket" and self.mostrar_pasos:
            d = evento["datos"] or {}
            self._agregar(f"Prioridad **{d.get('prioridad')}** · {d.get('motivo', '')}",
                          f"🎫 Ticket {d.get('codigo_ticket')}", "done")
        elif tipo == "error":
            self._agregar(f"⚠️ {evento['mensaje']}")
