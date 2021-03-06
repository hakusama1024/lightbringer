from pytz import utc

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ProcessPoolExecutor

import random
import os, sys, time, logging

from alpha_vantage.timeseries import TimeSeries
from alpha_vantage.techindicators import TechIndicators
from alpha_vantage.sectorperformance import SectorPerformances

import pandas, json

# Read API infos from config 
config_paht = '../config/myinfo.json'
if os.path.exists(config_paht):
    with open(config_paht, 'r') as f:
        info = json.load(f)
        ALPHAVANTAGE_API_TOKEN = info['alphavantage']['key']

g_ts = TimeSeries(key=ALPHAVANTAGE_API_TOKEN, output_format='pandas') 
g_ti = TechIndicators(key=ALPHAVANTAGE_API_TOKEN, output_format='pandas') 
g_sp = SectorPerformances(key=ALPHAVANTAGE_API_TOKEN, output_format='pandas') 
 
# global constants
MAX_API_RETRIES = 3 

# ti params 
g_intervals = ['1min', '5min', '15min', '60min']
slow_interval = '1min' 
fast_interval = '1min' 
ema_fast_period = 4 
ema_slow_period = 8 
series_type = 'close' 
track_interval = '1min'


def run(symbol):
    fast_ema = g_ti.get_ema(
        symbol=symbol, interval=fast_interval, time_period=ema_fast_period, series_type=series_type)
    slow_ema = g_ti.get_ema(
        symbol=symbol, interval=slow_interval, time_period=ema_slow_period, series_type=series_type)
    price = g_ts.get_intraday(
        symbol=symbol, interval=track_interval)
    print (price[1]['3. Last Refreshed'][-8:], price[0].tail(1)['close'][0])

jobstores = {
            'mongo': {'type': 'mongodb'},
                'default': SQLAlchemyJobStore(url='sqlite:///jobs.sqlite')
                }
executors = {
            'default': {'type': 'threadpool', 'max_workers': 20},
                'processpool': ProcessPoolExecutor(max_workers=5)
                }
job_defaults = {
            'coalesce': True,
                'max_instances': 3
                }
scheduler = BackgroundScheduler()
scheduler.configure(executors=executors, job_defaults=job_defaults, timezone=utc)

def foo(stuff):
    print (stuff, random.random())

scheduler.start()
j = scheduler.add_job(run, trigger='interval', args=['AMD'], seconds=1, id='AMD')
j.reschedule(trigger='interval', seconds = 3)

while True:
    key = input('command: ')
    if key.lower() == 'q':
        scheduler.shutdown(wait=False)
        break
    elif key.lower() == 'l':
        scheduler.print_jobs()
    
