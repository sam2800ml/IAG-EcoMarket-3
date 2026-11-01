"""Herramienta: escalar el caso a un agente humano con un ticket."""

from datetime import datetime

from src.herramientas import pedidos as svc_pedidos
from src.herramientas.contrato import exito
from src.herramientas.registro import obtener_registro

PRIORIDADES = {"alta", "media", "baja"}


def escalar_a_humano(motivo: str, resumen: str, prioridad: str = "media", numero_seguimiento: str | None = None,
                     hilo: str = "") -> dict:
    registro = obtener_registro()
    prioridad = (prioridad or "media").lower().strip()
    ticket = {
        "codigo_ticket": registro.siguiente_codigo("TCK", "tickets", "codigo_ticket"),
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "hilo": hilo,
        "prioridad": prioridad if prioridad in PRIORIDADES else "media",
        "motivo": (motivo or "sin motivo")[:200],
        "numero_seguimiento": svc_pedidos.normalizar_numero(numero_seguimiento),
        "resumen": (resumen or motivo or "")[:1000],
    }
    registro.guardar_ticket(ticket)
    return exito({k: ticket[k] for k in ("codigo_ticket", "prioridad", "motivo", "numero_seguimiento")},
                 f"Caso escalado con el ticket {ticket['codigo_ticket']}. Un agente humano lo atenderá "
                 "(lunes a sábado de 7 a. m. a 9 p. m.; domingos y festivos de 9 a. m. a 5 p. m.).")
