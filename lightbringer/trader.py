import requests
import urllib
import pandas as pd
from Robinhood import Robinhood
from alpha_vantage.timeseries import TimeSeries
from alpha_vantage.techindicators import TechIndicators
from alpha_vantage.sectorperformance import SectorPerformances
from time import sleep

import json
import pushover
import schedule
import time
import datetime
import numpy as np
from requests.exceptions import ConnectionError
import os
import logging
import argparse
import getpass
from tabulate import tabulate
import cmd


logging.basicConfig(
    format='%(asctime)s: %(message)s',
    datefmt='%Y/%m/%d %I:%M:%S %p',
    level=logging.INFO)

# Configure these values in your OS environment variables
# Windows: http://www.dowdandassociates.com/blog/content/howto-set-an-environment-variable-in-windows-command-line-and-registry/
# Mac: http://osxdaily.com/2015/07/28/set-enviornment-variables-mac-os-x/
# Linux: https://www.cyberciti.biz/faq/set-environment-variable-linux/

#  ROBINHOOD_USERNAME = os.environ['ROBINHOOD_USERNAME']
#  ROBINHOOD_PASSWORD = os.environ['ROBINHOOD_PASSWORD']

#  ALPHAVANTAGE_API_TOKEN = os.environ[
#      'ALPHAVANTAGE_API_TOKEN']  # Get your free key at https://www.alphavantage.co/

#  # Signup account with Pushover and install on your phone so you can
#  # receive notifications
#  PUSHOVER_TOKEN = os.environ['PUSHOVER_TOKEN']
#  PUSHOVER_USERKEY = os.environ['PUSHOVER_USERKEY']

#  Parse arguments
parser = argparse.ArgumentParser(description="Command Arguments")
parser.add_argument(
    '-u',
    '--username',
    dest='rhusername',
    required=False,
    action='store',
    help='Robinhood username')
parser.add_argument(
    '-p',
    '--password',
    dest='rhpassword',
    required=False,
    action='store',
    help='Robinhood password')
parser.add_argument('-c', '--config', dest='configfile', required=False, action='store', default='config/info.json', help='path to your json file\
        which contains API informations of Pushover, AlphaVantage')
args = parser.parse_args()

# Login RH
# ROBINHOOD_USERNAME = ''
# ROBINHOOD_PASSWORD = ''


# Read API infos from config
config_paht = args.configfile
if os.path.exists(config_paht):
    with open(config_paht, 'r') as f:
        info = json.load(f)
        ALPHAVANTAGE_API_TOKEN = info['alphavantage']['key']
        PUSHOVER_TOKEN = info['pushover']['token']
        PUSHOVER_USERKEY = info['pushover']['key']

po_client = pushover.Client(PUSHOVER_USERKEY, api_token=PUSHOVER_TOKEN)

g_ts = TimeSeries(key=ALPHAVANTAGE_API_TOKEN, output_format='pandas')
g_ti = TechIndicators(key=ALPHAVANTAGE_API_TOKEN, output_format='pandas')
g_sp = SectorPerformances(key=ALPHAVANTAGE_API_TOKEN, output_format='pandas')

PO_TITLE = "Robinhood"
REFRESH_SECURITIES_INTERVAL = 4  # minutes
INTRADAY_SIGNAL_INTERVAL = 15  # minutes
MAX_API_RETRIES = 3

slow_interval = 'daily'
fast_interval = '15min'
ema_fast_period = 12
ema_slow_period = 26
series_type = 'close'

data = {}
msg_template = {
    "MACD_OWN_BUY": "",  # Owned stocks to HOLD: {}
    "MACD_OWN_SELL": "Owned stocks to SELL: {}",
    "MACD_WATCH_BUY": "Watched stocks to BUY: {}",
    "MACD_WATCH_SELL": "",  # Watched stocks to stay away from: {}
    "PRICE_CROSS_EMA_OWN_EXIT_LONG": "Owned stocks exit LONG - consider SELL: {}",
    "PRICE_CROSS_EMA_OWN_EXIT_SHORT": "",  # Owned stocks exit SHORT - to HOLD: {}
    "PRICE_CROSS_EMA_WATCH_EXIT_LONG": "",  # Watched stocks to stay away from: {}
    "PRICE_CROSS_EMA_WATCH_EXIT_SHORT": "Watched stocks exit SHORT - consider BUY: {}",
}
g_liststates = ['queued', 'unconfirmed', 'confirmed', 'partially_filled']

# list dedup function


def f7(seq):
    seen = set()
    seen_add = seen.add
    return [x for x in seq if not (x in seen or seen_add(x))]


def lst_to_str(lst):
    return ', '.join(lst).encode('ascii', 'ignore')


def is_market_open():
    # return True # for debugging
    now = datetime.datetime.now()
    return True
    return datetime.time(13, 30) <= now.time() <= datetime.time(20, 00)


def combine_security_lists(own_list, watch_list, exclude_list):
    l = []
    global owned_securities, g_watchlist
    owned_securities = []
    g_watchlist = []
    for security in own_list:
        req = requests.get(security['instrument'])
        data = req.json()
        owned_securities.append(data['symbol'])
        if data['symbol'] not in l and data['symbol'] not in exclude_list:
            l.append(data['symbol'])

    for security in watch_list:
        req = requests.get(security['instrument'])
        data = req.json()
        g_watchlist.append(data['symbol'])
        if data['symbol'] not in l and data['symbol'] not in exclude_list:
            l.append(data['symbol'])
    return l


# Global vars
# securities marked not supported by Alpha Vantage
unsupported_securities = ['ROKU']
owned_securities = []  # securities you currently own on Robinhood
g_watchlist = []  # securities you currently watch on Robinhood
combined_securities = []  # combination of owned and watched lists
g_openorders = {}

# Should execute these to refresh your holding securities
# on Robinhood every 2-5 minutes


def refresh_security_list_from_robinhood():
    logging.info("refresh_security_list_from_robinhood()")
    if is_market_open():

        global owned_securities, g_watchlist, combined_securities
        owned_securities = []
        g_watchlist = []
        combined_securities = []

        retry_count = 1
        while (retry_count <= MAX_API_RETRIES):
            try:
                combined_securities = combine_security_lists(
                    r.securities_owned()['results'],
                    r.securities_watched()['results'],
                    unsupported_securities)
                break
            except urllib2.HTTPError:  # Alpha Vantage API has high failure rate - retry 3 times
                logging.error(
                    "HTTPError exception calling Robinhood - Retry: {}/{}".format(
                        symbol, retry_count, MAX_API_RETRIES))
                retry_count = retry_count + 1
                sleep(0.5)

        print(owned_securities, g_watchlist, combined_securities)

# DAILY long interval evaluation of positions and provide suggestion on BUY / SELL signal on securities you own/watch
# Suggest to run 3 times: (1) before go to bed, (2) after market opens 30 minutes, (3) before market closes 30 minutes
# If MACD crosses over 0.0 - Signal BUY or SELL


def evaluate_daily_positions(symbols):
    logging.info("evaluate_daily_positions()")
    own_buy = []
    own_sell = []
    watch_buy = []
    watch_sell = []
    for symbol in g_watchlist:
        logging.info("processing {}".format(symbol))
        retry_count = 1
        while (retry_count <= MAX_API_RETRIES):
            try:
                macd = g_ti.get_macd(
                    symbol=symbol,
                    interval=slow_interval,
                    series_type=series_type)
                [macd_m2, macd_m1] = macd[0].tail(2)['MACD']
                if macd_m2 * macd_m1 <= 0:  # change direction
                    if macd_m2 <= 0:  # buy signal
                        if symbol in owned_securities:
                            own_buy.append(symbol)
                        else:
                            watch_buy.append(symbol)
                    else:  # sell signal
                        if symbol in owned_securities:
                            own_sell.append(symbol)
                        else:
                            watch_sell.append(symbol)
                # else:
                #     print symbol
                break
            except KeyError as e:
                logging.error(
                    "[{}] is not supported by Alpha Vantage.".format(symbol))
                break
            except urllib.HTTPError:  # Alpha Vantage API has high failure rate - retry 3 times
                logging.error(
                    "[{}] - Exception HTTPError - Retry: {}/{}".format(
                        symbol, retry_count, MAX_API_RETRIES))
                retry_count = retry_count + 1
                sleep(0.5)
        sleep(0.5)  # Time in seconds.

    if own_sell:
        logging.info(
            msg_template['MACD_OWN_SELL'].format(lst_to_str(own_sell)))
        po_client.send_message(
            msg_template['MACD_OWN_SELL'].format(
                lst_to_str(own_sell)), title=PO_TITLE)

    if watch_buy:
        logging.info(
            msg_template['MACD_WATCH_BUY'].format(lst_to_str(watch_buy)))
        po_client.send_message(
            msg_template['MACD_WATCH_BUY'].format(
                lst_to_str(watch_buy)), title=PO_TITLE)


# INTRADAY short interval evaluation of pisitions and provide suggestion on BUY / SELL signal on securities you own/watch
# If in LONG position and last price drops BELOW slow ema -> Exit LONG (can sell)
# IF in SHORT position and last price spikes ABOVE fast ema -> Exit SHORT (can buy)
# Run every x minutes - can use period defined in fast_interval - don't
# run this too frequent
def evaluate_intraday_positions():
    logging.info("evaluate_intraday_positions()")
    if is_market_open():
        own_buy = []
        own_sell = []
        watch_buy = []
        watch_sell = []
        global owned_securities, g_watchlist, combined_securities
        for symbol in combined_securities:
            logging.info("processing {}".format(symbol))
            retry_count = 1
            while (retry_count <= MAX_API_RETRIES):
                try:
                    fast_ema = g_ti.get_ema(
                        symbol=symbol,
                        interval=slow_interval,
                        time_period=ema_fast_period,
                        series_type=series_type)
                    slow_ema = g_ti.get_ema(
                        symbol=symbol,
                        interval=slow_interval,
                        time_period=ema_slow_period,
                        series_type=series_type)
                    price = g_ts.get_intraday(
                        symbol=symbol, interval=fast_interval, outputsize='compact')
                    last_fast_ema = fast_ema[0].tail(1)['EMA'][0]
                    last_slow_ema = slow_ema[0].tail(1)['EMA'][0]
                    last_price = price[0].tail(1)['close'][0]
                    if last_fast_ema > last_slow_ema:  # LONG
                        if last_price < last_slow_ema:
                            if symbol in owned_securities:
                                own_sell.append(symbol)
                            else:
                                watch_sell.append(symbol)
                    elif last_fast_ema < last_slow_ema:  # SHORT
                        if last_price > last_fast_ema:
                            if symbol in owned_securities:
                                own_buy.append(symbol)
                            else:
                                watch_buy.append(symbol)
                    else:  # EMA crossing!!!
                        msg = "[{}] EMAs crossing, must BUY/SELL now!".format(
                            symbol)
                        logging.info(msg)
                        po_client.send_message(msg, title=PO_TITLE)

                        # TODO: process crossing better
                        # if macd_m1 > macd_m2:
                        #     print "[{}] EMAs crossing, must BUY now!".format(symbol)
                        # else:
                        # print "[{}] EMAs crossing, must SELL
                        # now!".format(symbol)
                    break
                except KeyError as e:
                    logging.error(
                        "[{}] is not supported by Alpha Vantage.".format(symbol))
                    break
                except urllib.HTTPError:  # Alpha Vantage API has high failure rate - retry 3 times
                    logging.error(
                        "[{}] - Exception HTTPError - Retry: {}/{}".format(
                            symbol, retry_count, MAX_API_RETRIES))
                    retry_count = retry_count + 1
                    sleep(0.5)
            sleep(0.5)  # Time in seconds.

        if own_sell:
            logging.info(
                msg_template['PRICE_CROSS_EMA_OWN_EXIT_LONG'].format(
                    lst_to_str(own_sell)))
            po_client.send_message(
                msg_template['PRICE_CROSS_EMA_OWN_EXIT_LONG'].format(
                    lst_to_str(own_sell)), title=PO_TITLE)

        if watch_buy:
            logging.info(
                msg_template['PRICE_CROSS_EMA_WATCH_EXIT_SHORT'].format(
                    lst_to_str(watch_buy)))
            po_client.send_message(
                msg_template['PRICE_CROSS_EMA_WATCH_EXIT_SHORT'].format(
                    lst_to_str(watch_buy)), title=PO_TITLE)


def unusual_volume(symbols=g_watchlist, dlen=4):
    '''
    Check unusual volumes
    :param symbols:
    :param dlen: average volume window length, excluding the last tick
    :return:
    '''
    ret = []
    for sym in symbols:
        d = g_ts.get_intraday(sym, interval='5min')
        avgvol = sum(d[0][-1 - dlen:-1]['volume']) / dlen
        vol = int(d[0].tail(1)['volume'])
        if vol > (2 * avgvol):
            ret.append([sym, True])

    return ret


class TKshell(cmd.Cmd):
    intro = 'Welcome to the Robinhood shell. Type help or ? to list commands.\n'
    prompt = '> '

    # API Object
    trader = None

    # Cache file used to store instrument cache
    instruments_cache_file = 'instruments.data'

    # Maps Symbol to Instrument URL
    instruments_cache = {}

    # Maps URL to Symbol
    instruments_reverse_cache = {}

    def __init__(self):
        cmd.Cmd.__init__(self)
        self.trader = Robinhood()
        self.trader.login_prompt()

        try:
            data = open('instruments.data').read()
            self.instruments_cache = json.loads(data)
            for k in self.instruments_cache:
                self.instruments_reverse_cache[self.instruments_cache[k]] = k
        except BaseException:
            pass

    # ----- basic commands -----
    def do_l(self, arg):
        'Lists current portfolio'
        portfolio = self.trader.portfolios()
        print('Equity Value:', portfolio['equity'])

        account_details = self.trader.get_account()
        if 'margin_balances' in account_details:
            print(
                'Buying Power:',
                account_details['margin_balances']['unallocated_margin_cash'])

        positions = self.trader.securities_owned()

        symbols = ''
        buy_price_data = {}
        for position in positions['results']:
            symbol = self.get_symbol(position['instrument'])
            buy_price_data[symbol] = position['average_buy_price']
            symbols += symbol + ','
        symbols = symbols[:-1]

        raw_data = self.trader.quote_data(symbols)
        quotes_data = {}
        for quote in raw_data:
            quotes_data[quote['symbol']] = quote['last_trade_price']
        d = []
        for position in positions['results']:
            quantity = int(float(position['quantity']))
            symbol = self.get_symbol(position['instrument'])
            price = quotes_data[symbol]
            total_equity = float(price) * quantity
            buy_price = float(buy_price_data[symbol])
            p_l = total_equity - buy_price * quantity
            d.append([symbol, price, quantity, total_equity, buy_price, p_l])

        column_headers = [
            "symbol",
            "current price",
            "quantity",
            "total equity",
            "cost basis",
            "p/l"]
        table = tabulate(d, headers=column_headers)
        print(table)

    def do_q(self, arg):
        'Get quote for stock q <symbol>'
        symbols = arg.strip()
        headers = [
            'symbol',
            'price',
            'bid_size',
            'bid_price',
            'ask_size',
            'ask_price']
        try:
            data = self.trader.get_quote_list(
                symbols, 'symbol,last_trade_price,bid_size,bid_price,ask_size,ask_price')
            print(tabulate(data, headers=headers))
        except BaseException:
            print("Error getting quote for:", symbols)

    def do_b(self, arg):
        'Buy stock b <symbol> <quantity> <price>'
        parts = arg.split()
        if len(parts) == 3:
            symbol = parts[0]
            quantity = parts[1]
            price = round(float(parts[2]), 2)

            stock_instrument = self.trader.instruments(symbol)[0]
            res = self.trader.place_buy_order(
                stock_instrument, quantity, price)

            if not (res.status_code == 200 or res.status_code == 201):
                print("Error executing order")
                try:
                    data = res.json()
                    if 'detail' in data:
                        print(data['detail'])
                except BaseException:
                    pass
            else:
                print("Done")
        else:
            print("Bad Order")

    def do_qb(self, arg):
        '''
        Quick Buy
        a quick buy is a limit buy which price is set at previous close + 15% * previous candle body
        :param arg: <symbol> <quantity>
        :return: order detail
        '''
        parts = arg.split()
        if len(parts) == 2:
            symbol = parts[0]
            quantity = parts[1]
            d = g_ts.get_intraday(symbol=symbol, interval='5min')[0][-2:-1]
            o = float(d['open'])
            c = float(d['close'])
            price = round(min(o, c) + 0.15 * abs(o - c), 2)

            stock_instrument = self.trader.instruments(symbol)[0]
            res = self.trader.place_buy_order(
                stock_instrument, quantity, price)

            if not (res.status_code == 200 or res.status_code == 201):
                print("Error executing order")
                try:
                    data = res.json()
                    if 'detail' in data:
                        print(data['detail'])
                except BaseException:
                    pass
            else:
                print("Done")
        else:
            print("Bad Order")

    def do_s(self, arg):
        'Sell stock s <symbol> <quantity> <price>'
        parts = arg.split()
        if len(parts) == 3:
            symbol = parts[0]
            quantity = parts[1]
            price = float(parts[2])

            stock_instrument = self.trader.instruments(symbol)[0]
            res = self.trader.place_sell_order(
                stock_instrument, quantity, price)

            if not (res.status_code == 200 or res.status_code == 201):
                print("Error executing order")
                try:
                    data = res.json()
                    if 'detail' in data:
                        print(data['detail'])
                except BaseException:
                    pass
            else:
                print("Done")
        else:
            print("Bad Order")

    def do_o(self, arg):
        'List open orders'
        open_orders = self.trader.order_history()['results'][:10]
        if open_orders:
            column_headers = [
                "index",
                "symbol",
                "state",
                "price",
                "quantity",
                "type",
                "id"]
            global g_openorders, g_liststates
            g_openorders = []
            count = 1
            for order in open_orders:
                if order['state'] in g_liststates:
                    g_openorders.append(
                        [count,
                         self.get_symbol(order['instrument']),
                         order['state'],
                         order['price'],
                         int(float(order['quantity'])),
                         order['side'],
                         order['id']
                         ])
                    count += 1
            g_openorders.reverse()
            table = tabulate(g_openorders, headers=column_headers)
            print(table)
        else:
            print("No Open Orders")

    def do_c(self, arg):
        '''
        Cancel an open order by index return in list orders
        :param arg:
        :return:
        '''
        order_index = int(arg.strip())
        try:
            self.trader.cancel_order(g_openorders[-order_index][-1])
        except Exception as e:
            print("Error executing cancel\n", e)
        print(g_openorders[-order_index], "has been cancelled.")

    def do_u(self, arg):
        '''
        refresh the watch list, combining positions owned with symbol in wl.json
        :param arg:
        :return:
        '''
        positions = self.trader.securities_owned()
        global g_watchlist
        g_watchlist = []
        for position in positions['results']:
            g_watchlist.append(self.trader.quote_data(position['instrument']))
            # TODO maybe add pre-calculated price level in the json file as
            # well
        with open('wl.json', 'r') as f:
            d = json.load(f)
            for each in d:
                g_watchlist.append(each)

        print("Watchlist updated")
        print(tabulate(d))

    def do_r(self, arg):
        '''
        rate
        :param arg:
        :return:
        '''

    def do_bye(self, arg):
        open(
            self.instruments_cache_file,
            'w').write(
            json.dumps(
                self.instruments_cache))
        return True

    # ------ utils --------
    def get_symbol(self, url):
        if url not in self.instruments_reverse_cache:
            self.add_instrument_from_url(url)

        return self.instruments_reverse_cache[url]

    def add_instrument_from_url(self, url):
        data = self.trader.get_url(url)
        symbol = data['symbol']
        self.instruments_cache[symbol] = url
        self.instruments_reverse_cache[url] = symbol


def parse(arg):
    'Convert a series of zero or more numbers to an argument tuple'
    return tuple(map(int, arg.split()))


logging.info("Service start!")

#  refresh_security_list_from_robinhood()
# evaluate_daily_positions()
# evaluate_intraday_positions()
#
# # Refresh security list from Robinhood every X minutes
# schedule.every(REFRESH_SECURITIES_INTERVAL).minutes.do(
#     refresh_security_list_from_robinhood)
#
#
# # DAILY long interval evaluation of positions and provide suggestion on BUY / SELL signal on securities you own/watch
# # Suggest to run 3 times: (1) before go to bed, (2) after market opens 30
# # minutes, (3) before market closes 30 minutes
# schedule.every().day.at("06:00").do(evaluate_daily_positions)
#                #6AM UTC ~ 11PM PST - before go to bed
# schedule.every().day.at("14:00").do(evaluate_daily_positions)
#                #2PM UTC ~ 10AM EST - 30 after market open
# schedule.every().day.at("19:30").do(evaluate_daily_positions)
#                #7.30PM UTC ~ 3.30PM EST - 30 before market close
#
# # INTRADAY short interval evaluation of pisitions and provide suggestion
# # on BUY / SELL signal on securities you own/watch
# schedule.every(INTRADAY_SIGNAL_INTERVAL).minutes.do(
#     evaluate_intraday_positions)

if __name__ == '__main__':
    TKshell().cmdloop()
