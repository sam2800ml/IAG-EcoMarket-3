FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GRADIO_SERVER_NAME=0.0.0.0 \
    GRADIO_ANALYTICS_ENABLED=False \
    CARPETA_EJECUCION=/app/var

WORKDIR /app

# Dependencias primero: se reutiliza la capa de caché mientras no cambie requirements.txt
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src ./src
COPY app ./app
COPY data ./data
COPY scripts ./scripts
COPY indexar.py ./

# Usuario sin privilegios: si alguien comprometiera la app, no tendría acceso de root al contenedor
RUN useradd --create-home --uid 1000 eco && mkdir -p /app/var && chown -R eco /app/var
USER eco

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/', timeout=4)"

CMD ["python", "-m", "app.main"]
