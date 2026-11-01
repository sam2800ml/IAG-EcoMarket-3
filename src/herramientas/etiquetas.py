"""Herramienta: generar la etiqueta de devolución (acción con efecto real).

Antes de crear nada aplica, en este orden:
    1. Interruptor de emergencia (MODO_SOLO_LECTURA).
    2. El candado: debe existir una verificación de elegibilidad aprobada, guardada en
       el estado del grafo, para ESTE pedido y ESTE producto, sin revisión humana pendiente.
    3. Idempotencia: si la devolución ya existe, devuelve la misma etiqueta.
    4. Llamada (simulada) al servicio de guías de la transportadora, con un reintento.

La etiqueta es un PDF con código de barras que el cliente descarga desde la interfaz.
"""

import hashlib
import time
from datetime import timedelta
from pathlib import Path

from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.pagesizes import A6, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from src import config
from src.herramientas import pedidos as svc_pedidos
from src.herramientas.contrato import error, exito
from src.herramientas.elegibilidad import permite_etiqueta
from src.herramientas.registro import obtener_registro

BODEGA = "Bodega EcoMarket · Calle 13 # 65-20, Zona Industrial Montevideo, Bogotá"
PREFIJO_GUIA = {"Servientrega": "SV", "Coordinadora": "CO", "Envía": "EN", "Interrapidísimo": "IR"}


class TransportadoraNoDisponible(RuntimeError):
    pass


def _solicitar_guia(transportadora: str, codigo: str) -> str:
    """Simula la API de la transportadora. Falla si está en TRANSPORTADORAS_NO_DISPONIBLES."""
    if transportadora in config.TRANSPORTADORAS_NO_DISPONIBLES:
        raise TransportadoraNoDisponible(f"{transportadora} no responde (tiempo de espera agotado)")
    digitos = int(hashlib.sha1(codigo.encode()).hexdigest(), 16) % 10**10
    return f"{PREFIJO_GUIA.get(transportadora, 'EC')}{digitos:010d}"


def _dibujar_pdf(datos: dict, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ancho, alto = landscape(A6)
    c = canvas.Canvas(str(ruta), pagesize=(ancho, alto))
    c.setTitle(f"Etiqueta {datos['codigo_devolucion']}")
    verde = colors.HexColor("#2F6B3A")
    c.setFillColor(verde)
    c.rect(0, alto - 16 * mm, ancho, 16 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(6 * mm, alto - 10 * mm, "EcoMarket · Etiqueta de devolución")
    c.setFont("Helvetica", 8)
    c.drawRightString(ancho - 6 * mm, alto - 10 * mm, "Envío prepagado · sin costo para el cliente")

    c.setFillColor(colors.black)
    y = alto - 24 * mm
    for etiqueta, valor in [
        ("Devolución", datos["codigo_devolucion"]), ("Pedido", datos["numero_seguimiento"]),
        ("Producto", datos["producto"][:52]), ("Transportadora", datos["transportadora"]),
        ("Entregar antes de", datos["fecha_limite_envio"]),
    ]:
        c.setFont("Helvetica-Bold", 8)
        c.drawString(6 * mm, y, f"{etiqueta}:")
        c.setFont("Helvetica", 8)
        c.drawString(34 * mm, y, valor)
        y -= 5 * mm
    c.setFont("Helvetica-Bold", 7)
    c.drawString(6 * mm, y - 1 * mm, "Destino:")
    c.setFont("Helvetica", 7)
    c.drawString(20 * mm, y - 1 * mm, BODEGA)

    barras = code128.Code128(datos["guia"], barHeight=14 * mm, barWidth=0.38 * mm)
    barras.drawOn(c, 6 * mm, 8 * mm)
    c.setFont("Helvetica", 8)
    c.drawString(8 * mm, 4 * mm, f"Guía {datos['guia']}")
    c.showPage()
    c.save()


def generar_etiqueta_devolucion(elegibilidad: dict | None, numero_seguimiento: str | None,
                                producto: str | None) -> dict:
    # 1. Interruptor de emergencia
    if config.MODO_SOLO_LECTURA:
        return error("ACCIONES_DESHABILITADAS",
                     "Por ahora no puedo generar etiquetas. Un agente humano terminará el proceso.")

    numero = svc_pedidos.normalizar_numero(numero_seguimiento)
    pedido = svc_pedidos.buscar_pedido(numero) if numero else None
    id_producto = None
    if pedido:
        item, _ = svc_pedidos.identificar_producto(pedido, producto)
        id_producto = item["id_producto"] if item else None
    if id_producto is None and elegibilidad and elegibilidad.get("numero_seguimiento") == numero:
        id_producto = elegibilidad.get("id_producto")

    # 2. El candado
    autorizado, codigo = permite_etiqueta(elegibilidad, numero, id_producto)
    if not autorizado:
        if codigo == "REQUIERE_REVISION_HUMANA":
            return error(codigo, "Esta devolución debe revisarla una persona antes de generar la etiqueta. "
                         "Usa escalar_a_humano.")
        return error(codigo, "No se puede generar la etiqueta: primero hay que verificar la elegibilidad de este "
                     "producto con verificar_elegibilidad_producto y que el resultado sea favorable.")

    registro = obtener_registro()
    # 3. Idempotencia
    existente = registro.buscar_devolucion(numero, id_producto)
    if existente:
        ruta = config.CARPETA_ETIQUETAS / f"{existente['codigo_devolucion']}.pdf"
        if not ruta.exists():
            _dibujar_pdf(existente, ruta)
        return exito({**existente, "ruta_pdf": str(ruta), "reutilizada": True},
                     f"La devolución {existente['codigo_devolucion']} ya existía; se entrega la misma etiqueta.")

    # 4. Servicio de la transportadora (simulado) con un reintento
    transportadora = elegibilidad["transportadora"]
    codigo_dev = registro.siguiente_codigo("DEV", "devoluciones", "codigo_devolucion")
    guia, intentos = None, 0
    for intentos in (1, 2):  # noqa: B007 - se usa después del ciclo para informar los intentos
        try:
            guia = _solicitar_guia(transportadora, codigo_dev)
            break
        except TransportadoraNoDisponible:
            time.sleep(0.2)
    if guia is None:
        return error("TRANSPORTADORA_NO_DISPONIBLE",
                     f"El servicio de guías de {transportadora} no respondió después de {intentos} intentos. "
                     "La devolución sí procede: escala el caso a un agente humano para que la termine.",
                     transportadora=transportadora, intentos=intentos)

    datos = {
        "codigo_devolucion": codigo_dev, "numero_seguimiento": numero, "id_producto": id_producto,
        "producto": elegibilidad["producto"], "transportadora": transportadora, "guia": guia,
        "fecha_creacion": config.FECHA_HOY.isoformat(),
        "fecha_limite_envio": (config.FECHA_HOY + timedelta(days=config.PLAZO_ENVIO_DEVOLUCION_DIAS)).isoformat(),
        "motivo": elegibilidad["motivo"], "creada_por": "agente",
    }
    ruta = config.CARPETA_ETIQUETAS / f"{codigo_dev}.pdf"
    _dibujar_pdf(datos, ruta)
    registro.guardar_devolucion(datos)
    return exito({**datos, "ruta_pdf": str(ruta), "reutilizada": False,
                  "instrucciones": ["Empaca el producto en su empaque original.",
                                    "Pega la etiqueta en la caja.",
                                    f"Entrégalo en cualquier punto de {transportadora} antes del "
                                    f"{datos['fecha_limite_envio']}."],
                  "reembolso": "5 a 7 días hábiles al mismo medio de pago, después de revisar el producto."},
                 f"Etiqueta {codigo_dev} generada con la guía {guia}.")
