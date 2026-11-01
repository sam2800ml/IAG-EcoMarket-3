"""Contrato común de respuesta de todas las herramientas.

Todas las herramientas devuelven un diccionario con la misma forma:

    {"ok": True,  "datos": {...},         "mensaje": "texto legible"}
    {"ok": False, "codigo_error": "...",  "mensaje": "texto legible", "datos": {...}}

Así el agente siempre sabe si la acción salió bien y, si falló, por qué. El
mensaje está pensado para que el modelo lo explique al cliente en lenguaje
sencillo; el código de error sirve para tomar decisiones y para el monitoreo.
"""

# Lista cerrada de errores que pueden devolver las herramientas.
CODIGOS_ERROR = {
    "DATOS_INCOMPLETOS": "Falta un dato obligatorio (número de seguimiento, correo o producto).",
    "FORMATO_INVALIDO": "El número de seguimiento o el correo no tienen un formato válido.",
    "PEDIDO_NO_EXISTE": "No hay ningún pedido con ese número de seguimiento.",
    "IDENTIDAD_NO_COINCIDE": "El correo no coincide con el registrado en el pedido.",
    "PRODUCTO_NO_ESPECIFICADO": "El pedido tiene varios productos y no se indicó cuál.",
    "PRODUCTO_NO_EN_PEDIDO": "El producto indicado no forma parte del pedido.",
    "SIN_ELEGIBILIDAD_PREVIA": "Se intentó generar una etiqueta sin una verificación de elegibilidad aprobada.",
    "REQUIERE_REVISION_HUMANA": "La devolución necesita la aprobación de una persona antes de generar la etiqueta.",
    "TRANSPORTADORA_NO_DISPONIBLE": "El servicio de guías de la transportadora no responde.",
    "ACCIONES_DESHABILITADAS": "El sistema está en modo de solo lectura; no se pueden ejecutar acciones.",
    "BASE_CONOCIMIENTO_NO_DISPONIBLE": "No se pudo consultar la base de conocimiento.",
    "ERROR_INTERNO": "Error inesperado al ejecutar la herramienta.",
}


def exito(datos: dict, mensaje: str = "") -> dict:
    return {"ok": True, "datos": datos, "mensaje": mensaje}


def error(codigo: str, mensaje: str | None = None, **datos) -> dict:
    assert codigo in CODIGOS_ERROR, f"Código de error desconocido: {codigo}"
    return {"ok": False, "codigo_error": codigo, "mensaje": mensaje or CODIGOS_ERROR[codigo], "datos": datos}
