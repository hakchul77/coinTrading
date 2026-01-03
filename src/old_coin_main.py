# Install Package : python-dotenv, pybithumb, requests

import os, time
from datetime import datetime
import requests

from pybithumb import Bithumb
from dotenv import load_dotenv
load_dotenv()

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

###########################################################################################
### LOGER SETTING                                                                       
###########################################################################################
log_path = Path("log")  # 현재 디렉토리 기준
log_path.mkdir(parents=True, exist_ok=True)
LOG_FILE_NAME = log_path / f"trading_{datetime.today().strftime('%Y%m%d')}.log"
# LOG_FILE_NAME = ('/log/trading_' + str(datetime.datetime.now())[:10]+'.log')
# SOURCE_DIR = '/home/ubuntu/dev/trading/'

logger = logging.getLogger("Rotating Log")
logger.setLevel(logging.DEBUG)
    
# add a rotating handler
handler = RotatingFileHandler(LOG_FILE_NAME, maxBytes=100*1024*1024, backupCount=3)
logger.addHandler(handler)
###########################################################################################

# 디스코드 채널로 메세지 전송
discord_url = os.getenv("DISCORD_URL")

def discord_send_message(text):
    # message = {"content": f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {str(text)}"}
    message = {"content": f"{str(text)}"}
    requests.post(discord_url, data=message)
    # print(message)

# 여기서부터 Split Trading

# Split 전략 생성 (종목, 시작가격, 회차갯수, 주문갯수, 매수간격, 매도간격)
def make_strategies(ticker, startBuyPrice, divideCount, dealCount, buyInterval, sellInterval):
    strategies = {}
    for i in range(divideCount):
        strategy = {}
        strategy["id"] = i
        strategy["buyPrice"] = startBuyPrice - buyInterval * i
        strategy["sellPrice"] = startBuyPrice - buyInterval * i + sellInterval
        strategy["dealCount"] = dealCount
        strategy["isBuy"] = False
        strategy["checkBought"] = False
        strategy["isBought"] = False
        strategy["isSell"] = False
        strategy["checkSold"] = False
        strategy["status"] = 'Standby'
        strategies[i] = strategy
    return strategies

def check_my_balance(Bithumb, ticker):
    currentPrice = myBithumb.get_current_price(ticker)
    tradingFee = myBithumb.get_trading_fee(ticker)
    myBalance = myBithumb.get_balance(ticker)

    # print(f"------------------------------------------")
    logger.info(f"------------------------------------------")
    logger.info(f"### Start Trading ###")
    discord_send_message(f"### Start Trading ###")

    msg = f"{ticker}, Coin: {myBalance[0]}, Trading Coin: {myBalance[1]}, Balance: {myBalance[2]:.1f}, Trading Money: {myBalance[3]}"
    logger.info(msg)
    discord_send_message(msg)

    return currentPrice

# 매수 대상 회차 마킹 (하단 2개 더 추가) 
def check_buying(currentPrice, buyInterval, strategies):
    for strategy in strategies.values():
        if (not strategy["checkBought"] and not strategy["isBought"]) and ((currentPrice - buyInterval * 2) <= strategy["buyPrice"]) :
            # print(f"Buy {strategy['id']} at {currentPrice} based on buy price : {strategy['buyPrice']}")
            logger.info(f"Buy {strategy['id']} at {currentPrice} based on buy price : {strategy['buyPrice']}")
            strategy["isBuy"] = True

# 매수 대상 마킹된 거 매수 주문 실행
def buy_strategy(strategies, currentPrice):
    for strategy in strategies.values():
        if strategy["isBuy"]:
            order_result = myBithumb.buy_limit_order(ticker, strategy['buyPrice'], strategy["dealCount"])
            strategy["isBuy"] = False
            strategy["checkBought"] = True
            strategy["buyDesc"] = order_result
            strategy["status"] = 'Buying'

# 매수 주문 중 체결된거 체크하고 체결되었으면 매도 대상 마킹
def check_bought(strategies):
    for strategy in strategies.values():
        if strategy["checkBought"]:
            order_desc = strategy["buyDesc"]
            result = myBithumb.get_order_completed(order_desc)
            if result:
                result_data = result.get("data")
                if result_data:
                    if result_data["order_status"] == 'Completed':
                        strategy["checkBought"] = False
                        strategy["isBought"] = True
                        strategy["isSell"] = True
                        strategy["buyDesc"] = None

# 매도 대상 마킹된거 매도 주문
def sell_strategy(strategies, currentPrice):
    for strategy in strategies.values():
        if strategy["isSell"]:
            order_result = myBithumb.sell_limit_order(ticker, strategy['sellPrice'], strategy["dealCount"])
            strategy["isSell"] = False
            strategy["checkSold"] = True
            strategy["sellDesc"] = order_result
            strategy["status"] = 'Selling'

# 매도 되었는지 체크하고 체결되었으면 해당 회차 초기화
def check_sold(strategies):
    for strategy in strategies.values():
        if strategy["checkSold"]:
            order_desc = strategy["sellDesc"]
            result = myBithumb.get_order_completed(order_desc)
            if result:
                result_data = result.get("data")
                if result_data:
                    if result_data["order_status"] == 'Completed':
                        strategy["checkSold"] = False
                        strategy["isBought"] = False
                        strategy["sellDesc"] = None
                        strategy["status"] = 'Standby'

# 매수 주문만 취소
def cancel_order(strategies):
    for strategy in strategies.values():
        if strategy["checkBought"]:
            result = myBithumb.cancel_order(strategy["buyDesc"])
            strategy["buyDesc"] = None
        # elif strategy["checkSold"]:
        #     result = myBithumb.cancel_order(strategy["sellDesc"])
        #     strategy["sellDesc"] = None

if __name__ == "__main__":
    ConnKey = os.getenv("BITHUMB_ACCESS_KEY")
    SecKey = os.getenv("BITHUMB_SECRET_KEY")
    myBithumb = Bithumb(ConnKey, SecKey)

    # Define Strategy
    ticker = "DOGE"
    startBuyPrice = 309
    divideCount = 10
    dealCount = 200
    buyInterval = 1
    sellInterval = 1
    #-------------------

    strategies = make_strategies(ticker, startBuyPrice, divideCount, dealCount, buyInterval, sellInterval)
 
    currentPrice = check_my_balance(myBithumb, ticker)

    cnt = 0
    max_inter = 150000 # 계속 돌리려면 크게 하거나 해당 로직 삭제 
    while True:
        cnt += 1
        currentPrice = myBithumb.get_current_price(ticker)
        if not currentPrice:
            logger.info("CurrentPrice is None... Try again!!")
            continue
        # print(f"-----------------------------------------")
        # print(f"{cnt} / {max_inter}")
        # print(f"current price: {currentPrice}")
        logger.info(f"-----------------------------------------")
        logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
        logger.info(f"{cnt} / {max_inter}")
        logger.info(f"current price: {currentPrice}")
        if cnt > max_inter:
            cancel_order(strategies) # 매수 주문만 취소
            logger.info(f"### End Trading ###")
            discord_send_message(f"### End Trading ###")
            break
        
        # 600번 실행마다 Discord로 전송 (alive check 같은거...)
        if cnt % 300 == 0:
            discord_send_message(f"---------------------------------------------------")
            logger.info(f"---------------------------------------------------")
            discord_send_message(f"Current Price: {currentPrice} ")
            logger.info(f"Current Price: {currentPrice} ")

            concatenated_text = ""
            for strategy in strategies.values():
                if strategy["checkBought"] or strategy["checkSold"]:
                    tmp = f"{strategy['id']} : {strategy['status']}, Buy Price: {strategy['buyPrice']}, Sell Price: {strategy['sellPrice']}"
                    concatenated_text += tmp + "\n"
                    
            discord_send_message(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
            discord_send_message(concatenated_text)
            logger.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
            logger.info(concatenated_text)

        # print(f"### Check Buying ###")
        check_buying(currentPrice, buyInterval, strategies)
        # print(f"### Order Buying ###")
        buy_strategy(strategies, currentPrice)
        # print(f"### Check Bought ###")
        check_bought(strategies)
        # print(f"### Order Selling ###")
        sell_strategy(strategies, currentPrice)
        # print(f"### Check Sold ###")
        check_sold(strategies)

        time.sleep(3)

        # for strategy in strategies.values():
        #     print(f"{strategy['id']} : {strategy['status']}, Buy Price: {strategy['buyPrice']}, Sell Price: {strategy['sellPrice']}")
