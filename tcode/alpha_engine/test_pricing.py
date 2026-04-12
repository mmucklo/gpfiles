import yfinance as yf
import requests
from bs4 import BeautifulSoup
import re

def get_yfinance():
    ticker = yf.Ticker("TSLA")
    # try fast_info first
    try:
        return float(ticker.fast_info['lastPrice'])
    except:
        return float(ticker.history(period="1d")["Close"].iloc[-1])

def get_google_scrape():
    url = "https://www.google.com/finance/quote/TSLA:NASDAQ"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}
    res = requests.get(url, headers=headers)
    soup = BeautifulSoup(res.text, "html.parser")
    # google finance often uses data-last-price or a class
    el = soup.find("div", {"class": "YMlKec fxKbKc"})
    if el:
        return float(el.text.replace("$", "").replace(",", ""))
    raise ValueError("Google Scrape fail")

def get_cnbc_scrape():
    url = "https://www.cnbc.com/quotes/TSLA"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    res = requests.get(url, headers=headers)
    soup = BeautifulSoup(res.text, "html.parser")
    # CNBC often has QuoteStrip-lastPrice
    el = soup.find("span", {"class": "QuoteStrip-lastPrice"})
    if el:
        return float(el.text.replace(",", ""))
    raise ValueError("CNBC Scrape fail")

try:
    print(f"YF: {get_yfinance()}")
except Exception as e:
    print(f"YF FAIL: {e}")

try:
    print(f"GOOGLE: {get_google_scrape()}")
except Exception as e:
    print(f"GOOGLE FAIL: {e}")

try:
    print(f"CNBC: {get_cnbc_scrape()}")
except Exception as e:
    print(f"CNBC FAIL: {e}")
