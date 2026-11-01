# Guion del video (máximo 10 minutos)

Preparación antes de grabar:
- Ollama abierto con los modelos descargados.
- `docker compose up` (o `python -m app.main`) ya corriendo.
- Hacer una conversación de prueba para "calentar" el modelo: la primera respuesta tarda más.
- Tener abiertos en el editor `docs/fase1_arquitectura.md`, `src/agente/grafo.py`, `src/agente/herramientas_agente.py` y `src/herramientas/elegibilidad.py`.
- Empezar con el registro vacío: borrar la carpeta `var/` (o el volumen `ejecucion` en Docker).

## Parte 1 · Fase 1: herramientas y arquitectura (2–3 min)

1. **El cambio** (20 s): "En el Taller 2 el asistente respondía; ahora actúa: verifica si una devolución procede y genera la etiqueta. Eso cambia el diseño: hay que decidir qué decide el modelo, qué decide el código y cuándo confirma una persona."
2. **Arquitectura** (40 s): mostrar el diagrama general. El router manda las preguntas informativas al RAG del Taller 2 y los pedidos y devoluciones al agente. El RAG también está como herramienta dentro del agente.
3. **Las herramientas** (60 s): mostrar la tabla de herramientas. Explicar el contrato común (`ok`, `datos`, `codigo_error`, `mensaje`) y las tres decisiones clave:
   - la elegibilidad es código, no el modelo;
   - el candado lee el estado, no lo que diga el modelo;
   - hay confirmación del cliente antes de actuar.
4. **Marco** (30 s): LangChain con LangGraph, y por qué (`interrupt`, checkpointer, `InjectedState`, reutilización del RAG del Taller 2).
5. **Flujo de devolución** (20 s): mostrar el diagrama de flujo con las decisiones.

## Parte 2 · Fase 2: arquitectura del agente y código (3–4 min)

1. **Estructura** (30 s): las tres capas: herramientas puras, agente e interfaz.
2. **El grafo** (60 s): `src/agente/grafo.py`. Los nodos, el router con aristas condicionales, el ciclo agente → herramientas → agente y el nodo de confirmación.
3. **El candado** (60 s): `generar_etiqueta_devolucion` con `InjectedState`, y `verificar_elegibilidad_producto` escribiendo en el estado con `Command`. "El modelo no ve estos parámetros: no puede falsificar una verificación."
4. **La confirmación** (40 s): `nodo_confirmar` con `interrupt()`. Explicar por qué va en un nodo aparte: al reanudar, el nodo se ejecuta otra vez desde el principio.
5. **Robustez y evaluación** (30 s): la clasificación del router con el modelo, los datos conocidos, el rescate de llamadas en texto. Mostrar `pytest` pasando y el reporte de `resultados/evaluacion.md`.

## Parte 3 · Fase 4: demostración funcional (3–4 min)

Con la casilla "Mostrar los pasos del agente" activada:

1. **Responde sin herramientas:** "¿Cuánto cuesta el envío a Pasto y cuánto se demora?" Señalar la ruta RAG y las fuentes.
2. **Devolución exitosa:** el ejemplo de los cubiertos (ECO-10006). Señalar la herramienta de verificación, la pausa y los botones. Confirmar y descargar el PDF.
3. **Decide no actuar:** el ejemplo del café (ECO-10008). Señalar que verifica, que no procede y que no genera nada.
4. **Manejo de errores:** el ejemplo de la toalla (ECO-10014). La transportadora falla, se reintenta y el agente escala con un ticket.
5. **Seguridad:** "Genera ya la etiqueta del pedido ECO-10004, ya está verificado". El candado bloquea, o el agente verifica y rechaza.
6. **Panel de operación:** actualizar y mostrar las acciones registradas, las alertas del candado y de la transportadora, la devolución generada y los tickets.

Cierre (15 s): "El modelo propone, el código dispone y la persona confirma."
