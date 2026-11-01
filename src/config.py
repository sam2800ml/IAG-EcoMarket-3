"""Configuración central del agente de EcoMarket.

Amplía la configuración del Taller 2 (RAG) con los parámetros del agente, las
herramientas de acción y el despliegue. Todo se puede cambiar con variables de
entorno (o un archivo .env) sin tocar el código.
"""

import os
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _cargar_env(ruta: Path) -> None:
    """Lee un archivo .env sencillo (CLAVE=valor) sin dependencias externas."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea and not linea.startswith("#") and "=" in linea:
            clave, valor = linea.split("=", 1)
            os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_env(RAIZ / ".env")


def _bool(nombre: str, defecto: str = "false") -> bool:
    return os.getenv(nombre, defecto).strip().lower() in {"1", "true", "si", "sí", "yes"}


# --- Rutas de datos -----------------------------------------------------------
CARPETA_DOCUMENTOS = RAIZ / "data" / "documentos"
ARCHIVO_PEDIDOS = RAIZ / "data" / "pedidos.json"
ARCHIVO_DEVOLUCIONES_INICIALES = RAIZ / "data" / "devoluciones_iniciales.json"
CARPETA_RESULTADOS = RAIZ / "resultados"
# Datos generados en ejecución (registro de acciones, etiquetas, memoria del agente).
# En Docker esta carpeta es un volumen, así que sobrevive a los reinicios.
CARPETA_EJECUCION = Path(os.getenv("CARPETA_EJECUCION", RAIZ / "var"))
CARPETA_ETIQUETAS = CARPETA_EJECUCION / "etiquetas"
BD_OPERACION = CARPETA_EJECUCION / "operacion.db"      # acciones, devoluciones, tickets
BD_MEMORIA = CARPETA_EJECUCION / "memoria_agente.db"   # checkpointer de LangGraph

# --- Modelos (Ollama) -----------------------------------------------------------
OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODELO_LLM = os.getenv("OLLAMA_MODEL", "qwen3:4b")
# El agente necesita un modelo con soporte de herramientas ("tools" en `ollama show`).
MODELO_AGENTE = os.getenv("OLLAMA_MODEL_AGENTE", MODELO_LLM)
MODELO_EMBEDDINGS = os.getenv("OLLAMA_EMBEDDINGS", "bge-m3")
RAZONAMIENTO = _bool("OLLAMA_THINK")
SEMILLA = int(os.getenv("OLLAMA_SEED", "42"))  # salidas reproducibles para comparar

# --- Base vectorial (Qdrant) ----------------------------------------------------
CARPETA_QDRANT = Path(os.getenv("QDRANT_PATH", RAIZ / "qdrant_data"))
QDRANT_URL = os.getenv("QDRANT_URL") or None    # en Docker: http://qdrant:6333
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY") or None
COLECCION = os.getenv("QDRANT_COLLECTION", "ecomarket_conocimiento")

# --- Segmentación y recuperación (Taller 2) --------------------------------------
TAMANO_CHUNK = int(os.getenv("CHUNK_SIZE", "500"))
SOLAPAMIENTO = int(os.getenv("CHUNK_OVERLAP", "75"))
TAMANO_LOTE_EMBEDDINGS = int(os.getenv("EMBED_BATCH", "32"))
K_RESULTADOS = int(os.getenv("RAG_K", "4"))
UMBRAL_RELEVANCIA = float(os.getenv("RAG_UMBRAL", "0.45"))

# --- Reglas de negocio de las devoluciones ---------------------------------------
FECHA_HOY = date.fromisoformat(os.getenv("FECHA_REFERENCIA", "2026-09-24"))
PLAZO_DEVOLUCION_DIAS = 30
PLAZO_DEFECTO_DIAS = 5
PLAZO_ENVIO_DEVOLUCION_DIAS = 7     # días que tiene el cliente para entregar el paquete
# Por encima de este valor, una persona debe aprobar la devolución (mitigación de fraude).
UMBRAL_APROBACION_COP = int(os.getenv("UMBRAL_APROBACION_COP", "300000"))
# Transportadoras cuyo servicio de guías está "caído" (fallo simulado para demostrar errores).
TRANSPORTADORAS_NO_DISPONIBLES = {
    t.strip() for t in os.getenv("TRANSPORTADORAS_NO_DISPONIBLES", "Envía").split(",") if t.strip()
}

# --- Seguridad y control del agente -----------------------------------------------
# Interruptor de emergencia: si es true, el agente no puede ejecutar acciones (solo lectura).
MODO_SOLO_LECTURA = _bool("MODO_SOLO_LECTURA")
MAX_PASOS_AGENTE = int(os.getenv("MAX_PASOS_AGENTE", "25"))  # recursion_limit del grafo

# --- Interfaz ----------------------------------------------------------------------
PUERTO = int(os.getenv("PUERTO", "7860"))
HOST = os.getenv("GRADIO_SERVER_NAME", "127.0.0.1")
