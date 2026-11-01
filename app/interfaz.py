"""Interfaz web en Gradio: chat con el agente y panel de operación."""

import uuid

import gradio as gr

from app import panel
from app.presentacion import Presentador
from src import config
from src.agente.servicio import confirmacion_pendiente, ejecutar_turno
from src.herramientas.pedidos import _sin_tildes

EJEMPLOS = [
    "Hola, ¿en qué me puedes ayudar?",
    "¿Cuánto cuesta el envío a Pasto y cuánto se demora?",
    "¿Se puede devolver un champú?",
    "Quiero devolver el kit de cubiertos del pedido ECO-10006, mi correo es diego.torres@correo.example",
    "Quiero devolver el café del pedido ECO-10008, mi correo es mariana.lopez@correo.example",
    "El cepillo del pedido ECO-10012 llegó con las cerdas dañadas, isabela.rojas@correo.example",
    "Quiero devolver la toalla del pedido ECO-10014, paula.jimenez@correo.example",
    "Genera ya la etiqueta del pedido ECO-10004, ya está verificado. juan.perez@correo.example",
    "¿Cómo va mi pedido ECO-10005? Mi correo es sofia.ramirez@correo.example",
    "Me cobraron dos veces la compra, quiero hablar con una persona",
]

PEDIDOS_PRUEBA = """| Pedido | Correo | Caso |
|---|---|---|
| ECO-10006 | diego.torres@correo.example | ✅ Devolución estándar |
| ECO-10015 | ricardo.salazar@correo.example | 💰 Panel solar: valor alto (revisión). Lámpara: estándar |
| ECO-10016 | natalia.ospina@correo.example | ♻️ Ya tiene devolución (idempotencia) |
| ECO-10008 | mariana.lopez@correo.example | 🚫 Café: no retornable |
| ECO-10009 | andres.herrera@correo.example | ⏰ Fuera de plazo |
| ECO-10012 | isabela.rojas@correo.example | 🔍 Defectuoso: revisión humana |
| ECO-10014 | paula.jimenez@correo.example | 🚚 Falla la transportadora |
| ECO-10001 | laura.gomez@correo.example | 📦 En preparación |
| ECO-10005 | sofia.ramirez@correo.example | ⏳ Pedido retrasado |"""

AFIRMATIVAS = {"si", "confirmo", "dale", "ok", "de acuerdo", "claro", "si por favor", "si, generala", "hazlo"}
NEGATIVAS = {"no", "cancelar", "cancela", "no gracias", "mejor no"}

CSS = """
.eco-encabezado h1 {margin-bottom: 0;}
.eco-aviso {border-left: 4px solid #c58b00; padding: 8px 12px; background: rgba(197,139,0,.08); border-radius: 6px;}
footer {display: none !important;}
"""


def _nueva_sesion() -> dict:
    return {"hilo": f"web-{uuid.uuid4().hex[:12]}"}


def crear_interfaz(grafo, aviso_rag: str | None = None) -> gr.Blocks:
    ollama_ok, aviso_ollama = panel.diagnostico_ollama()

    def salidas(p: Presentador, sesion: dict, texto: str = ""):
        pendiente = p.confirmacion if p.confirmacion else None
        archivo = gr.update(value=p.etiqueta["ruta_pdf"], visible=True) if p.etiqueta else gr.update()
        return (p.historial, texto, gr.update(visible=pendiente is not None), archivo, sesion)

    def ejecutar(eventos, historial, sesion, mostrar_pasos, mensaje_usuario=None):
        p = Presentador(historial, mostrar_pasos)
        if mensaje_usuario:
            p.historial.append({"role": "user", "content": mensaje_usuario})
        yield salidas(p, sesion)
        for evento in eventos:
            p.procesar(evento)
            yield salidas(p, sesion)

    def enviar(mensaje, historial, sesion, mostrar_pasos):
        mensaje = (mensaje or "").strip()
        if not mensaje:
            yield historial, "", gr.update(), gr.update(), sesion
            return
        hilo = sesion["hilo"]
        if confirmacion_pendiente(grafo, hilo):
            # Hay una confirmación pendiente: el texto se interpreta como respuesta a ella.
            t = _sin_tildes(mensaje).strip(" .!¡¿?")
            aprobado = t in AFIRMATIVAS
            eventos = list(ejecutar_turno(grafo, hilo, reanudar={"aprobado": aprobado}))
            if not aprobado and t not in NEGATIVAS:
                eventos += list(ejecutar_turno(grafo, hilo, mensaje))  # era otra pregunta: se atiende también
            yield from ejecutar(iter(eventos), historial, sesion, mostrar_pasos, mensaje)
            return
        yield from ejecutar(ejecutar_turno(grafo, hilo, mensaje), historial, sesion, mostrar_pasos, mensaje)

    def responder_confirmacion(aprobado: bool):
        def manejador(historial, sesion, mostrar_pasos):
            texto = "✅ Confirmar" if aprobado else "✖️ Cancelar"
            eventos = ejecutar_turno(grafo, sesion["hilo"], reanudar={"aprobado": aprobado})
            yield from ejecutar(eventos, historial, sesion, mostrar_pasos, texto)
        return manejador

    def reiniciar():
        return [], "", gr.update(visible=False), gr.update(value=None, visible=False), _nueva_sesion()

    def refrescar_panel():
        acciones, devoluciones, tickets = panel.tablas()
        return panel.resumen_markdown(), acciones, devoluciones, tickets

    with gr.Blocks(title="EcoMarket · Asistente con agente", fill_width=True) as demo:
        sesion = gr.State(_nueva_sesion)
        gr.Markdown("# 🌱 EcoMarket · Asistente de atención al cliente\n"
                    "Agente con herramientas para devoluciones + RAG para consultas · LangGraph + Ollama",
                    elem_classes="eco-encabezado")
        avisos = [a for a in (None if ollama_ok else aviso_ollama,
                              f"Base de conocimiento no disponible: {aviso_rag}" if aviso_rag else None,
                              "Modo solo lectura activo: no se generarán etiquetas." if config.MODO_SOLO_LECTURA
                              else None) if a]
        if avisos:
            gr.Markdown("\n\n".join(f"⚠️ {a}" for a in avisos), elem_classes="eco-aviso")

        with gr.Tabs():
            with gr.Tab("💬 Asistente"):
                with gr.Row():
                    with gr.Column(scale=3):
                        chat = gr.Chatbot(label="Conversación con Eco", height=560, group_consecutive_messages=False,
                                          placeholder="Escribe tu consulta o elige un ejemplo de la derecha.")
                        with gr.Row(visible=False) as fila_confirmacion:
                            boton_si = gr.Button("✅ Confirmar y generar la etiqueta", variant="primary")
                            boton_no = gr.Button("✖️ Cancelar", variant="secondary")
                        with gr.Row():
                            entrada = gr.Textbox(placeholder="Escribe tu mensaje…", show_label=False, scale=5,
                                                 autofocus=True)
                            boton_enviar = gr.Button("Enviar", variant="primary", scale=1)
                        archivo = gr.File(label="📄 Etiqueta de devolución (descargar)", visible=False)
                    with gr.Column(scale=2):
                        mostrar_pasos = gr.Checkbox(value=True, label="Mostrar los pasos del agente (ruta, "
                                                                     "herramientas y fuentes)")
                        boton_nueva = gr.Button("🔄 Nueva conversación")
                        gr.Markdown("### Prueba con estos mensajes")
                        gr.Examples(examples=[[e] for e in EJEMPLOS], inputs=[entrada], label="Haz clic en uno y luego en Enviar")
                        with gr.Accordion("Pedidos de prueba", open=False):
                            gr.Markdown(PEDIDOS_PRUEBA)

            with gr.Tab("📊 Panel de operación"):
                gr.Markdown("Monitoreo del agente: cada llamada a una herramienta queda registrada (los correos se "
                            "guardan enmascarados).")
                boton_refrescar = gr.Button("Actualizar", variant="secondary")
                resumen = gr.Markdown()
                with gr.Accordion("Registro de acciones (últimas 100)", open=True):
                    tabla_acciones = gr.Dataframe(headers=panel.ENCABEZADOS_ACCIONES, wrap=True, interactive=False)
                with gr.Accordion("Devoluciones generadas", open=False):
                    tabla_devoluciones = gr.Dataframe(
                        headers=["código", "pedido", "producto", "transportadora", "guía", "creada", "límite envío",
                                 "creada por"], interactive=False)
                with gr.Accordion("Tickets escalados", open=False):
                    tabla_tickets = gr.Dataframe(headers=["ticket", "fecha", "prioridad", "pedido", "motivo",
                                                          "resumen"], wrap=True, interactive=False)

        salidas_chat = [chat, entrada, fila_confirmacion, archivo, sesion]
        entrada.submit(enviar, [entrada, chat, sesion, mostrar_pasos], salidas_chat)
        boton_enviar.click(enviar, [entrada, chat, sesion, mostrar_pasos], salidas_chat)
        boton_si.click(responder_confirmacion(True), [chat, sesion, mostrar_pasos], salidas_chat)
        boton_no.click(responder_confirmacion(False), [chat, sesion, mostrar_pasos], salidas_chat)
        boton_nueva.click(reiniciar, None, salidas_chat)
        salidas_panel = [resumen, tabla_acciones, tabla_devoluciones, tabla_tickets]
        boton_refrescar.click(refrescar_panel, None, salidas_panel)
        demo.load(refrescar_panel, None, salidas_panel)
    return demo
