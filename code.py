# A CircuitPython example for the Raspberry Pi Pico W using cooperative multitasking via asyncio which sends data from a BMP180 sensor to an InfluxDB v2 server.
# https://julianmcconnell.com
# Version 20230926a

import board
import digitalio
import wifi
import socketpool
import bmp180
import adafruit_ntp
import time
import asyncio
import supervisor
import os
import busio
import adafruit_requests as requests
import ssl
import usyslog
from max6675 import MAX6675
from busio import I2C
from lcd_api import LcdApi
from i2c_lcd import I2cLcd

sck1 = board.GP2
cs1 = board.GP3
so1 = board.GP4

sck2 = board.GP6
cs2 = board.GP7
so2 = board.GP8

thermocouple1 = MAX6675(sck1, cs1, so1)
temp1 = thermocouple1.read()
print(temp1)

thermocouple2 = MAX6675(sck2, cs2, so2)
temp2 = thermocouple2.read()
print(temp2)

I2C_ADDR     = 0x27
I2C_NUM_ROWS = 2
I2C_NUM_COLS = 16

i2c = I2C(sda=board.GP0, scl=board.GP1, frequency=400000)
lcd = I2cLcd(i2c, I2C_ADDR, I2C_NUM_ROWS, I2C_NUM_COLS)

def celsius_to_fahrenheit(temp_celsius): 
    temp_fahrenheit = temp_celsius * (9/5) + 32 
    return temp_fahrenheit

# Load WiFi credentials from settings.toml
ssid = os.getenv('ssid')
psk = os.getenv('psk')

# socketpool
pool = socketpool.SocketPool(wifi.radio)

# Initialize BMP180 sensor
#i2c = busio.I2C(sda=board.GP0,scl=board.GP1,frequency=100000)
#bmp = bmp180.BMP180(i2c)
#bmp.mode = bmp180.MODE_HIGHRES

# Global variables for storing temperature and pressure readings
max6675_1_temperature = None
max6675_2_temperature = None

# Load InfluxDB details from settings.toml
INFLUXDB_URL_BASE = os.getenv('INFLUXDB_URL')
INFLUXDB_ORG = os.getenv('INFLUXDB_ORG')
INFLUXDB_BUCKET = os.getenv('INFLUXDB_BUCKET')
INFLUXDB_TOKEN = os.getenv('INFLUXDB_TOKEN')

# Construct the full InfluxDB URL with org and bucket parameters
INFLUXDB_URL = f"{INFLUXDB_URL_BASE}?org={INFLUXDB_ORG}&bucket={INFLUXDB_BUCKET}"

HEADERS = {
    "Authorization": f"Token {INFLUXDB_TOKEN}",
    "Content-Type": "application/json"
}

# Syslog server settings from settings.toml
SYSLOG_SERVER = os.getenv('SYSLOG_SERVER')
SYSLOG_SERVER_ENABLED = os.getenv('SYSLOG_SERVER_ENABLED') in ["TRUE", "true"]
SYSLOG_PORT = int(os.getenv('SYSLOG_PORT', 514))  # Default to 514 if not set

# Syslog server setup
if SYSLOG_SERVER_ENABLED:
    s = usyslog.UDPClient(pool, SYSLOG_SERVER, SYSLOG_PORT)

def log_to_syslog(level, message):
    if SYSLOG_SERVER_ENABLED:
        try:
            s.log(level, message)
        except RuntimeError:
            pass

# Read the BMP180
async def read_bmp180():
    global max6675_1_temperature
    global max6675_2_temperature

    while True:
        try:
            temp1 = thermocouple1.read()
            temp1F = celsius_to_fahrenheit(temp1)
            max6675_1_temperature = temp1F
            temp2 = thermocouple2.read()
            temp2F = celsius_to_fahrenheit(temp2)
            max6675_2_temperature = temp2F
            #bmp180_pressure = bmp.pressure
            #print(f"Temperature: {bmp180_temperature} C, Pressure: {bmp180_pressure} hPa")
            log_to_syslog(usyslog.S_INFO, f"Temperature1: {max6675_1_temperature} F, Temperature: {max6675_2_temperature} F")
        except RuntimeError as error:
            #print("BMP180 sensor error:", error.args[0])
            log_to_syslog(usyslog.S_ERR, "BMP180 sensor error:" + error.args[0])
        await asyncio.sleep(1)

# Connect to WiFi
async def wifi_connect():
    while True:
        if not wifi.radio.connected:
            #print("Attempting to connect to WiFi...")
            try:
                wifi.radio.connect(ssid, psk)
                #print(f"Connected to {ssid}")
            except (ConnectionError, wifi.RadioError) as e:
                #print("Failed to connect:", e)
                await asyncio.sleep(10)
        else:
            await asyncio.sleep(60)

# NTP time sync
async def ntp_time_sync():
    while not wifi.radio.connected:
        await asyncio.sleep(1)

    ntp = adafruit_ntp.NTP(pool, tz_offset=-7)

    while True:
        try:
            #print("Syncing time...")
            log_to_syslog(usyslog.S_INFO, "Syncing time...")
            current_time_struct = ntp.datetime
            formatted_time = f"{current_time_struct.tm_year}-{current_time_struct.tm_mon:02d}-{current_time_struct.tm_mday:02d} {current_time_struct.tm_hour:02d}:{current_time_struct.tm_min:02d}:{current_time_struct.tm_sec:02d}"
            #print(f"Time synchronized: {formatted_time}")
            log_to_syslog(usyslog.S_INFO, f"Time synchronized: {formatted_time}")
        except Exception as e:
            #print("Failed to sync time:", e)
            log_to_syslog(usyslog.S_ERR, "Failed to sync time:" + str(e))
        await asyncio.sleep(3600)

# Send data to InfluxDB v2 server
async def send_data_to_influxdb():
    global max6675_1_temperature
    global max6675_2_temperature

    while not wifi.radio.connected:
        await asyncio.sleep(1)

    ssl_context = ssl.create_default_context()
    http_session = requests.Session(pool, ssl_context)

    while True:
        if max6675_1_temperature is not None and max6675_2_temperature is not None:
            data = f"temperature1,device=temp1 value={max6675_1_temperature}\temperature2,device=temp2 value={max6675_2_temperature}"
            try:
                response = http_session.post(INFLUXDB_URL, headers=HEADERS, data=data)
                if response.status_code == 204:
                    #print("Data sent to InfluxDB successfully!")
                    log_to_syslog(usyslog.S_INFO, "Data sent to InfluxDB successfully!")
                else:
                    #print("Failed to send data to InfluxDB:", response.text)
                    log_to_syslog(usyslog.S_ERR, "Failed to send data to InfluxDB:" + response.text)
                response.close()
            except Exception as e:
                #print("Error sending data to InfluxDB:", e)
                log_to_syslog(usyslog.S_ERR, "Error sending data to InfluxDB:" + str(e))
        await asyncio.sleep(10)

# Main
async def main():
    asyncio.create_task(read_bmp180())
    asyncio.create_task(wifi_connect())
    asyncio.create_task(ntp_time_sync())
    asyncio.create_task(send_data_to_influxdb())

    while True:
        await asyncio.sleep(1)

asyncio.run(main())
