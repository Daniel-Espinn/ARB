# ARB
bot de arbitraje de criptomonedas diseñado para operar a alta velocidad.


# Omega-001: Sistema de Arbitraje Multi-Exchange en Tiempo Real

Omega-001 es un bot de arbitraje de criptomonedas diseñado para operar a alta velocidad, utilizando un enfoque por capas que combina pre-filtrado de pares por liquidez, monitoreo en tiempo real con WebSockets y detección de oportunidades de arbitraje simple y triangular. Escrito en Python con `asyncio` y la biblioteca `ccxt`, está pensado como una base robusta y escalable para traders algorítmicos.

## Características Principales

- **Arquitectura por capas**: Separación clara entre adquisición de datos, filtrado, detección y ejecución.
- **Pre-filtrado inteligente**: Solo monitorea pares con volumen mínimo y spread bajo, reduciendo la carga computacional.
- **WebSockets en tiempo real**: Usa `ccxt.pro` para recibir actualizaciones de order books al instante.
- **Arbitraje simple (cross-exchange)**: Detecta discrepancias de precios entre diferentes exchanges para el mismo par.
- **Detección triangular (intra-exchange)**: Busca ciclos de tres monedas dentro de un mismo exchange (implementación base, lista para extender).
- **Ejecución simulada**: Placeholder para integrar órdenes reales con API keys.
- **Manejo de errores y reconexiones**: Robusto ante fallos de red o límites de API.
- **Configurable mediante diccionario**: Ajusta fácilmente umbrales, comisiones, pares y exchanges.

## Requisitos

- Python 3.8 o superior
- Bibliotecas: `ccxt`, `asyncio` (incluida en la stdlib), `numpy` (opcional para cálculos avanzados)

Instalación rápida:

pip install ccxt
Instalación
Clona el repositorio:

bash
git clone https://github.com/tu-usuario/omega-001.git
cd omega-001
(Opcional) Crea un entorno virtual:

bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
Instala las dependencias:

bash
pip install -r requirements.txt
(Si no tienes requirements.txt, instala ccxt manualmente)

Configuración
Edita el diccionario CONFIG en Omega-001.py:

python
CONFIG = {
    'exchanges': ['binance', 'kraken', 'coinbase'],  # Exchanges a monitorear
    'quote_currencies': ['USDT', 'USD', 'BTC'],      # Monedas de cotización preferidas
    'min_volume_usd': 1_000_000,                      # Volumen mínimo diario en USD
    'max_spread_percent': 0.5,                        # Spread máximo aceptado (%)
    'refresh_filter_minutes': 30,                      # Re-filtrado de pares cada 30 min
    'arbitrage_min_profit_percent': 0.2,               # Beneficio mínimo neto después de comisiones
    'trade_amount_btc': 0.01,                          # Tamaño de operación simulado (en BTC)
    'log_level': 'INFO',
}
También puedes ajustar las comisiones simuladas en el diccionario FEES según tu nivel en cada exchange.

Uso
Ejecuta el bot:

bash
python3 Omega-001.py
El bot comenzará cargando los mercados de los exchanges, luego realizará un primer filtrado de pares y se suscribirá a los order books de los pares seleccionados. A partir de ese momento, monitoreará continuamente en busca de oportunidades de arbitraje.

Nota: Las operaciones no se ejecutan realmente a menos que implementes la lógica de trading en execute_arbitrage() con tus API keys.

Estructura del Código
El bot sigue un diseño modular en capas:

Capa 1: Pre-filtrado de Pares (refresh_filtered_pairs)
Obtiene tickers de todos los exchanges vía REST.

Filtra por:

Moneda de cotización en quote_currencies.

Volumen mínimo (min_volume_usd).

Spread máximo (max_spread_percent) usando bid/ask.

Almacena los símbolos que pasan los filtros en self.filtered_pairs.

Capa 2: Monitoreo en Tiempo Real (watch_order_books)
Para cada exchange y par filtrado, lanza una tarea asíncrona que se suscribe al order book vía WebSocket (ccxt.pro).

Los libros se guardan en self.orderbooks[exchange][symbol].

Capa 3: Detección de Oportunidades
Arbitraje simple: En detect_opportunities_for_symbol(), cada vez que se actualiza un order book, se comparan los mejores bids/asks del mismo símbolo en todos los exchanges. Si hay una diferencia rentable (después de comisiones), se registra.

Arbitraje triangular: En detect_triangular_opportunities(), se buscan triángulos de monedas dentro de un mismo exchange (ej. BTC → ETH → USDT → BTC). La implementación actual es un placeholder; se puede extender con el algoritmo de Bellman-Ford.

Capa 4: Ejecución (execute_arbitrage)
Función placeholder que simula la colocación de órdenes. Aquí deberías añadir la lógica real con tus API keys, manejo de órdenes simultáneas y gestión de riesgos.

Control Principal
run() inicia las tres tareas principales: filtrado, watch de order books y detección triangular periódica.

shutdown() cierra correctamente todas las conexiones.

Estrategias Implementadas
Arbitraje Simple (Cross-Exchange)
Compara el precio de compra (ask) en un exchange con el precio de venta (bid) en otro para el mismo par. Se aplican comisiones para calcular el beneficio neto.

Arbitraje Triangular (Intra-Exchange)
Detecta ciclos de conversión entre tres monedas que resulten en una ganancia. Actualmente solo identifica triángulos potenciales; se puede completar con el cálculo real de rutas.

Próximos Pasos / Mejoras Posibles
Implementar Bellman-Ford para detección triangular eficiente.

Añadir autenticación con API keys y ejecución real de órdenes.

Incorporar profundidad de mercado (varios niveles del libro) para calcular slippage.

Usar bases de datos (InfluxDB, TimescaleDB) para almacenar oportunidades y métricas.

Crear un panel de control con métricas en tiempo real (Prometheus + Grafana).

Optimizar con Cython/Numba para partes críticas.

Soportar múltiples pares de stablecoins y agregar pares cruzados.

Advertencias
⚠️ Este bot es para fines educativos y de investigación. El trading de arbitraje conlleva riesgos significativos, incluyendo pérdidas de capital debido a:

Latencia de red.

Slippage en órdenes.

Cambios repentinos de precios.

Límites de API y posibles baneos.

Errores en la implementación.

No uses fondos reales sin realizar pruebas exhaustivas y sin entender completamente los riesgos.
