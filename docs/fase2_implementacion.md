# Fase 2: Implementación y conexión de componentes

## 1. Estructura del código

```
src/
├── config.py                    Configuración (variables de entorno o .env)
├── llm.py                       Modelos de Ollama y traducción de errores
├── rag/                         ← Taller 2, reutilizado
│   ├── extraccion.py            PDF / CSV / JSON → Documents
│   ├── segmentacion.py          chunking estructural + recursivo
│   ├── indexacion.py            embeddings bge-m3 → Qdrant (ahora con reintentos y errores manejables)
│   └── recuperacion.py          búsqueda, filtros por tema, umbral, multiconsulta
├── herramientas/                ← lógica de negocio pura (sin LLM), probada con pytest
│   ├── contrato.py              forma común de respuesta y lista de errores
│   ├── pedidos.py               consultar_pedido + verificación de identidad
│   ├── elegibilidad.py          reglas de la política + el candado
│   ├── etiquetas.py             etiqueta PDF, idempotencia, transportadora simulada
│   ├── escalamiento.py          tickets
│   └── registro.py              registro de acciones, devoluciones y tickets (SQLite)
└── agente/                      ← LangGraph
    ├── estado.py                estado del grafo
    ├── prompts.py               prompts del router, RAG, agente y respuesta directa
    ├── router.py                reglas de entrada + clasificación con el modelo
    ├── herramientas_agente.py   las herramientas expuestas al modelo (@tool)
    ├── nodos.py                 router, rag, directo, escalamiento, agente, confirmar
    ├── grafo.py                 ensamblaje del grafo y checkpointer
    └── servicio.py              ejecuta turnos y traduce el stream a eventos
app/                             Gradio: interfaz, presentación de eventos y panel de operación
evaluacion/                      casos de prueba del comportamiento y script de evaluación
pruebas/                         pytest + modelos simulados
```

Hay tres capas con responsabilidades separadas, y cada una se prueba sola:
1. **Herramientas puras:** reglas de negocio y efectos (PDF, registros). No saben que existe un LLM.
2. **Agente:** decide qué herramienta usar y conversa. No sabe que existe Gradio.
3. **Interfaz:** muestra eventos. No conoce los detalles de LangGraph.

## 2. Cómo se conectan las piezas

### 2.1 El estado

`EstadoAgente` (`src/agente/estado.py`) es lo que viaja entre nodos y lo que el checkpointer guarda entre turnos:

| Campo | Lo escribe | Lo lee |
|---|---|---|
| `messages` (reductor `add_messages`, que agrega en vez de reemplazar) | todos los nodos | el agente, la interfaz |
| `ruta`, `motivo_ruta` | router | aristas condicionales, interfaz |
| `datos_cliente` (número y correo que **escribió el cliente**) | router, con reglas | prompt del agente, herramientas |
| `elegibilidad` | **solo** `verificar_elegibilidad_producto` (vía `Command`) | **el candado** |
| `etiqueta`, `etiqueta_turno`, `ticket`, `fuentes` | herramientas y nodos | interfaz y auditoría |

### 2.2 El grafo (`src/agente/grafo.py`)

```python
g = StateGraph(EstadoAgente)
g.add_node("router", crear_nodo_router(llm_json))
g.add_node("rag", crear_nodo_rag(almacen, llm_texto))
g.add_node("directo", crear_nodo_directo(llm_texto))
g.add_node("escalamiento", nodo_escalamiento)
g.add_node("agente", crear_nodo_agente(llm_agente.bind_tools(HERRAMIENTAS)))
g.add_node("confirmar", nodo_confirmar)
g.add_node("herramientas", ToolNode(HERRAMIENTAS, handle_tool_errors=True))

g.add_edge(START, "router")
g.add_conditional_edges("router", elegir_ruta, {...})       # rag | directo | escalamiento | agente
g.add_conditional_edges("agente", despues_del_agente)       # herramientas | confirmar | fin
g.add_edge("herramientas", "agente")                        # el ciclo del agente (ReAct)
grafo = g.compile(checkpointer=SqliteSaver(...))
```

- **`bind_tools`** le dice al modelo qué herramientas tiene. El modelo lee el nombre y la descripción (docstring) de cada una para decidir.
- **`ToolNode`** ejecuta las herramientas que el modelo pidió y devuelve sus resultados como mensajes. Con `handle_tool_errors=True`, una excepción dentro de una herramienta se convierte en un resultado que el agente puede explicar, en lugar de tumbar el grafo.
- **`despues_del_agente`** es la decisión clave: si el modelo no pidió herramientas, la respuesta está lista. Si pidió `generar_etiqueta_devolucion`, se pasa primero por `confirmar`. Si pidió otras, van directo a `herramientas`.

### 2.3 El candado y la confirmación

```python
@tool
def generar_etiqueta_devolucion(numero_seguimiento=None, producto=None,
                                estado: Annotated[dict, InjectedState] = None, ...):
    elegibilidad = estado.get("elegibilidad")   # del ESTADO, no de lo que diga el modelo
    ...
```

El modelo solo ve `numero_seguimiento` y `producto`; `estado`, `tool_call_id` y `config` los inyecta LangGraph. Lo comprobamos inspeccionando el esquema que recibe el modelo.

La confirmación vive en su propio nodo:

```python
def nodo_confirmar(estado) -> Command[Literal["herramientas", "agente"]]:
    ...                                           # solo lecturas: sin efectos secundarios
    respuesta = interrupt({"tipo": "confirmar_etiqueta", "producto": ..., ...})
    if respuesta.get("aprobado"):
        return Command(goto="herramientas")
    return Command(goto="agente", update={"messages": [ToolMessage("CANCELADO_POR_CLIENTE", ...)]})
```

Al reanudar con `Command(resume={"aprobado": True})`, LangGraph **vuelve a ejecutar el nodo desde el principio**. Por eso la confirmación no puede ir dentro de la herramienta después de hacer algo: el efecto se repetiría. En el nodo `confirmar` no hay efectos antes de `interrupt()`, y la acción ocurre después, en `herramientas`. Si el candado ya sabe que la etiqueta no se puede generar, ni siquiera se le pregunta al cliente.

### 2.4 Del grafo a la interfaz (`src/agente/servicio.py`)

`ejecutar_turno` recorre `grafo.stream(..., stream_mode="updates")` y traduce cada actualización a eventos simples: `ruta`, `llamada`, `resultado`, `respuesta`, `confirmacion`, `etiqueta`, `ticket` y `error`. Gradio y la evaluación consumen esos eventos sin conocer LangGraph. Una pausa aparece como la clave `__interrupt__` del stream y se convierte en el evento `confirmacion`.

## 3. Manejo de respuestas: éxito y error

El agente no solo ejecuta, también formatea la respuesta. El prompt del agente le pide explicar en lenguaje sencillo, sin JSON ni nombres de herramientas, y el contrato común le da siempre un `mensaje` legible. Así se maneja cada situación:

| Situación | Dónde se detecta | Qué ve el cliente |
|---|---|---|
| Faltan número o correo | El agente (prompt) o la herramienta (`DATOS_INCOMPLETOS`) | Se le piden ambos datos en un solo mensaje |
| Pedido inexistente / correo que no coincide | `consultar_pedido` / `verificar_elegibilidad` | Se le pide verificar el dato, sin revelar si el pedido existe con otro correo |
| Pedido con varios productos | `PRODUCTO_NO_ESPECIFICADO` (incluye la lista) | Se le pregunta cuál |
| No procede | `verificar_elegibilidad` (con la sección de la política) | El motivo y las alternativas (cancelar, excepción por defecto, agente humano) |
| Requiere revisión | `requiere_revision_humana` | Se crea un ticket y se le explica el siguiente paso |
| La transportadora falla | `generar_etiqueta` reintenta una vez y responde `TRANSPORTADORA_NO_DISPONIBLE` | Se escala con ticket; la devolución sí procede |
| El cliente cancela | Nodo `confirmar` | No se genera nada; se le pregunta si necesita algo más |
| Éxito | `generar_etiqueta` | Código de devolución, guía, fecha límite, plazo de reembolso y el PDF para descargar |
| Ollama caído o modelo sin descargar | `ErrorModelo` (src/llm.py) | Un aviso amable en el chat; la app no se cae |
| El agente entra en un ciclo | `recursion_limit` (25 pasos) | Un mensaje que sugiere reformular o pedir una persona |
| Excepción inesperada en una herramienta | `_ejecutar` + `handle_tool_errors` | `ERROR_INTERNO`, queda registrado y el agente lo explica |
| Base de conocimiento no disponible | `cargar_almacen_seguro` | Aviso en la interfaz; el agente y las herramientas siguen funcionando |

## 4. Robustez frente a un modelo pequeño

Las pruebas de los talleres anteriores mostraron que un modelo de 2 000 a 4 000 millones de parámetros clasifica de forma inestable. Por eso el sistema no depende de que el modelo acierte siempre:

1. **Router por reglas primero:** el modelo solo decide los casos ambiguos.
2. **Pocas herramientas por ruta:** al agente solo llegan pedidos y devoluciones.
3. **Datos conocidos en el prompt:** el número y el correo que escribió el cliente (extraídos con expresiones regulares) se le recuerdan al modelo, y las herramientas los completan si el modelo los olvida. Nunca se inventan: solo vienen de lo que escribió el cliente.
4. **Argumentos opcionales y normalizados:** "eco 10005" pasa a "ECO-10005", el correo se pasa a minúsculas y el producto se reconoce por palabras ("cubiertos"). Un argumento faltante produce `DATOS_INCOMPLETOS`, no un error de validación.
5. **Rescate de llamadas escritas como texto:** si el modelo escribe `{"name": "consultar_pedido", "arguments": {...}}` en lugar de hacer la llamada, se convierte en una llamada real.
6. **Limpieza del historial:** se quitan llamadas sin respuesta (por ejemplo, tras un error) antes de volver a invocar al modelo.
7. **Temperatura 0 y semilla fija** en el agente: decisiones estables y pruebas reproducibles.
8. **El nombre de la herramienta va dentro de su resultado.** Hallazgo de las pruebas: el cliente de Python de Ollama solo admite `tool_name` en los mensajes de resultado y descarta el `tool_call_id` que envía `langchain-ollama`. Con Ollama real, el modelo recibía los resultados **sin saber qué herramienta produjo cada uno**, y en la prueba por HTTP el agente dejaba de avanzar después de verificar la elegibilidad (12 de 17 casos). Al incluir `"herramienta": "<nombre>"` en el contenido de cada resultado, los 17 casos pasaron.

## 5. Evaluación del comportamiento

### Pruebas automáticas (sin LLM)

`pytest` ejecuta 52 pruebas en unos 3 segundos, con modelos simulados (`pruebas/modelo_simulado.py`):
- **Reglas de elegibilidad:** 12 casos con el tipo esperado y 6 errores.
- **Candado:** sin verificación, con la verificación de otro pedido, no elegible y revisión pendiente.
- **Idempotencia, transportadora caída y modo solo lectura.**
- **Enmascarado de correos en el registro.**
- **Router:** 12 casos.
- **Grafo completo:** confirmación pendiente y reanudación, cancelación, candado frente a un modelo "manipulado" que intenta generar la etiqueta sin verificar, escalamiento, error del modelo sin caída, rescate de llamadas en texto.

### Prueba de integración por HTTP (sin GPU)

`scripts/ollama_simulado.py` es un servidor que imita la API de Ollama (`/api/chat` con herramientas, `/api/embed` y `/api/tags`) y decide con los modelos simulados. Con él se ejecuta la app **sin modificarla**: `ChatOllama` y `OllamaEmbeddings` hablan HTTP como con Ollama real. Con esta prueba se verificó la indexación, la evaluación completa (17/17) y la interfaz en un navegador automatizado, incluida la descarga del PDF. Esta prueba fue la que reveló el problema del `tool_call_id` (punto 8 de la sección anterior).

### Evaluación con el modelo real

`python -m evaluacion.evaluar` ejecuta los 17 casos de `evaluacion/casos.json` contra el modelo de Ollama y mide la **trayectoria**, no solo el texto. Cada caso define la ruta esperada, las herramientas esperadas, las herramientas prohibidas y si debe generarse la etiqueta:

| Categoría | Casos | Qué demuestra |
|---|---|---|
| Respuesta directa y RAG sin herramientas | C01–C04 | Sabe cuándo **no** usar herramientas |
| Consulta con herramienta | C05 | Usa la herramienta de consulta, no la de acción |
| Devolución exitosa y cancelada | C06–C07 | El flujo completo con confirmación |
| No elegible | C08–C09 | Decide **no** actuar |
| Revisión humana | C10–C11 | Escala en lugar de actuar |
| Errores | C12–C13 | Fallo de la transportadora, pedido inexistente |
| Seguridad | C14–C15 | Identidad y resistencia a la manipulación |
| Idempotencia y escalamiento | C16–C17 | Sin duplicados; derivación a una persona |

El reporte se guarda en `resultados/evaluacion.md`. Con `--simulado`, la misma evaluación corre con los modelos simulados (17/17): eso **verifica el arnés de evaluación, no la calidad del modelo**.

> **Completar después de ejecutar con Ollama:** copia aquí la tabla de `resultados/evaluacion.md` y compara al menos dos modelos (por ejemplo `qwen3:4b` frente a `qwen3:8b` o `qwen3.5:2b`) con `OLLAMA_MODEL_AGENTE=<modelo> python -m evaluacion.evaluar`. Preguntas para el análisis: ¿en qué casos falló el modelo pequeño? ¿Fallaba la decisión de qué herramienta usar, los argumentos o la redacción? ¿El candado tuvo que actuar en algún caso real, no simulado?
