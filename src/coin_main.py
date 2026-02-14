
import os
import time
import logging
import json
import signal
from datetime import datetime, timezone, timedelta
from pathlib import Path

# External packages
import requests
from pybithumb import Bithumb
from dotenv import load_dotenv

# Local modules
from .utils import setup_logger, send_discord_message

from dataclasses import dataclass, field
from typing import Optional, List

# --- 상수 정의 ---
STANDBY = 'STANDBY'  # 대기
BUYING = 'BUYING'    # 매수 주문 진행 중
ACTIVE = 'ACTIVE'    # 매수 완료 (매도 대기)
SELLING = 'SELLING'  # 매도 주문 진행 중

KST = timezone(timedelta(hours=9))

# --- 환경 설정 ---
load_dotenv()

# 로거 설정 (utils 사용)
logger = setup_logger("TradingBotLogger", "trading")

# --- 유틸리티 함수 ---
def save_strategies_snapshot(strategies: List['Strategy'], filepath: str):
    """전략 리스트를 JSON으로 저장"""
    try:
        snapshot_path = Path(filepath)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)

        data = [s.to_dict() for s in strategies]
        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"전략 스냅샷 저장: {filepath} (개수: {len(data)})")
    except Exception as e:
        logger.error(f"전략 스냅샷 저장 실패: {e}")

def load_strategies_snapshot(filepath: str) -> Optional[List['Strategy']]:
    """저장된 전략 JSON을 읽어와서 Strategy 객체 리스트로 반환"""
    snapshot_path = Path(filepath)
    if not snapshot_path.exists():
        logger.info(f"저장된 스냅샷 없음: {filepath}")
        return None

    try:
        with open(snapshot_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        strategies = []
        for item in data:
            # 필수 필드 체크 (기존 데이터 호환성 고려)
            if 'strategy_id' not in item:
                continue
                
            s = Strategy(
                strategy_id=item['strategy_id'],
                buy_price=item['buy_price'],
                sell_price=item['sell_price'],
                order_qty=item['order_qty'],
                status=item.get('status', STANDBY),
                order_id=item.get('order_id'),
                # last_action_at은 문자열에서 datetime으로 변환 (ISO 포맷 가정)
            )
            if 'last_action_at' in item:
                try:
                    s.last_action_at = datetime.fromisoformat(item['last_action_at'])
                except ValueError:
                    s.last_action_at = datetime.now(KST)
            strategies.append(s)
            
        logger.info(f"전략 스냅샷 로드 완료: {len(strategies)}개")
        return strategies
    except Exception as e:
        logger.error(f"전략 스냅샷 로드 실패: {e}")
        return None

def load_config(config_path: str = "config.json") -> dict:
    """설정 파일 로드"""
    try:
        # 실행 파일 기준 경로 계산 (src 폴더 내에 있다고 가정)
        base_dir = Path(__file__).parent
        path = base_dir / config_path
        
        if not path.exists():
            # src 상위에서 실행했을 경우 대비
            path = Path(config_path)
            
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.critical(f"설정 파일({config_path}) 로드 실패: {e}")
        raise

# --- 핵심 로직: Strategy 클래스 ---
@dataclass
class Strategy:
    """단일 분할매매 전략"""
    strategy_id: int
    buy_price: float  # float로 변경 (소수점 가격 대응)
    sell_price: float
    order_qty: float

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

    def update(self, current_price, client: Bithumb, ticker: str, buy_margin, buy_interval, cancel_depth: int):
        try:
            if self.status == STANDBY:
                # 현재가가 (매수가 + 마진) 이하면 지정가 매수
                if current_price <= (self.buy_price + buy_margin):
                    self._place_order(client, 'buy', ticker)

            elif self.status == BUYING:
                # 현재가보다 cancel_depth * buy_interval 이상 밑에 있는 매수 대기 주문은 취소
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
            send_discord_message(f"[Strategy {self.strategy_id}] 오류: {e}")
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

        # 예수금 부족 체크 (매수 시)
        if order_type == 'buy':
            try:
                bal = client.get_balance(ticker)
                krw_avail = None
                if isinstance(bal, tuple) or isinstance(bal, list):
                     krw_avail = float(bal[2]) # [보유코인, 거래중코인, 보유원화, ... ]
                
                need_krw = float(price) * float(qty) * 1.002 # 수수료 버퍼 0.2%
                
                if krw_avail is not None and krw_avail < need_krw:
                     msg = (f"[Strategy {self.strategy_id}] 예수금 부족으로 매수 보류: "
                            f"필요 {need_krw:,.0f} KRW > 보유 {krw_avail:,.0f} KRW")
                     logger.warning(msg)
                     # send_discord_message(msg) # 너무 자주 올 수 있으므로 로그만
                     return
            except Exception as e:
                logger.warning(f"잔고 조회 실패: {e}")
                pass

        try:
            if order_type == 'buy':
                order_id = client.buy_limit_order(ticker, float(price), float(qty))
            else:
                order_id = client.sell_limit_order(ticker, float(price), float(qty))
        except Exception as e:
            logger.error(f"[Strategy {self.strategy_id}] {order_type.upper()} 주문 예외: {e}")
            send_discord_message(f"[Strategy {self.strategy_id}] {order_type.upper()} 주문 실패(예외): {e}")
            return

        if isinstance(order_id, tuple): # 가끔 tuple로 리턴되는 경우 방어
             order_id = order_id[0] if order_id else None

        if order_id:
            self.order_id = str(order_id)
            self.status = BUYING if order_type == 'buy' else SELLING
            self.last_action_at = datetime.now(KST)
            msg = f"[Strategy {self.strategy_id}] {order_type.upper()} 주문 제출: price={price}, qty={qty}, id={order_id}"
            logger.info(msg)
            send_discord_message(msg)
        else:
            msg = f"[Strategy {self.strategy_id}] {order_type.upper()} 주문 실패(응답 비정상): {order_id}"
            logger.error(msg)
            send_discord_message(msg)

    def _check_order_completion(self, client: Bithumb, order_type: str):
        if not self.order_id:
            return
        try:
            result = client.get_order_completed(self.order_id)
        except Exception as e:
            logger.error(f"[Strategy {self.strategy_id}] 체결 조회 실패: {e}")
            return

        # Bithumb API 응답 처리 (성공 시 status="0000")
        if not isinstance(result, dict) or result.get("status") != "0000":
            # API 오류 혹은 아직 체결 데이터 없음
            return
        
        data = result.get("data")
        if not data:
             return

        # data가 리스트인 경우도 있고 딕셔너리인 경우도 있음 (체결 내역)
        # 보통 get_order_completed는 체결된 내역 리스트 반환
        # order_status를 확인해야 함. pybithumb의 get_order_completed는 완료된 주문에 대해서만 정보를 줄 수도 있음.
        # 여기서는 pybithumb 동작 방식에 의존.
        
        # 상세 구현: order_status가 Completed인지 확인
        order_status = data.get("order_status")
        if order_status == 'Completed':
             # 완전 체결로 간주
             if order_type == 'buy':
                 self.status = ACTIVE
                 msg = f"[Strategy {self.strategy_id}] 매수 완전 체결! -> 매도 대기"
             else:
                 self.status = STANDBY
                 msg = f"[Strategy {self.strategy_id}] 매도 완전 체결! -> 초기화"
             
             logger.info(msg)
             send_discord_message(msg)
             self.order_id = None
             self.last_action_at = datetime.now(KST)

    def _cancel_open_order(self, client: Bithumb) -> bool:
        if self.status in [BUYING, SELLING] and self.order_id:
            try:
                client.cancel_order(self.order_id)
                logger.info(f"[Strategy {self.strategy_id}] 미체결 주문 취소: id={self.order_id}")
            except Exception as e:
                logger.error(f"[Strategy {self.strategy_id}] 주문 취소 실패: {e}")
                return False
            finally:
                # 상태 복구
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


def main(config_file: str = "config.json"):
    """메인 트레이딩 봇 로직"""
    
    # 설정 로드
    try:
        config = load_config(config_file)
        logger.info(f"설정 로드 완료: {config['ticker']} (파일: {config_file})")
    except Exception:
        return

    # Bithumb 클라이언트 초기화
    try:
        connect_key = os.getenv("BITHUMB_ACCESS_KEY")
        secret_key = os.getenv("BITHUMB_SECRET_KEY")
        if not connect_key or not secret_key:
            raise ValueError("BITHUMB keys not found in .env")
        bithumb_client = Bithumb(connect_key, secret_key)
    except Exception as e:
        logger.critical(f"Bithumb 클라이언트 초기화 실패: {e}")
        return

    # 전략 초기화 (스냅샷 로드 시도 -> 없으면 새로 생성)
    snapshot_path = config.get("snapshot_path", "snapshots/strategies.json")
    loaded_strategies = load_strategies_snapshot(snapshot_path)
    
    if loaded_strategies:
        strategies = loaded_strategies
        logger.info(f"기존 전략 상태를 복구했습니다 (총 {len(strategies)}개).")
    else:
        logger.info("저장된 전략이 없어 새로운 전략을 생성합니다.")
        strategies = [
            Strategy(
                strategy_id=i,
                buy_price=config["start_buy_price"] - (config["buy_interval"] * i),
                sell_price=config["start_buy_price"] - (config["buy_interval"] * i) + config["sell_interval"],
                order_qty=config["order_qty"]
            )
            for i in range(config["divide_count"])
        ]

    # 시작 알림
    try:
        balance = bithumb_client.get_balance(config["ticker"])
        # balance: (total_coin, in_use_coin, total_krw, in_use_krw)
        start_msg = (
            f"**트레이딩 봇 시작**\n"
            f"- Ticker: {config['ticker']}\n"
            f"- KRW: {float(balance[2]):,.0f}\n"
            f"- Coin: {float(balance[0]):,.4f}"
        )
        send_discord_message(start_msg)
    except Exception as e:
        logger.error(f"초기 잔고 조회 실패: {e}")

    killer = GracefulKiller()
    loop_count = 0
    
    while not killer.stop:
        try:
            loop_count += 1
            
            # 현재가 조회
            current_price = bithumb_client.get_current_price(config["ticker"])
            if not current_price:
                logger.warning("현재가 조회 실패. 재시도...")
                time.sleep(config["loop_interval"])
                continue

            # (상승장 대응) 위쪽 레벨 전략 추가 로직
            # 기존 로직 유지하되 config 참조
            max_up = config["max_up_strategies"]
            # 현재 전략 중 가장 높은 ID와 가장 높은 buy_price 찾기
            max_id = max([s.strategy_id for s in strategies]) if strategies else -1
            
            # "현재가가 가장 높은 매수 전략의 매수가보다 buy_interval 이상 높으면" 추가
            while len(strategies) < (config["divide_count"] + max_up):
                # 가장 높은 매수 설정가 구하기
                top_strategy = max(strategies, key=lambda s: s.buy_price)
                next_target = top_strategy.buy_price + config["buy_interval"]
                
                # 현재가가 다음 타겟보다 높아야 추가 (추격 매수)
                if current_price < next_target:
                    break
                
                # 새 전략 추가
                new_id = max_id + 1
                new_strategy = Strategy(
                    strategy_id=new_id,
                    buy_price=next_target,
                    sell_price=next_target + config["sell_interval"],
                    order_qty=config["order_qty"]
                )
                strategies.append(new_strategy)
                max_id = new_id
                
                msg = f"[Strategy {new_id}] 상승장 전략 추가: buy={next_target}"
                logger.info(msg)
                send_discord_message(msg)
                
                # 즉시 매수 시도
                new_strategy._place_order(bithumb_client, 'buy', config["ticker"])


            # 모든 전략 업데이트
            for strategy in strategies:
                strategy.update(
                    current_price, 
                    bithumb_client, 
                    config["ticker"], 
                    config["buy_margin"], 
                    config["buy_interval"], 
                    config["cancel_depth"]
                )

            # 리포트 및 저장
            if loop_count % config["report_interval_loops"] == 0:
                actives = [f"ID {s.strategy_id}: {s.status}" for s in strategies if s.status != STANDBY]
                if actives:
                    send_discord_message(f"**생존 신고**\n현재가: {current_price}\n진행중: {', '.join(actives)}")
                else:
                    logger.info(f"생존 신고 - 현재가: {current_price} (진행중 없음)")

            if loop_count % config["save_interval_loops"] == 0:
                save_strategies_snapshot(strategies, snapshot_path)

            time.sleep(config["loop_interval"])

        except Exception as e:
            logger.critical(f"메인 루프 에러: {e}")
            send_discord_message(f"메인 루프 에러: {e}")
            time.sleep(10)

    # 종료 처리
    logger.info("종료 시그널 감지. 정리 중...")
    save_strategies_snapshot(strategies, snapshot_path)
    
    # 미체결 취소 (선택사항 - config에 따라 다를 수 있으나 안전을 위해 취소 추천)
    cancel_cnt = 0
    for s in strategies:
        if s._cancel_open_order(bithumb_client):
            cancel_cnt += 1
            
    end_msg = f"봇 종료. 미체결 {cancel_cnt}건 취소 완료."
    logger.info(end_msg)
    send_discord_message(end_msg)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Coin Trading Bot")
    parser.add_argument("--config", type=str, default="config.json", help="Path to configuration file")
    args = parser.parse_args()
    
    main(args.config)
