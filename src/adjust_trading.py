import os
import json
import time
import pyupbit
import logging
import requests
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict
from typing import Optional, Dict
from dotenv import load_dotenv
load_dotenv()


KST = timezone(timedelta(hours=9))
# ----------------------------------------------------------------------------
# 로깅 설정
# ----------------------------------------------------------------------------
# 로거 설정
log_path = Path("log")
log_path.mkdir(parents=True, exist_ok=True)
LOG_FILE_NAME = log_path / f"adjust_trading_{datetime.today().strftime('%Y%m%d')}.log"

logger = logging.getLogger("TradingBotLogger")
logger.setLevel(logging.INFO)
# 콘솔 핸들러 추가 (디버깅 시 유용)
# stream_handler = logging.StreamHandler()
# logger.addHandler(stream_handler)
# 파일 핸들러 추가
file_handler = RotatingFileHandler(LOG_FILE_NAME, maxBytes=100*1024*1024, backupCount=5)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# ----------------------------------------------------------------------------
# 환경 설정
# ----------------------------------------------------------------------------
STATE_FILE = "state.json"
INITIAL_CAPITAL = 1_000_000
INITIAL_BUY_RATIO = 0.4
INITIAL_BUY_PRICE: Optional[float] = None  # None이면 시장가
BUY_FLOOR_DROP = -0.10
PROFIT_TARGET = 0.01
LOSS_LIMIT = -0.01
ORDER_RATIO = 0.1
MAX_CONSECUTIVE_BUYS = 5

# ----------------------------------------------------------------------------
# 상태 데이터 클래스
# ----------------------------------------------------------------------------
@dataclass
class OrderState:
    sell_id: Optional[int] = None
    sell_price: Optional[float] = None
    sell_qty: Optional[float] = None
    buy_id: Optional[int] = None
    buy_price: Optional[float] = None
    buy_qty: Optional[float] = None
    buy_floor: Optional[float] = None
    consecutive_buys: int = 0
    is_execute: bool = True

# ----------------------------------------------------------------------------
# 상태 파일 관리
# ----------------------------------------------------------------------------
def send_discord_message(text: str):
    """디스코드 채널로 메시지 전송 (웹훅 JSON, 타임아웃/예외 처리 포함)"""
    discord_url = os.getenv("DISCORD_DCT_URL")
    if not discord_url:
        logger.warning("DISCORD_URL이 설정되지 않았습니다.")
        return
    payload = {"content": f"[{datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}] {text}"}
    try:
        requests.post(discord_url, json=payload, timeout=5)
    except requests.RequestException as e:
        logger.error(f"디스코드 메시지 전송 실패: {e}")

def load_state() -> OrderState:
    if os.path.isfile(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return OrderState(**json.load(f))
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
    return OrderState()

def save_state(state: OrderState):
    with open(STATE_FILE, 'w') as f:
        json.dump(asdict(state), f, indent=2)

# ----------------------------------------------------------------------------
# Upbit API Wrapper using pyupbit
# ----------------------------------------------------------------------------
class UpbitApi:
    def __init__(self, access_key, secret_key):
        self.upbit = pyupbit.Upbit(access_key, secret_key)

    def get_balance(self, symbol: str) -> Dict[str, float]:
        balances = self.upbit.get_balances()
        # print(balances)
        krw = next((float(b['balance']) for b in balances if b['currency'] == 'KRW'), 0.0)
        coin = next((float(b['balance']) for b in balances if b['currency'] == symbol.replace("KRW-", "")), 0.0)
        avg_price = next((float(b['avg_buy_price']) for b in balances if b['currency'] == symbol.replace("KRW-", "")), 0.0)
        return {
            'available_krw': krw,
            'total_coin': coin,
            'avg_price': avg_price,
        }

    def get_price(self, symbol: str) -> float:
        return pyupbit.get_current_price(symbol)

    def order(self, symbol: str, price: float, qty: float, side: str) -> Optional[str]:
        if side == 'buy':
            result = self.upbit.buy_limit_order(symbol, price, qty)
        else:
            result = self.upbit.sell_limit_order(symbol, price, qty)

        if result and 'uuid' in result:
            return result['uuid']
        logger.error(f"Order failed: {side} {qty}@{price} result={result}")
        return None

    def check_order_status(self, order_id: str) -> tuple[bool, float]:
        result = self.upbit.get_order(order_id)
        if result:
            filled_qty = sum(float(t['volume']) for t in result['trades']) if result['trades'] else 0.0
            total_qty = float(result['volume'])
            fully_filled = filled_qty >= total_qty
            return fully_filled, filled_qty
        return False, 0.0

    def cancel(self, order_id: str) -> bool:
        result = self.upbit.cancel_order(order_id)
        return result is not None

# ----------------------------------------------------------------------------
# 거래 로직
# ----------------------------------------------------------------------------
class TradingBot:
    def __init__(self, code: str, api: UpbitApi): # api: BithumbApi):
        self.code = code
        self.api = api
        self.state = OrderState()

    def start(self):
        logger.info("Starting bot")
        send_discord_message("Starting bot")

        bal = self.api.get_balance(self.code)
        if bal['total_coin'] == 0 or not self.state.buy_floor:
            self._initial_buy()
        self._main_loop()

    def _initial_buy(self):
        bal = self.api.get_balance(self.code)
        price = INITIAL_BUY_PRICE or self.api.get_price(self.code)
        amount = min(bal['available_krw'], INITIAL_CAPITAL) * INITIAL_BUY_RATIO
        qty = round(amount / price, 6)
        order_id = self.api.order(self.code, price, qty, 'buy')
        logger.info(f"Inital Buy ID: {order_id}, price: {price}, qty: {qty}")
        send_discord_message(f"Inital Buy ID: {order_id}, price: {price}, qty: {qty}")
        if order_id:
            self.state.buy_id = order_id
            self.state.buy_price = price
            self.state.buy_qty = qty
            self.state.buy_floor = price * (1 + BUY_FLOOR_DROP)
            save_state(self.state)
            self._await_fill(order_id, "buy")

    def _await_fill(self, order_id: str, side: str):
        while True:
            filled, qty = self.api.check_order_status(order_id)
            if filled:
                logger.info(f"{side.upper()} order fully filled: {qty} units")
                send_discord_message(f"{side.upper()} order fully filled: {qty} units")
                break
            elif qty > 0:
                logger.info(f"{side.upper()} order partially filled: {qty} units")
            else:
                logger.info("Waiting for fill...")
            time.sleep(5)

    def _main_loop(self):
        logger.info(f"Main Loop Started: {self.code}")
        send_discord_message(f"Main Loop Started: {self.code}")
        while True:
            if not self.state.is_execute:
                break

            # print("Checking for new orders...")
            if self.state.sell_id:
                filled, qty = self.api.check_order_status(self.state.sell_id)
                # print(f"Checking sell status:{self.state.sell_id} ")
                if filled:
                    logger.info(f"Sold:{self.state.sell_id}, price: {self.state.sell_price}, qty: {qty} ")
                    send_discord_message(f"Sold:{self.state.sell_id}, price: {self.state.sell_price}, qty: {qty} ")
                    self.state.sell_id = None
                    self.state.consecutive_buys = 0
                    save_state(self.state)
                    self._place_bracket_orders(False)

            if self.state.buy_id:
                filled, qty = self.api.check_order_status(self.state.buy_id)
                print(f"Checking buy status:{self.state.buy_id} ")
                if filled:
                    logger.info(f"Bought:{self.state.buy_id}, price: {self.state.buy_price}, qty: {qty} ")
                    send_discord_message(f"Bought:{self.state.buy_id}, price: {self.state.buy_price}, qty: {qty} ")
                    self.state.buy_id = None
                    self.state.consecutive_buys += 1
                    save_state(self.state)
                    self._place_bracket_orders(True)
            else:
                logger.debug("No filled orders.")
            time.sleep(5)

    def _place_bracket_orders(self, is_buy_fill: bool):
        if self.state.sell_id:
            logger.info(f"Cancel sell:{self.state.sell_id} ")
            self.api.cancel(self.state.sell_id)
            self.state.sell_id = None
        if self.state.buy_id:
            logger.info(f"Cancel buy:{self.state.buy_id} ")
            self.api.cancel(self.state.buy_id)
            self.state.buy_id = None

        bal = self.api.get_balance(self.code)
        cash = bal['available_krw']
        qty = bal['total_coin']
        avg_price = bal['avg_price'] #self.state.buy_price

        if is_buy_fill:
            base_sell_p = avg_price
            base_buy_p = self.state.buy_price
        else:
            base_sell_p = self.state.sell_price
            base_buy_p = avg_price

        if qty > 0:
            sell_p = round(base_sell_p * (1 + PROFIT_TARGET))
            sell_qty = round((INITIAL_CAPITAL * ORDER_RATIO) / sell_p, 6)
            sell_qty = min(sell_qty, qty)
            sid = self.api.order(self.code, sell_p, sell_qty, 'sell')
            logger.info(f"Order New Sell:{sid}, sell price:{sell_p}, sell qty:{sell_qty} ")
            send_discord_message(f"Order New Sell:{sid}, sell price:{sell_p}, sell qty:{sell_qty} ")
            if sid:
                self.state.sell_id = sid
                self.state.sell_price = sell_p
                self.state.sell_qty = sell_qty
        else:
            logger.info(f"All Coins were sold out !!! ")
            send_discord_message(f"All Coins were sold out !!! ")
            self.state.is_execute = False

            if self.state.sell_id:
                logger.info(f"Termination: Cancel sell:{self.state.sell_id} ")
                self.api.cancel(self.state.sell_id)
                self.state.sell_id = None
            if self.state.buy_id:
                logger.info(f"Termination: Cancel buy:{self.state.buy_id} ")
                self.api.cancel(self.state.buy_id)
                self.state.buy_id = None

            return

        if self.state.consecutive_buys < MAX_CONSECUTIVE_BUYS:
            buy_p = round(base_buy_p * (1 + LOSS_LIMIT))

            if cash <= 5000:
                logger.info(f"{cash} is insufficient !! ")
                send_discord_message(f"{cash} is insufficient !! ")
                return

            if buy_p >= self.state.buy_floor:
                buy_qty = round((INITIAL_CAPITAL * ORDER_RATIO) / buy_p, 6)
                buy_qty = min(buy_qty, round(cash / buy_p, 6))
                bid = self.api.order(self.code, buy_p, buy_qty, 'buy')
                logger.info(f"Order New Buy:{bid}, buy price:{buy_p}, buy qty:{buy_qty} ")
                send_discord_message(f"Order New Buy:{bid}, buy price:{buy_p}, buy qty:{buy_qty} ")
                if bid:
                    self.state.buy_id = bid
                    self.state.buy_price = buy_p
                    self.state.buy_qty = buy_qty
        else:
            logger.info(f"MAX_CONSECUTIVE_BUYS: {self.state.consecutive_buys} reached !!!")
            send_discord_message(f"MAX_CONSECUTIVE_BUYS: {self.state.consecutive_buys} reached !!!")
        save_state(self.state)

    def stop(self):
        logger.info("Stopping bot")
        send_discord_message("Stopping bot")
        for oid in [self.state.buy_id, self.state.sell_id]:
            if oid:
                self.api.cancel(oid)

# ----------------------------------------------------------------------------
# 실행
# ----------------------------------------------------------------------------
if __name__ == '__main__':
    ConnKey = os.getenv("UPBIT_ACCESS_KEY")
    SecKey = os.getenv("UPBIT_SECRET_KEY")

    bot = TradingBot("KRW-XRP", UpbitApi(ConnKey, SecKey))
    try:
        bot.start()
    except KeyboardInterrupt:
        bot.stop()
    except Exception as e:
        logger.critical(f"Unexpected error: {e}", exc_info=True)
        bot.stop()

