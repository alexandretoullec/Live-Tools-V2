import sys
sys.path.append("./live_tools")  # Add the directory containing the custom modules to the system path
import ccxt  # Library for cryptocurrency trading with many exchanges
import ta  # Technical Analysis library to compute indicators
import pandas as pd  # Data manipulation library
from utilities.perp_bitget import PerpBitget  # Custom module for interacting with Bitget
from utilities.custom_indicators import get_n_columns  # Custom function to manipulate DataFrames
from datetime import datetime  # Library to handle date and time
import time  # Library for time-related functions
import json  # Library to handle JSON data
from secret import ACCOUNTS

# Record the start execution time
now = datetime.now()
current_time = now.strftime("%d/%m/%Y %H:%M:%S")
print("--- Start Execution Time :", current_time, "---")

# Load API keys and secrets from a JSON file
# f = open("./live_tools/secret.json")
# secret = json.load(f)
# f.close()

# Select the account and other configurations
account = ACCOUNTS["bitget1"]
production = True

# Trading pair and configurations
pair = "BTC/USDT:USDT"
timeframe = "1h"
leverage = 1

print(f"--- {pair} {timeframe} Leverage x {leverage} ---")

# Strategy parameters
type = ["long", "short"]
bol_window = 100
bol_std = 2.25
min_bol_spread = 0
long_ma_window = 500

# Function to determine if a long position should be opened
def open_long(row):
    if (
        row['n1_close'] < row['n1_higher_band'] 
        and (row['close'] > row['higher_band']) 
        and ((row['n1_higher_band'] - row['n1_lower_band']) / row['n1_lower_band'] > min_bol_spread)
        and (row['close'] > row['long_ma'])
    ):
        return True
    else:
        return False

# Function to determine if a long position should be closed
def close_long(row):
    if (row['close'] < row['ma_band']):
        return True
    else:
        return False

# Function to determine if a short position should be opened
def open_short(row):
    if (
        row['n1_close'] > row['n1_lower_band'] 
        and (row['close'] < row['lower_band']) 
        and ((row['n1_higher_band'] - row['n1_lower_band']) / row['n1_lower_band'] > min_bol_spread)
        and (row['close'] < row['long_ma'])        
    ):
        return True
    else:
        return False

# Function to determine if a short position should be closed
def close_short(row):
    if (row['close'] > row['ma_band']):
        return True
    else:
        return False

# Instantiate the PerpBitget object with API credentials

bitget = PerpBitget(
      public_api=account["public_api"],
        secret_api=account["secret_api"],
        password=account["password"],
)

# Get historical data for the specified pair and timeframe
df = bitget.get_more_last_historical_async(pair, timeframe, 1000)

# Keep only OHLCV data (Open, High, Low, Close, Volume)
df.drop(columns=df.columns.difference(['open','high','low','close','volume']), inplace=True)

# Calculate Bollinger Bands
bol_band = ta.volatility.BollingerBands(close=df["close"], window=bol_window, window_dev=bol_std)
df["lower_band"] = bol_band.bollinger_lband()
df["higher_band"] = bol_band.bollinger_hband()
df["ma_band"] = bol_band.bollinger_mavg()

# Calculate a long moving average
df['long_ma'] = ta.trend.sma_indicator(close=df['close'], window=long_ma_window)

# Add previous values of selected columns to the DataFrame
df = get_n_columns(df, ["ma_band", "lower_band", "higher_band", "close"], 1)

# Get the account balance in USDT
usd_balance = float(bitget.get_usdt_equity())
print("USD balance :", round(usd_balance, 2), "$")

# Get current open positions
positions_data = bitget.get_open_position()
position = [
    {"side": d["side"], "size": float(d["contracts"]) * float(d["contractSize"]), "market_price": d["info"]["marketPrice"], "usd_size": float(d["contracts"]) * float(d["contractSize"]) * float(d["info"]["marketPrice"]), "open_price": d["entryPrice"]}
    for d in positions_data if d["symbol"] == pair
]

# Get the second last row of the DataFrame to use as the current row for strategy checks
row = df.iloc[-2]

# Check if there are any open positions
if len(position) > 0:
    position = position[0]
    print(f"Current position : {position}")
    # Check if the long position should be closed
    if position["side"] == "long" and close_long(row):
        close_long_market_price = float(df.iloc[-1]["close"])
        close_long_quantity = float(
            bitget.convert_amount_to_precision(pair, position["size"])
        )
        exchange_close_long_quantity = close_long_quantity * close_long_market_price
        print(
            f"Place Close Long Market Order: {close_long_quantity} {pair[:-5]} at the price of {close_long_market_price}$ ~{round(exchange_close_long_quantity, 2)}$"
        )
        if production:
            bitget.place_market_order(pair, "sell", close_long_quantity, reduce=True)
    # Check if the short position should be closed
    elif position["side"] == "short" and close_short(row):
        close_short_market_price = float(df.iloc[-1]["close"])
        close_short_quantity = float(
            bitget.convert_amount_to_precision(pair, position["size"])
        )
        exchange_close_short_quantity = close_short_quantity * close_short_market_price
        print(
            f"Place Close Short Market Order: {close_short_quantity} {pair[:-5]} at the price of {close_short_market_price}$ ~{round(exchange_close_short_quantity, 2)}$"
        )
        if production:
            bitget.place_market_order(pair, "buy", close_short_quantity, reduce=True)
else:
    print("No active position")
    # Check if a long position should be opened
    if open_long(row) and "long" in type:
        long_market_price = float(df.iloc[-1]["close"])
        long_quantity_in_usd = usd_balance * leverage
        long_quantity = float(bitget.convert_amount_to_precision(pair, float(
            bitget.convert_amount_to_precision(pair, long_quantity_in_usd / long_market_price)
        )))
        exchange_long_quantity = long_quantity * long_market_price
        print(
            f"Place Open Long Market Order: {long_quantity} {pair[:-5]} at the price of {long_market_price}$ ~{round(exchange_long_quantity, 2)}$"
        )
        if production:
            bitget.place_market_order(pair, "buy", long_quantity, reduce=False)
    # Check if a short position should be opened
    elif open_short(row) and "short" in type:
        short_market_price = float(df.iloc[-1]["close"])
        short_quantity_in_usd = usd_balance * leverage
        short_quantity = float(bitget.convert_amount_to_precision(pair, float(
            bitget.convert_amount_to_precision(pair, short_quantity_in_usd / short_market_price)
        )))
        exchange_short_quantity = short_quantity * short_market_price
        print(
            f"Place Open Short Market Order: {short_quantity} {pair[:-5]} at the price of {short_market_price}$ ~{round(exchange_short_quantity, 2)}$"
        )
        if production:
            bitget.place_market_order(pair, "sell", short_quantity, reduce=False)

# Record the end execution time
now = datetime.now()
current_time = now.strftime("%d/%m/%Y %H:%M:%S")
print("--- End Execution Time :", current_time, "---")
