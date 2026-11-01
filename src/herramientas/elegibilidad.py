"""Herramienta: verificar si un producto de un pedido se puede devolver.

En los talleres anteriores esta decisión la tomaba un LLM ("evaluador") leyendo la
política. Ahora que la decisión habilita una acción real (generar una etiqueta),
pasa a ser CÓDIGO determinista: la misma entrada da siempre el mismo resultado, se
puede probar con pruebas automáticas y no se puede convencer con un prompt.

El modelo solo extrae los datos de la conversación y comunica el resultado.

Orden de las reglas (secciones de data/fuentes/politicas_ecomarket.md):
    1. Identidad y existencia del pedido.
    2. Producto dentro del pedido.
    3. Estado del pedido: debe estar entregado (sección 1.4).
    4. Devolución ya registrada -> se reutiliza (idempotencia).
    5. Defecto reportado:
         ≤ 5 días -> procede por excepción en cualquier categoría (1.3), con revisión de la foto.
         > 5 días en tecnología -> garantía (3.1), con revisión técnica.
    6. Categoría no retornable (1.2).
    7. Más de 30 días desde la entrega (1.1).
    8. Valor alto -> procede, pero requiere aprobación humana (control de fraude).
    9. En otro caso -> devolución estándar.
"""

from src import config
from src.herramientas import pedidos as svc_pedidos
from src.herramientas.contrato import error, exito
from src.herramientas.registro import obtener_registro

CATEGORIAS_NO_RETORNABLES = {
    "alimentos_perecederos": "es un alimento perecedero",
    "higiene_personal": "es un producto de higiene personal",
    "personalizados": "es un producto personalizado",
    "tarjetas_regalo": "es una tarjeta regalo",
}

_PALABRAS_DEFECTO = ("dañad", "danad", "roto", "rota", "defect", "no funciona", "no sirve", "no prende",
                     "no carga", "equivocad", "incorrect", "llego mal", "vino mal", "llego abierto",
                     "llego sucio", "golpead", "falla", "averiad", "partid", "no corresponde")


def clasificar_motivo(motivo: str | None) -> str:
    """Convierte el motivo en palabras del cliente en 'defectuoso' o 'cambio_de_opinion'."""
    texto = svc_pedidos._sin_tildes(motivo or "")
    return "defectuoso" if any(p in texto for p in _PALABRAS_DEFECTO) else "cambio_de_opinion"


def verificar_elegibilidad(numero_seguimiento: str | None, correo: str | None, producto: str | None,
                           motivo: str | None) -> dict:
    pedido, fallo = svc_pedidos.validar_acceso(numero_seguimiento, correo)
    if fallo:
        return fallo
    numero = pedido["numero_seguimiento"]

    item, candidatos = svc_pedidos.identificar_producto(pedido, producto)
    nombres = [p["nombre"] for p in candidatos]
    if item is None:
        if producto:
            return error("PRODUCTO_NO_EN_PEDIDO",
                         f"'{producto}' no está en el pedido {numero}. El pedido contiene: {', '.join(nombres)}.",
                         productos_del_pedido=nombres)
        return error("PRODUCTO_NO_ESPECIFICADO",
                     f"El pedido {numero} tiene varios productos. Pregunta cuál quiere devolver: {', '.join(nombres)}.",
                     productos_del_pedido=nombres)

    dias = svc_pedidos.dias_desde_entrega(pedido)
    motivo_tipo = clasificar_motivo(motivo)
    valor = item["precio_cop"] * item["cantidad"]
    base = {
        "numero_seguimiento": numero, "id_producto": item["id_producto"], "producto": item["nombre"],
        "categoria": item["categoria"], "estado_pedido": pedido["estado"], "dias_desde_entrega": dias,
        "motivo": motivo_tipo, "valor_cop": valor, "transportadora": pedido["transportadora"],
        "identidad_verificada": True, "requiere_revision_humana": False, "devolucion_existente": None,
    }

    def resultado(elegible: bool, tipo: str, razon: str, seccion: str, **extra) -> dict:
        datos = {**base, "elegible": elegible, "tipo": tipo, "razon": razon, "seccion_politica": seccion, **extra}
        estado = "PROCEDE" if elegible else "NO PROCEDE"
        if datos["requiere_revision_humana"]:
            estado += " (requiere revisión humana)"
        return exito(datos, f"{estado}: {razon}")

    # 3. Estado del pedido
    if pedido["estado"] == "Devolución en proceso":
        return resultado(False, "en_devolucion", "Este pedido ya tiene una devolución en proceso.", "1.5")
    if pedido["estado"] == "Cancelado":
        return resultado(False, "cancelado", "El pedido fue cancelado; no hay nada que devolver.", "1.4")
    if pedido["estado"] == "En preparación":
        return resultado(False, "cancelable", "El pedido aún no ha salido de bodega: en lugar de devolverlo, "
                         "se puede cancelar sin costo.", "1.4")
    if pedido["estado"] != "Entregado":
        return resultado(False, "no_entregado", f"El pedido está '{pedido['estado']}'. Hay que esperar a "
                         "recibirlo para iniciar la devolución.", "1.4")

    # 4. Idempotencia: ya existe una devolución para este producto
    existente = obtener_registro().buscar_devolucion(numero, item["id_producto"])
    if existente:
        return resultado(True, "ya_registrada", f"Ya existe la devolución {existente['codigo_devolucion']} para este "
                         "producto; se puede volver a entregar la misma etiqueta.", "1.5",
                         devolucion_existente=existente["codigo_devolucion"])

    # 5. Defectos y garantía
    if motivo_tipo == "defectuoso":
        if dias <= config.PLAZO_DEFECTO_DIAS:
            return resultado(True, "defectuoso", f"Producto reportado con defecto {dias} días después de la entrega "
                             f"(máximo {config.PLAZO_DEFECTO_DIAS}): aplica cambio o reembolso total. Un agente debe "
                             "revisar la foto del producto antes de generar la etiqueta.", "1.3",
                             requiere_revision_humana=True)
        if item["categoria"] == "tecnologia":
            return resultado(True, "garantia", "Han pasado más de 5 días, pero es un producto de tecnología con "
                             "garantía de 12 meses: se gestiona como reclamo de garantía con el equipo técnico.",
                             "3.1", requiere_revision_humana=True)

    # 6. Categorías no retornables
    if item["categoria"] in CATEGORIAS_NO_RETORNABLES:
        razon = (f"No se puede devolver porque {CATEGORIAS_NO_RETORNABLES[item['categoria']]}. Solo aplicaría "
                 "la excepción si llegó dañado o defectuoso, reportándolo dentro de los 5 días siguientes a la entrega.")
        return resultado(False, "no_retornable", razon, "1.2")

    # 7. Plazo general
    if dias > config.PLAZO_DEVOLUCION_DIAS:
        return resultado(False, "fuera_de_plazo", f"Han pasado {dias} días desde la entrega y el plazo es de "
                         f"{config.PLAZO_DEVOLUCION_DIAS} días.", "1.1")

    # 8. Valor alto: procede, pero lo aprueba una persona
    if valor > config.UMBRAL_APROBACION_COP:
        valor_txt = f"{valor:,}".replace(",", ".")
        return resultado(True, "alto_valor", f"La devolución procede según la política, pero por su valor "
                         f"({valor_txt} pesos) debe aprobarla una persona del equipo.", "1.1",
                         requiere_revision_humana=True)

    # 9. Devolución estándar
    return resultado(True, "devolucion_estandar", f"Producto retornable entregado hace {dias} días (plazo de "
                     f"{config.PLAZO_DEVOLUCION_DIAS} días).", "1.1")


def permite_etiqueta(elegibilidad: dict | None, numero: str | None, id_producto: str | None) -> tuple[bool, str]:
    """El CANDADO: ¿esta verificación autoriza generar la etiqueta de este pedido y producto?

    Se usa en la herramienta de etiquetas y en el nodo de confirmación. Lee el
    resultado guardado en el estado del grafo, nunca lo que afirme el modelo.
    """
    if not elegibilidad:
        return False, "SIN_ELEGIBILIDAD_PREVIA"
    if elegibilidad.get("numero_seguimiento") != numero or (id_producto and elegibilidad.get("id_producto") != id_producto):
        return False, "SIN_ELEGIBILIDAD_PREVIA"
    if not elegibilidad.get("elegible"):
        return False, "SIN_ELEGIBILIDAD_PREVIA"
    if elegibilidad.get("requiere_revision_humana"):
        return False, "REQUIERE_REVISION_HUMANA"
    return True, ""
