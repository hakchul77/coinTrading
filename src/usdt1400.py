from coin_main import main

TRADING_CONFIG = {
        "ticker": "USDT",
        "start_buy_price": 1400,
        "divide_count": 20,
        "order_qty": 300,
        "buy_interval": 1,
        "sell_interval": 2,
        "buy_margin": 2,  # 현재가가 매수가보다 이만큼 높아도 매수 시도 (기존 로직: buyInterval * 2)
        "loop_interval": 3,  # (초)
        "report_interval_loops": 300,
        "cancel_depth": 5,
        "max_up_strategies": 5,
        "save_interval_loops": 60,                       # 몇 루프마다 저장할지
        "snapshot_path": "snapshots/strategies.json",     # 저장 경로
}
main(TRADING_CONFIG)
