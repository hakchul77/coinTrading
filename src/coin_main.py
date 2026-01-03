# 필요한 패키지를 설치하세요: python-dotenv, pybithumb, requests
import os
import time
import logging
import json
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
import requests
import signal
from pybithumb import Bithumb
from dotenv import load_dotenv

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone, timedelta

# --- 상수 정의 ---
# 거래 상태
STANDBY = 'STANDBY'  # 대기
BUYING = 'BUYING'  # 매수 주문 진행 중
ACTIVE = 'ACTIVE'  # 매수 완료 (매도 대기)
SELLING = 'SELLING'  # 매도 주문 진행 중

KST = timezone(timedelta(hours=9))

# --- 환경 설정 ---
load_dotenv()

# 로거 설정
log_path = Path("log")
log_path.mkdir(parents=True, exist_ok=True)
LOG_FILE_NAME = log_path / f"trading_{datetime.today().strftime('%Y%m%d')}.log"

logger = logging.getLogger("TradingBotLogger")
logger.setLevel(logging.INFO)
file_handler = RotatingFileHandler(LOG_FILE_NAME, maxBytes=100 * 1024 * 1024, backupCount=5, encoding="utf-8")
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


# --- 유틸리티 함수 ---
def send_discord_message(text: str):
    """디스코드 채널로 메시지 전송 (웹훅 JSON, 타임아웃/예외 처리 포함)"""
    discord_url = os.getenv("DISCORD_SCT_URL")
    if not discord_url:
        logger.warning("DISCORD_URL이 설정되지 않았습니다.")
        return
    payload = {"content": f"[{datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}] {text}"}
    try:
        requests.post(discord_url, json=payload, timeout=5)
    except requests.RequestException as e:
        logger.error(f"디스코드 메시지 전송 실패: {e}")

def save_strategies_snapshot(strategies, filepath: str):
    """전략 리스트를 JSON으로 저장"""
    try:
        snapshot_dir = Path(filepath).parent
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        data = [s.to_dict() for s in strategies]
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"전략 스냅샷 저장: {filepath} (개수: {len(data)})")
    except Exception as e:
        logger.error(f"전략 스냅샷 저장 실패: {e}")

# --- 핵심 로직: Strategy 클래스 ---
@dataclass
class Strategy:
    """단일 분할매매 전략"""
    strategy_id: int
    buy_price: int
    sell_price: int
    order_qty: int

    status: str = STANDBY
    order_id: Optional[str] = None
    last_action_at: datetime = field(default_factory=lambda: datetime.now(KST))

    def to_dict(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "buy_price": self.buy_price,
            "sell_price": self.sell_price,
            "order_qty": self.order_qty,
            "status": self.status,
            "order_id": self.order_id,
            "last_action_at": self.last_action_at.isoformat(),
        }

    def _print(self):
        print(
            f"strategy_id: {self.strategy_id}, buy_price: {self.buy_price}, sell_price: {self.sell_price}, order_qty: {self.order_qty}, status: {self.status}, order_id: {self.order_id}, last_action_at: {self.last_action_at}")

    def update(self, current_price, client: Bithumb, ticker: str, buy_margin, buy_interval, cancel_depth: int):
        try:
            if self.status == STANDBY:
                # 현재가가 (매수가 + 마진) 이하면 지정가 매수
                if current_price <= (self.buy_price + buy_margin):
                    self._place_order(client, 'buy', ticker)

            elif self.status == BUYING:
                # 현재가보다 5개 전략(= buy_interval * 5) 이상 '밑'에 있는 매수 대기 주문은 취소하여 예수금 확보
                threshold_price = current_price - (buy_interval * cancel_depth)
                if self.buy_price <= threshold_price:
                    if self._cancel_open_order(client):
                        msg = (f"[Strategy {self.strategy_id}] 매수 대기 주문 취소(예수금 확보): "
                               f"buy={self.buy_price}, 현재가={current_price}, 기준={threshold_price}")
                        logger.info(msg)
                        send_discord_message(msg)
                    return
                self._check_order_completion(client, 'buy')

            elif self.status == ACTIVE:
                # 즉시 매도 지정가 진입 (전략 의도 유지)
                self._place_order(client, 'sell', ticker)

            elif self.status == SELLING:
                self._check_order_completion(client, 'sell')

        except Exception as e:
            logger.error(f"[Strategy {self.strategy_id}] 업데이트 오류: {e}")
            send_discord_message(f" [Strategy {self.strategy_id}] 오류: {e}")
            # 상태를 단순 리셋하지 않고, 미체결이 있으면 취소 후 STANDBY로
            try:
                self._cancel_open_order(client)
            finally:
                self.status = STANDBY
                self.order_id = None

    def _place_order(self, client: Bithumb, order_type: str, ticker: str):
        price = self.buy_price if order_type == 'buy' else self.sell_price
        qty = self.order_qty

        # 안전장치
        if order_type == 'sell' and price <= self.buy_price:
            logger.warning(f"[Strategy {self.strategy_id}] 비정상 호가(매도가<=매수가). 매도 생략: {price} <= {self.buy_price}")
            return

        # 예수금(보유 KRW) 부족 체크: BUY일 때만 --> 확인 필요
        if order_type == 'buy':
            try:
                bal = client.get_balance(ticker)
                # 기대 형태: [보유코인, 거래중코인, 보유원화, 거래중원화, ...]
                krw_avail = None
                if isinstance(bal, dict) and len(bal) >= 3:
                    krw_avail = float(bal[2])
                # 필요 원화 = 가격 * 수량 (+ 수수료/버퍼 약간)
                need_krw = float(price) * float(qty)
                fee_buffer_ratio = 0.001  # 0.1% 정도 버퍼(원하면 조정/환경변수화)
                need_krw *= (1.0 + fee_buffer_ratio)

                if krw_avail is not None and krw_avail < need_krw:
                    msg = (f"[Strategy {self.strategy_id}] 예수금 부족으로 매수 보류: "
                        f"필요 {need_krw:,.0f} KRW > 보유 {krw_avail:,.0f} KRW "
                        f"(price={price}, qty={qty})")
                    logger.warning(msg)
                    send_discord_message(msg)
                    return
            except Exception as e:
                # 잔고 조회 실패 시, 안전을 위해 주문을 진행하지 않고 경고만 남김
                warn = f"[Strategy {self.strategy_id}] 잔고 조회 실패로 매수 보류: {e}"
                logger.warning(warn)
                send_discord_message(warn)
                return

        try:
            if order_type == 'buy':
                order_id = client.buy_limit_order(ticker, float(price), float(qty))
            else:
                order_id = client.sell_limit_order(ticker, float(price), float(qty))
        except Exception as e:
            logger.error(f"[Strategy {self.strategy_id}] {order_type.upper()} 주문 예외: {e}")
            send_discord_message(f" [Strategy {self.strategy_id}] {order_type.upper()} 주문 실패(예외): {e}")
            return

        if order_id:
            self.order_id = order_id
            self.status = BUYING if order_type == 'buy' else SELLING
            self.last_action_at = datetime.now(KST)
            msg = f"[Strategy {self.strategy_id}] {order_type.upper()} 주문 제출: price={price}, qty={qty}, id={order_id}"
            logger.info(msg)
            send_discord_message(msg)
        else:
            msg = f"[Strategy {self.strategy_id}] {order_type.upper()} 주문 실패(응답 비정상): {order_id}"
            logger.error(msg)
            send_discord_message(f" {msg}")

    def _check_order_completion(self, client: Bithumb, order_type: str):
        if not self.order_id:
            return
        try:
            result = client.get_order_completed(self.order_id)
        except Exception as e:
            logger.error(f"[Strategy {self.strategy_id}] 체결 조회 실패: {e}")
            return

        # 기대 형태: {"status":"0000","data":[{...}]}
        if not isinstance(result, dict) or result.get("status") != "0000":
            logger.error(f"[Strategy {self.strategy_id}] 체결 응답 비정상: {result}")
            return
        
        data = result.get("data")

        if not isinstance(data, dict) or not data :
            logger.info(f"[Strategy {self.strategy_id}] 체결 내역 없음(대기 중일 수 있음): {result}")
            return

        if data.get("order_status") == 'Completed':
            try:
                order_qty = float(data.get("order_qty", 0) or 0)
                contracts = data.get("contract") or []
                filled_qty = sum(float(c.get("units", 0) or 0) for c in contracts)
                # 상태 문자열이 Completed여도 부분 체결일 수 있으므로 수량 비교로 판단
                eps = 1e-12
                full_filled = abs(order_qty - filled_qty) <= eps
            except Exception as e:
                logger.error(f"[Strategy {self.strategy_id}] 체결 데이터 파싱 실패: {e} / raw={data}")
                return
            
            if full_filled:
                if order_type == 'buy':
                    self.status = ACTIVE
                    msg = f" [Strategy {self.strategy_id}] 매수 완전 체결! -> 매도 대기 (id={self.order_id}, qty={order_qty})"
                else:
                    self.status = STANDBY
                    msg = f" [Strategy {self.strategy_id}] 매도 완전 체결! -> 초기화 (id={self.order_id}, qty={order_qty})"
                logger.info(msg)
                send_discord_message(msg)
                self.order_id = None
                self.last_action_at = datetime.now(KST)
            else:
                # 부분 체결 진행 중 (잔량은 참고용 로그)
                remain = max(order_qty - filled_qty, 0.0)
                logger.info(
                    f"[Strategy {self.strategy_id}] 부분 체결 진행 중: filled={filled_qty}, "
                    f"ordered={order_qty}, remain={remain}"
                )

    def _cancel_open_order(self, client: Bithumb) -> bool:
        if self.status in [BUYING] and self.order_id:
            try:
                client.cancel_order(self.order_id)
                logger.info(f"[Strategy {self.strategy_id}] 미체결 주문 취소: id={self.order_id}")
            except Exception as e:
                logger.error(f"[Strategy {self.strategy_id}] 주문 취소 실패: {e}")
                return False
            finally:
                # BUYING 취소면 다시 STANDBY, SELLING 취소면 다시 ACTIVE로 되돌림
                self.status = STANDBY if self.status == BUYING else ACTIVE
                self.order_id = None
                self.last_action_at = datetime.now(KST)
            return True
        return False


# --- 메인 ---
class GracefulKiller:
    def __init__(self):
        self.stop = False
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, *args):
        self.stop = True


# --- 메인 실행 로직 ---
def main(trading_cfg: dict | None):
    """메인 트레이딩 봇 로직"""
    # --- 거래 설정 ---

    if trading_cfg is None:
        TRADING_CONFIG = {
            "ticker": "DOGE",
            "start_buy_price": 323,
            "divide_count": 5,
            "order_qty": 20,
            "buy_interval": 1,
            "sell_interval": 1,
            "buy_margin": 2,  # 현재가가 매수가보다 이만큼 높아도 매수 시도 (기존 로직: buyInterval * 2)
            "loop_interval": 3,  # (초)
            "report_interval_loops": 600,
            "cancel_depth": 5,
            "max_up_strategies": 5,
            "save_interval_loops": 120,                       # 몇 루프마다 저장할지
            "snapshot_path": "snapshots/strategies.json",     # 저장 경로
        }
    else:
        TRADING_CONFIG = trading_cfg

    # Bithumb 클라이언트 초기화
    try:
        connect_key = os.getenv("BITHUMB_ACCESS_KEY")
        secret_key = os.getenv("BITHUMB_SECRET_KEY")
        bithumb_client = Bithumb(connect_key, secret_key)
    except Exception as e:
        logger.critical(f"Bithumb 클라이언트 초기화 실패: {e}")
        return

    # 전략 리스트 생성
    strategies = [
        Strategy(
            strategy_id=i,
            buy_price=TRADING_CONFIG["start_buy_price"] - (TRADING_CONFIG["buy_interval"] * i),
            sell_price=TRADING_CONFIG["start_buy_price"] - (TRADING_CONFIG["buy_interval"] * i) + TRADING_CONFIG[
                "sell_interval"],
            order_qty=TRADING_CONFIG["order_qty"]
        )
        for i in range(TRADING_CONFIG["divide_count"])
    ]

    # 위로 추가된 전략 관리 상태값
    up_created = 0
    next_up_offset = 1  # start_buy_price + buy_interval * 1 부터 시작

    # 트레이딩 시작 알림
    my_balance = bithumb_client.get_balance(TRADING_CONFIG["ticker"])
    start_msg = (
        f" **트레이딩 봇 시작**\n"
        f" - 티커: {TRADING_CONFIG['ticker']}\n"
        f" - 보유수량: {my_balance[0]}\n"
        f" - 거래중수량: {my_balance[1]}\n"
        f" - 보유원화: {my_balance[2]:,.0f} KRW\n"
        f" - 거래중원화: {my_balance[3]:,.0f} KRW"
    )
    logger.info(start_msg.replace('\n', ' '))
    send_discord_message(start_msg)

    killer = GracefulKiller()
    loop_count = 0
    while not killer.stop:
        try:
            loop_count += 1

            # 현재가 조회
            current_price = bithumb_client.get_current_price(TRADING_CONFIG["ticker"])
            if not current_price:
                logger.warning("현재가를 가져올 수 없습니다. 다음 루프에서 재시도합니다.")
                time.sleep(TRADING_CONFIG["loop_interval"])
                continue

            logger.info(f"--- [Loop {loop_count}] 현재가: {current_price:,} KRW, New created: {up_created:,} ---")

            # (1) 상승 시 위쪽 전략을 하나씩 추가하며 즉시 매수, 최대 max_up_strategies까지
            while True:
                if up_created > TRADING_CONFIG["max_up_strategies"]:
                    break

                target_level = TRADING_CONFIG["start_buy_price"] + (TRADING_CONFIG["buy_interval"] * next_up_offset)
                if current_price < target_level:
                    break  # 아직 다음 위 레벨을 돌파하지 않음

                # 같은 레벨의 전략이 이미 있으면(중복 생성 방지) 생성 보류
                already_exists = any(s.buy_price == target_level for s in strategies)
                if already_exists:
                    break  # 다음 루프에서 다시 확인

                # 해당 레벨에 '매도 대기/매도 진행' 전략이 있으면 충돌 방지 위해 생성/매수 보류
                sell_conflict = any(
                    (s.sell_price == target_level) and (s.status in (ACTIVE, SELLING, BUYING)) for s in strategies)
                if sell_conflict:
                    break  # 326 매도 체결 완료될 때까지 대기

                new_id = max([s.strategy_id for s in strategies]) + 1 if strategies else 0
                new_buy = target_level
                new_sell = target_level + TRADING_CONFIG["sell_interval"]
                new_strategy = Strategy(
                    strategy_id=new_id,
                    buy_price=new_buy,
                    sell_price=new_sell,
                    order_qty=TRADING_CONFIG["order_qty"]
                )
                strategies.append(new_strategy)

                add_msg = (f"[Strategy {new_id}] 위 레벨 전략 추가: "
                           f"buy={new_buy}, sell={new_sell}, 현재가={current_price} (offset={next_up_offset})")
                logger.info(add_msg)
                send_discord_message(add_msg)

                # 즉시 매수는 '충돌 없을 때만' 진행 (위의 가드 통과 시에만 여기 도달)
                try:
                    new_strategy._place_order(bithumb_client, 'buy', TRADING_CONFIG["ticker"])
                except Exception as e:
                    logger.error(f"[Strategy {new_id}] 즉시 매수 제출 실패: {e}")

                up_created += 1
                next_up_offset += 1

            # [핵심] 모든 전략을 한번에 업데이트
            for strategy in strategies:
                strategy.update(current_price, bithumb_client, TRADING_CONFIG["ticker"], TRADING_CONFIG["buy_margin"],
                                TRADING_CONFIG["buy_interval"], TRADING_CONFIG["cancel_depth"])

            # 주기적 리포트
            if loop_count % TRADING_CONFIG["report_interval_loops"] == 0:
                report_text = f"** 생존 신고 (Loop {loop_count})**\n - 현재가: {current_price:,} KRW\n"
                active_strategies = []
                for s in strategies:
                    if s.status != STANDBY:
                        active_strategies.append(
                            f" - ID {s.strategy_id}: {s.status}, 매수 {s.buy_price}, 매도 {s.sell_price}")

                if active_strategies:
                    report_text += "**[진행중인 전략]**\n" + "\n".join(active_strategies)
                else:
                    report_text += " - 모든 전략 대기 중"

                send_discord_message(report_text)
                logger.info("주기적 리포트 전송 완료.")

            # 스냅샷 주기 저장
            if loop_count % TRADING_CONFIG["save_interval_loops"] == 0:
                save_strategies_snapshot(strategies, TRADING_CONFIG["snapshot_path"])

            time.sleep(TRADING_CONFIG["loop_interval"])

        except Exception as e:
            logger.critical(f"메인 루프에서 예측하지 못한 오류 발생: {e}")
            send_discord_message(f" **치명적 오류 발생**: {e}\n봇을 확인해야 합니다.")
            time.sleep(60)  # 오류 발생 시 잠시 대기

    # 트레이딩 종료 처리
    logger.info("최대 루프 횟수에 도달하여 트레이딩을 종료합니다. 미체결 주문을 취소합니다.")
    send_discord_message(" **트레이딩 종료 중...**\n미체결된 매수/매도 주문을 취소합니다.")
    # ⬇️ 종료 전 스냅샷
    save_strategies_snapshot(strategies, TRADING_CONFIG["snapshot_path"])

    cancelled_count = 0
    for strategy in strategies:
        if strategy._cancel_open_order(bithumb_client):
            cancelled_count += 1

    end_msg = f" **트레이딩 봇 종료**\n - 총 {cancelled_count}개의 주문을 취소했습니다."
    logger.info(end_msg)
    send_discord_message(end_msg)


if __name__ == "__main__":
    TRADING_CONFIG = {
        "ticker": "DOGE",
        "start_buy_price": 325,
        "divide_count": 20,
        "order_qty": 250,
        "buy_interval": 1,
        "sell_interval": 1,
        "buy_margin": 2,  # 현재가가 매수가보다 이만큼 높아도 매수 시도 (기존 로직: buyInterval * 2)
        "loop_interval": 3,  # (초)
        "report_interval_loops": 300,
        "cancel_depth": 5,
        "max_up_strategies": 10,
        "save_interval_loops": 60,                       # 몇 루프마다 저장할지
        "snapshot_path": "snapshots/strategies.json",     # 저장 경로        
    }
    main(TRADING_CONFIG)


