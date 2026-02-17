#!/usr/bin/env python3

import asyncio
import logging
import time
from collections import defaultdict
from typing import Dict, List, Set, Tuple, Optional
import ccxt.async_support as ccxt_rest
import ccxt.pro as ccxt_pro

# Configuración
CONFIG = {
    'exchanges': ['binance', 'kraken', 'coinbase'],  # Nombres según ccxt
    'quote_currencies': ['USDT', 'USD', 'BTC'],      # Monedas de cotización preferidas
    'min_volume_usd': 1_000_000,                      # Volumen mínimo diario en USD
    'max_spread_percent': 0.5,                        # Spread máximo aceptado (porcentaje)
    'refresh_filter_minutes': 30,                      # Re-filtrado de pares cada 30 min
    'arbitrage_min_profit_percent': 0.2,               # Beneficio mínimo neto (después de comisiones)
    'trade_amount_btc': 0.01,                          # Tamaño de operación simulado
    'log_level': 'INFO',
}

FEES = {
    'binance': 0.001,   # 0.1%
    'kraken': 0.002,    # 0.2%
    'coinbase': 0.005,  # 0.5%
}

class ArbitrageBot:
    def __init__(self, config: dict):
        self.config = config
        self.logger = self._setup_logger()
        self.exchanges_rest = {}   # Para consultas REST (carga de mercados, tickers)
        self.exchanges_pro = {}    # Para WebSockets
        self.filtered_pairs = defaultdict(set)  # exchange -> set de símbolos filtrados
        self.orderbooks = defaultdict(dict)      # [exchange][symbol] = orderbook
        self.tickers = defaultdict(dict)          # [exchange][symbol] = ticker (para volumen)
        self.last_filter_time = 0
        self.running = True

    def _setup_logger(self):
        logging.basicConfig(
            level=getattr(logging, self.config['log_level']),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        return logging.getLogger('ArbitrageBot')

    async def init_exchanges(self):
        for name in self.config['exchanges']:
            try:
                # REST (para cargar mercados y obtener tickers)
                rest_ex = getattr(ccxt_rest, name)({
                    'enableRateLimit': True,
                    'options': {'defaultType': 'spot'}
                })
                await rest_ex.load_markets()
                self.exchanges_rest[name] = rest_ex
                self.logger.info(f"REST {name}: {len(rest_ex.markets)} mercados cargados.")

                # PRO (WebSockets)
                pro_ex = getattr(ccxt_pro, name)({
                    'enableRateLimit': True,
                    'options': {'defaultType': 'spot'}
                })
                # No cargamos markets aquí porque ya los tenemos, pero podemos asignarlos
                pro_ex.markets = rest_ex.markets  # reutilizar
                self.exchanges_pro[name] = pro_ex
            except Exception as e:
                self.logger.error(f"Error inicializando {name}: {e}")

    async def close_exchanges(self):
        for ex in self.exchanges_rest.values():
            await ex.close()
        for ex in self.exchanges_pro.values():
            await ex.close()

    # ----------------------------------------------------------------------
    # CAPA 1: PRE-FILTRADO DE PARES POR LIQUIDEZ
    # ----------------------------------------------------------------------
    async def fetch_all_tickers(self) -> Dict[str, Dict[str, dict]]:
        tasks = {}
        for name, ex in self.exchanges_rest.items():
            tasks[name] = asyncio.create_task(self._safe_fetch_tickers(name, ex))
        results = {}
        for name, task in tasks.items():
            try:
                tickers = await task
                results[name] = tickers
            except Exception as e:
                self.logger.error(f"Error obteniendo tickers de {name}: {e}")
                results[name] = {}
        return results

    async def _safe_fetch_tickers(self, name, ex):
        try:
            tickers = await ex.fetch_tickers()
            self.logger.info(f"{name}: {len(tickers)} tickers recibidos.")
            return tickers
        except Exception as e:
            self.logger.error(f"{name} fetch_tickers falló: {e}")
            return {}

    def filter_pairs_by_liquidity(self, all_tickers: Dict[str, Dict[str, dict]]):
        filtered = defaultdict(set)
        for exchange, tickers in all_tickers.items():
            for symbol, t in tickers.items():
                if not t:
                    continue
                # Verificar que tenga quoteVolume y sea numérico
                quote_vol = t.get('quoteVolume')
                if quote_vol is None or not isinstance(quote_vol, (int, float)):
                    continue
                # Verificar que el símbolo tenga formato con slash
                if '/' not in symbol:
                    continue
                base, quote = symbol.split('/')
                if quote not in self.config['quote_currencies']:
                    continue
                # Volumen mínimo
                if quote_vol < self.config['min_volume_usd']:
                    continue
                # Calcular spread si hay bid/ask
                bid = t.get('bid')
                ask = t.get('ask')
                if bid is None or ask is None or bid <= 0 or ask <= 0:
                    continue
                spread_pct = (ask - bid) / bid * 100
                if spread_pct > self.config['max_spread_percent']:
                    continue
                # Pasa filtros
                filtered[exchange].add(symbol)
        self.logger.info(f"Filtrado completado. Pares seleccionados: { {k: len(v) for k, v in filtered.items()} }")
        return filtered

    async def refresh_filtered_pairs(self):
        # Primera ejecución inmediata
        self.logger.info("Iniciando refresco inicial de pares filtrados...")
        tickers = await self.fetch_all_tickers()
        self.filtered_pairs = self.filter_pairs_by_liquidity(tickers)
        self.last_filter_time = time.time()

        # Luego el bucle periódico
        while self.running:
            await asyncio.sleep(self.config['refresh_filter_minutes'] * 60)
            self.logger.info("Refrescando pares filtrados...")
            tickers = await self.fetch_all_tickers()
            self.filtered_pairs = self.filter_pairs_by_liquidity(tickers)
            self.last_filter_time = time.time()

    # ----------------------------------------------------------------------
    # CAPA 2: MONITOREO EN TIEMPO REAL (WEBSOCKETS)
    # ----------------------------------------------------------------------
    async def watch_order_books(self):
        tasks = []
        for exchange_name, pro_ex in self.exchanges_pro.items():
            # Solo los pares filtrados para este exchange
            pairs = self.filtered_pairs.get(exchange_name, set())
            if not pairs:
                self.logger.warning(f"No hay pares filtrados para {exchange_name}, se omite.")
                continue
            for symbol in pairs:
                # Verificar que el símbolo exista en los mercados (por si acaso)
                if symbol not in pro_ex.markets:
                    self.logger.warning(f"{symbol} no está en markets de {exchange_name}, se omite.")
                    continue
                tasks.append(self._watch_book(exchange_name, pro_ex, symbol))
        if tasks:
            await asyncio.gather(*tasks)
        else:
            self.logger.error("No hay tareas de watch_order_books, revisa filtros.")

    async def _watch_book(self, exchange_name, pro_ex, symbol):
        while self.running:
            try:
                book = await pro_ex.watch_order_book(symbol)
                self.orderbooks[exchange_name][symbol] = book
                # Cada vez que se actualiza un libro, podemos lanzar detección para este símbolo
                await self.detect_opportunities_for_symbol(symbol)
            except Exception as e:
                self.logger.error(f"Error en watch_book {exchange_name} {symbol}: {e}")
                await asyncio.sleep(1)  # esperar antes de reconectar

    # ----------------------------------------------------------------------
    # CAPA 3: DETECCIÓN DE OPORTUNIDADES
    # ----------------------------------------------------------------------
    async def detect_opportunities_for_symbol(self, symbol):
        # Recopilar libros disponibles para este símbolo
        books = []
        for ex_name, ex_books in self.orderbooks.items():
            if symbol in ex_books:
                books.append((ex_name, ex_books[symbol]))
        if len(books) < 2:
            return

        # Comparar todos contra todos
        for i, (ex_a, book_a) in enumerate(books):
            for j, (ex_b, book_b) in enumerate(books[i+1:], start=i+1):
                # Extraer mejor bid de A y mejor ask de B
                if not book_a['bids'] or not book_b['asks']:
                    continue
                bid_a = book_a['bids'][0][0]
                ask_b = book_b['asks'][0][0]

                # Calcular beneficio bruto
                if bid_a > ask_b:
                    # Comprar en ex_b, vender en ex_a
                    profit_pct = (bid_a - ask_b) / ask_b * 100
                    # Aplicar comisiones
                    fee_buy = FEES.get(ex_b, 0.001)
                    fee_sell = FEES.get(ex_a, 0.001)
                    # Simular operación con tamaño fijo
                    amount = self.config['trade_amount_btc']
                    cost = ask_b * amount * (1 + fee_buy)
                    revenue = bid_a * amount * (1 - fee_sell)
                    profit_net = revenue - cost
                    profit_net_pct = (profit_net / cost) * 100 if cost > 0 else 0

                    if profit_net_pct >= self.config['arbitrage_min_profit_percent']:
                        self.logger.info(f"OPORTUNIDAD SIMPLE: {symbol} - Comprar {ex_b} a {ask_b:.2f}, vender {ex_a} a {bid_a:.2f}, profit neto {profit_net_pct:.2f}% (bruto {profit_pct:.2f}%)")
                        # Aquí se llamaría a la ejecución real
                        await self.execute_arbitrage(ex_b, ex_a, symbol, 'buy', 'sell', ask_b, bid_a, amount)

    async def detect_triangular_opportunities(self, exchange_name):
        # Obtener todos los símbolos de este exchange con libros
        ex_books = self.orderbooks.get(exchange_name, {})
        if len(ex_books) < 3:
            return

        # Construir un grafo de precios: para cada par, tener precio de compra (ask) y venta (bid)
        # Ejemplo: para 'BTC/USDT', ask es comprar BTC con USDT, bid es vender BTC por USDT
        # Necesitamos todos los pares con monedas comunes
        # Simplificación: iteramos sobre triángulos predefinidos o generamos combinaciones de 3 monedas
        # En un sistema real, podrías usar Bellman-Ford, pero aquí haremos una búsqueda simple de triángulos comunes.

        # Obtener lista de monedas únicas involucradas en los pares
        coins = set()
        pair_to_coin = {}
        coin_to_pairs = defaultdict(list)
        for symbol in ex_books:
            if '/' in symbol:
                base, quote = symbol.split('/')
                coins.add(base)
                coins.add(quote)
                pair_to_coin[symbol] = (base, quote)
                coin_to_pairs[base].append(symbol)
                coin_to_pairs[quote].append(symbol)

        # Buscar triángulos: tres monedas A, B, C tal que existan pares AB, BC, CA (o AC, etc.)
        # Esto es un ejemplo simple, podría mejorarse con todas las permutaciones
        # Limitamos a monedas comunes: BTC, ETH, USDT, etc.
        common_coins = {'BTC', 'ETH', 'USDT', 'USD', 'BNB', 'SOL', 'ADA'}
        coins = coins.intersection(common_coins)

        for coin_a in coins:
            for coin_b in coins:
                if coin_a >= coin_b:
                    continue
                for coin_c in coins:
                    if coin_c <= coin_b:
                        continue
                    # Buscar pares que conecten estas monedas en orden
                    # Supongamos que queremos ir de A a B, B a C, C a A
                    pair_ab = self._find_pair(exchange_name, coin_a, coin_b)
                    pair_bc = self._find_pair(exchange_name, coin_b, coin_c)
                    pair_ca = self._find_pair(exchange_name, coin_c, coin_a)
                    if pair_ab and pair_bc and pair_ca:
                        # Tenemos un triángulo, calcular tipo de cambio
                        # Depende de la dirección. Por ejemplo, comprar A con B (si par es A/B, ask es comprar A con B)
                        # Necesitamos una ruta que nos devuelva más cantidad de la moneda inicial
                        # Esto es complejo, lo dejamos como placeholder
                        await self._evaluate_triangle(exchange_name, coin_a, coin_b, coin_c,
                                                      pair_ab, pair_bc, pair_ca)

    def _find_pair(self, exchange_name, coin1, coin2):
        """Busca un símbolo que involucre coin1 y coin2 en cualquier orden."""
        symbols = self.orderbooks[exchange_name].keys()
        for sym in symbols:
            if '/' in sym:
                b, q = sym.split('/')
                if (b == coin1 and q == coin2) or (b == coin2 and q == coin1):
                    return sym
        return None

    async def _evaluate_triangle(self, exchange, a, b, c, pair_ab, pair_bc, pair_ca):
        """Evalúa un triángulo específico."""
        # Obtener libros
        book_ab = self.orderbooks[exchange].get(pair_ab)
        book_bc = self.orderbooks[exchange].get(pair_bc)
        book_ca = self.orderbooks[exchange].get(pair_ca)
        if not book_ab or not book_bc or not book_ca:
            return

        # Determinar direcciones: asumimos que queremos empezar con A (por ejemplo USDT) y terminar con más A
        # Necesitamos saber si cada par es base/quote o quote/base para elegir bid o ask
        # Para simplificar, solo calculamos una ruta: A -> B -> C -> A
        # Primero, necesitamos convertir A a B: si existe par A/B, usamos ask (comprar A con B) ??? Confuso.
        # Mejor: Para cada par, definimos precio de A en términos de B.
        # Si el par es A/B, entonces 1 A = bid (si vendemos A) o ask (si compramos A). Depende.

        # Aquí solo mostramos que se detectó un triángulo potencial
        self.logger.info(f"Triángulo potencial en {exchange}: {a}-{b}-{c} con pares {pair_ab}, {pair_bc}, {pair_ca}")

    # ----------------------------------------------------------------------
    # CAPA 4: EJECUCIÓN (SIMULADA)
    # ----------------------------------------------------------------------
    async def execute_arbitrage(self, exchange_buy, exchange_sell, symbol, side_buy, side_sell,
                                price_buy, price_sell, amount):
        """
        Ejecuta una operación de arbitraje (simulada).
        En producción, aquí enviarías órdenes reales a los exchanges.
        """
        self.logger.info(f"EJECUTANDO: Comprar {amount} {symbol} en {exchange_buy} a {price_buy}, "
                         f"vender en {exchange_sell} a {price_sell}")
        # Aquí iría la lógica real con API keys
        # Ejemplo:
        # buy_order = await self.exchanges_pro[exchange_buy].create_order(symbol, 'limit', 'buy', amount, price_buy)
        # sell_order = await self.exchanges_pro[exchange_sell].create_order(symbol, 'limit', 'sell', amount, price_sell)
        # Verificar que ambas se ejecuten, manejar riesgos, etc.
        # Por ahora, simulamos un retardo
        await asyncio.sleep(0.1)

    # ----------------------------------------------------------------------
    # CONTROL PRINCIPAL
    # ----------------------------------------------------------------------
    async def run(self):
        """Inicia todas las tareas del bot."""
        await self.init_exchanges()
        if not self.exchanges_pro:
            self.logger.error("No se pudo inicializar ningún exchange. Abortando.")
            return

        # Lanzar tareas:
        # 1. Refresco periódico de filtros (ya hace la primera ejecución)
        filter_task = asyncio.create_task(self.refresh_filtered_pairs())
        # 2. Monitoreo de order books (se ejecuta indefinidamente)
        watch_task = asyncio.create_task(self.watch_order_books())
        # 3. Detección triangular periódica (podría ejecutarse cada cierto tiempo)
        triangle_task = asyncio.create_task(self.triangle_loop())

        # Esperar a que terminen (nunca deberían, a menos que haya error)
        await asyncio.gather(filter_task, watch_task, triangle_task)

    async def triangle_loop(self):
        """Bucle para ejecutar detección triangular cada cierto tiempo."""
        while self.running:
            await asyncio.sleep(5)  # cada 5 segundos, por ejemplo
            for exchange in self.exchanges_pro:
                await self.detect_triangular_opportunities(exchange)

    async def shutdown(self):
        self.running = False
        await self.close_exchanges()

async def main():
    bot = ArbitrageBot(CONFIG)
    try:
        await bot.run()
    except KeyboardInterrupt:
        print("\nDeteniendo bot...")
        await bot.shutdown()
    except Exception as e:
        logging.exception("Error fatal en el bot")
        await bot.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
