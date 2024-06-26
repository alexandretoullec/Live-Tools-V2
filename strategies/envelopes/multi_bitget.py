import datetime
import sys

# Append the path to the Live-Tools-V2 directory for importing custom modules
sys.path.append("./Live-Tools-V2")

import asyncio
from utilities.bitget_perp import PerpBitget
from secret import ACCOUNTS
import ta

# Adjust asyncio event loop policy for Windows
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def main():
    # Get account details from ACCOUNTS dictionary
    account = ACCOUNTS["bitget1"]

    # Set trading parameters
    margin_mode = "isolated"  # Margin mode can be 'isolated' or 'crossed'
    exchange_leverage = 3  # Leverage to be used on the exchange

    tf = "1h"  # Timeframe for OHLCV data
    size_leverage = 3  # Leverage to be used for position sizing
    sl = 0.3  # Stop-loss percentage

    # Parameters for different trading pairs
    params = {
        "BTC/USDT": {
            "src": "close",
            "ma_base_window": 7,
            "envelopes": [0.07, 0.1, 0.15],
            "size": 0.1,
            "sides": ["long", "short"],
        },
        "ETH/USDT": {
            "src": "close",
            "ma_base_window": 5,
            "envelopes": [0.07, 0.1, 0.15],
            "size": 0.1,
            "sides": ["long", "short"],
        },
        "ADA/USDT": {
            "src": "close",
            "ma_base_window": 5,
            "envelopes": [0.07, 0.09, 0.12, 0.15],
            "size": 0.1,
            "sides": ["long", "short"],
        },
        "DOGE/USDT": {
            "src": "close",
            "ma_base_window": 5,
            "envelopes": [0.07, 0.1, 0.15, 0.2],
            "size": 0.05,
            "sides": ["long", "short"],
        },
    }

    # Initialize exchange connection
    exchange = PerpBitget(
        public_api=account["public_api"],
        secret_api=account["secret_api"],
        password=account["password"],
    )
    invert_side = {"long": "sell", "short": "buy"}

    print(f"--- Execution started at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    try:
        # Load market data using cctx load_markets method
        await exchange.load_markets()

        # Validate and filter trading pairs
        for pair in params.copy():
            info = exchange.get_pair_info(pair)
            if info is None:
                print(f"Pair {pair} not found, removing from params...")
                del params[pair]

        pairs = list(params.keys())

        try:
            print(f"Setting {margin_mode} x{exchange_leverage} on {len(pairs)} pairs...")
            tasks = [
                exchange.set_margin_mode_and_leverage(pair, margin_mode, exchange_leverage)
                for pair in pairs
            ]
            await asyncio.gather(*tasks)  # Set leverage and margin mode for all pairs
        except Exception as e:
            print(e)

        print(f"Getting data and indicators on {len(pairs)} pairs...")
        tasks = [exchange.get_last_ohlcv(pair, tf, 50) for pair in pairs]
        dfs = await asyncio.gather(*tasks)
        df_list = dict(zip(pairs, dfs))

        # Parcours de chaque paire dans df_list
        for pair in df_list:
            current_params = params[pair]  # Récupération des paramètres actuels pour la paire
            df = df_list[pair]  # Récupération du DataFrame correspondant à la paire

            # Choix de la source pour le calcul de la moyenne mobile
            if current_params["src"] == "close":
                src = df["close"]  # Utilisation du prix de clôture
            elif current_params["src"] == "ohlc4":
                src = (df["close"] + df["high"] + df["low"] + df["open"]) / 4  # Calcul de OHLC4

            # Calcul de la moyenne mobile simple (SMA)
            df["ma_base"] = ta.trend.sma_indicator(
                close=src, window=current_params["ma_base_window"]
            )

            # Calcul des enveloppes supérieures et inférieures
            high_envelopes = [
                round(1 / (1 - e) - 1, 3) for e in current_params["envelopes"]
            ]  # Calcul des enveloppes supérieures à partir des pourcentages
            for i in range(1, len(current_params["envelopes"]) + 1):
                df[f"ma_high_{i}"] = df["ma_base"] * (1 + high_envelopes[i - 1])  # Calcul des MA hautes
                df[f"ma_low_{i}"] = df["ma_base"] * (
                    1 - current_params["envelopes"][i - 1]
                )  # Calcul des MA basses

            df_list[pair] = df  # Mise à jour du DataFrame dans df_list avec les colonnes ajoutées

        # Get account balance
        usdt_balance = await exchange.get_balance()
        usdt_balance = usdt_balance.total
        print(f"Balance: {round(usdt_balance, 2)} USDT")

        # Get all open trigger orders for each pair
        tasks = [exchange.get_open_trigger_orders(pair) for pair in pairs]
        print(f"Getting open trigger orders...")
        trigger_orders = await asyncio.gather(*tasks)
        trigger_order_list = dict(zip(pairs, trigger_orders))

        # Cancel all trigger orders
        tasks = []
        for pair in df_list:
            params[pair]["canceled_orders_buy"] = len(
                [order for order in trigger_order_list[pair] if (order.side == "buy" and order.reduce is False)]
            )
            params[pair]["canceled_orders_sell"] = len(
                [order for order in trigger_order_list[pair] if (order.side == "sell" and order.reduce is False)]
            )
            tasks.append(exchange.cancel_trigger_orders(pair, [order.id for order in trigger_order_list[pair]]))
        print(f"Canceling trigger orders...")
        await asyncio.gather(*tasks)

        # Get all open limit orders for each pair
        tasks = [exchange.get_open_orders(pair) for pair in pairs]
        print(f"Getting open orders...")
        orders = await asyncio.gather(*tasks)
        order_list = dict(zip(pairs, orders))

        # Cancel all open limit orders
        tasks = []
        for pair in df_list:
            params[pair]["canceled_orders_buy"] += len(
                [order for order in order_list[pair] if (order.side == "buy" and order.reduce is False)]
            )
            params[pair]["canceled_orders_sell"] += len(
                [order for order in order_list[pair] if (order.side == "sell" and order.reduce is False)]
            )
            tasks.append(exchange.cancel_orders(pair, [order.id for order in order_list[pair]]))
        print(f"Canceling limit orders...")
        await asyncio.gather(*tasks)

        # Get all open positions
        print(f"Getting live positions...")
        positions = await exchange.get_open_positions(pairs)

        tasks_close = []
        tasks_open = []
        for position in positions:
            print(
                f"Current position on {position.pair} {position.side} - {position.size} ~ {position.usd_size} $"
            )
            row = df_list[position.pair].iloc[-2]

            # Close existing positions
            tasks_close.append(
                exchange.place_order(
                    pair=position.pair,
                    side=invert_side[position.side],
                    price=row["ma_base"],
                    size=exchange.amount_to_precision(position.pair, position.size),
                    type="limit",
                    reduce=True,
                    margin_mode=margin_mode,
                    error=False,
                )
            )
            if position.side == "long":
                sl_side = "sell"
                sl_price = exchange.price_to_precision(position.pair, position.entry_price * (1 - sl))
            elif position.side == "short":
                sl_side = "buy"
                sl_price = exchange.price_to_precision(position.pair, position.entry_price * (1 + sl))

            tasks_close.append(
                exchange.place_trigger_order(
                    pair=position.pair,
                    side=sl_side,
                    trigger_price=sl_price,
                    price=None,
                    size=exchange.amount_to_precision(position.pair, position.size),
                    type="market",
                    reduce=True,
                    margin_mode=margin_mode,
                    error=False,
                )
            )

            # Place new trigger orders
            for i in range(
                len(params[position.pair]["envelopes"]) - params[position.pair]["canceled_orders_buy"],
                len(params[position.pair]["envelopes"]),
            ):
                tasks_open.append(
                    exchange.place_trigger_order(
                        pair=position.pair,
                        side="buy",
                        price=exchange.price_to_precision(position.pair, row[f"ma_low_{i+1}"]),
                        trigger_price=exchange.price_to_precision(position.pair, row[f"ma_low_{i+1}"] * 1.005),
                        size=exchange.amount_to_precision(
                            position.pair,
                            (
                                (params[position.pair]["size"] * usdt_balance)
                                / len(params[position.pair]["envelopes"])
                                * size_leverage
                            )
                            / row[f"ma_low_{i+1}"],
                        ),
                        type="limit",
                        reduce=False,
                        margin_mode=margin_mode,
                        error=False,
                    )
                )
            for i in range(
                len(params[position.pair]["envelopes"]) - params[position.pair]["canceled_orders_sell"],
                len(params[position.pair]["envelopes"]),
            ):
                tasks_open.append(
                    exchange.place_trigger_order(
                        pair=position.pair,
                        side="sell",
                        trigger_price=exchange.price_to_precision(position.pair, row[f"ma_high_{i+1}"] * 0.995),
                        price=exchange.price_to_precision(position.pair, row[f"ma_high_{i+1}"]),
                        size=exchange.amount_to_precision(
                            position.pair,
                            (
                                (params[position.pair]["size"] * usdt_balance)
                                / len(params[position.pair]["envelopes"])
                                * size_leverage
                            )
                            / row[f"ma_high_{i+1}"],
                        ),
                        type="limit",
                        reduce=False,
                        margin_mode=margin_mode,
                        error=False,
                    )
                )

        # Place orders to close positions
        print(f"Placing {len(tasks_close)} close SL / limit order...")
        await asyncio.gather(*tasks_close)

        # Pairs not currently in a position
        pairs_not_in_position = [
            pair for pair in pairs if pair not in [position.pair for position in positions]
        ]
        for pair in pairs_not_in_position:
            row = df_list[pair].iloc[-2]
            for i in range(len(params[pair]["envelopes"])):
                if "long" in params[pair]["sides"]:
                    tasks_open.append(
                        exchange.place_trigger_order(
                            pair=pair,
                            side="buy",
                            price=exchange.price_to_precision(pair, row[f"ma_low_{i+1}"]),
                            trigger_price=exchange.price_to_precision(pair, row[f"ma_low_{i+1}"] * 1.005),
                            size=exchange.amount_to_precision(
                                pair,
                                (
                                    (params[pair]["size"] * usdt_balance)
                                    / len(params[pair]["envelopes"])
                                    * size_leverage
                                )
                                / row[f"ma_low_{i+1}"],
                            ),
                            type="limit",
                            reduce=False,
                            margin_mode=margin_mode,
                            error=False,
                        )
                    )
                if "short" in params[pair]["sides"]:
                    tasks_open.append(
                        exchange.place_trigger_order(
                            pair=pair,
                            side="sell",
                            trigger_price=exchange.price_to_precision(pair, row[f"ma_high_{i+1}"] * 0.995),
                            price=exchange.price_to_precision(pair, row[f"ma_high_{i+1}"]),
                            size=exchange.amount_to_precision(
                                pair,
                                (
                                    (params[pair]["size"] * usdt_balance)
                                    / len(params[pair]["envelopes"])
                                    * size_leverage
                                )
                                / row[f"ma_high_{i+1}"],
                            ),
                            type="limit",
                            reduce=False,
                            margin_mode=margin_mode,
                            error=False,
                        )
                    )

        # Place orders to open new positions
        print(f"Placing {len(tasks_open)} open limit order...")
        await asyncio.gather(*tasks_open)

        await exchange.close()
        print(f"--- Execution finished at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    except Exception as e:
        await exchange.close()
        raise e


if __name__ == "__main__":
    asyncio.run(main())
