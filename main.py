"""
Binance USDT-M Futures Volume Scanner — Entry Point.

Starts three concurrent components:
  1. Scanner          — scans all pairs every cycle
  2. Signal Tracker   — background price updater + take-profit alerts
  3. Command Listener — Telegram bot command handler
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from binance_client import BinanceClient
from market_cap import MarketCapProvider
from notifier import TelegramNotifier
from scanner import Scanner
from tracker import SignalTracker
from trader import Trader
from strategy_manager import StrategyManager
from bot_commands import TelegramCommandListener
from ws_price_monitor import WSPriceMonitor


def load_config(path: str = "config.json") -> dict:
    cfg_path = Path(path)
    if not cfg_path.exists():
        print(f"ERROR  config file not found: {path}")
        sys.exit(1)
    with open(cfg_path, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)

    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg.setdefault("telegram", {})["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.environ.get("TELEGRAM_CHAT_ID"):
        cfg.setdefault("telegram", {})["chat_id"] = os.environ["TELEGRAM_CHAT_ID"]
    if os.environ.get("BINANCE_API_KEY"):
        cfg.setdefault("binance", {})["api_key"] = os.environ["BINANCE_API_KEY"]
    if os.environ.get("BINANCE_API_SECRET"):
        cfg.setdefault("binance", {})["api_secret"] = os.environ["BINANCE_API_SECRET"]

    return cfg


def setup_logging(config: dict) -> None:
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_cfg.get("log_file", "scanner.log"), encoding="utf-8"),
    ]
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def validate_config(cfg: dict) -> None:
    required_keys = [
        ("telegram", "bot_token"),
        ("telegram", "chat_id"),
    ]
    for section, key in required_keys:
        value = cfg.get(section, {}).get(key, "")
        if not value or value.startswith("YOUR_"):
            logging.getLogger("main").error(
                "config.json  [%s][%s] is not set.", section, key
            )
            sys.exit(1)


def _start_health_server(port: int = 8080) -> None:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *args):
            pass

    server = HTTPServer(("0.0.0.0", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, name="health", daemon=True)
    thread.start()
    logging.getLogger("main").info("Health check server started on port %d", port)


def main() -> None:
    config = load_config()
    setup_logging(config)
    validate_config(config)
    logger = logging.getLogger("main")

    logger.info("=" * 60)
    logger.info("  Binance Futures Volume Scanner  —  starting")
    logger.info("=" * 60)

    _start_health_server(port=8180)

    # shared binance client
    rl = config.get("rate_limit", {})
    _is_testnet = config.get("binance", {}).get("testnet", False)
    binance = BinanceClient(
        api_key=config["binance"].get("api_key", ""),
        api_secret=config["binance"].get("api_secret", ""),
        delay_ms=rl.get("binance_delay_ms", 100),
        testnet=_is_testnet,
    )
    if _is_testnet:
        logger.info("🧪 TESTNET MODE — using demo-fapi.binance.com")
    else:
        logger.info("🔴 LIVE MODE — using fapi.binance.com")

    # shared telegram notifier
    notifier = TelegramNotifier(
        bot_token=config["telegram"]["bot_token"],
        chat_id=config["telegram"]["chat_id"],
    )
    if not notifier.validate():
        logger.error("Telegram validation failed — aborting.")
        sys.exit(1)

    # market cap provider (optional)
    mc_cfg = config.get("market_cap", {})
    market_cap = None
    if mc_cfg.get("enabled", False):
        market_cap = MarketCapProvider(
            cache_minutes=mc_cfg.get("cache_minutes", 120),
        )
        logger.info(
            "MarketCapProvider enabled (cache %d min)", mc_cfg.get("cache_minutes", 120)
        )
    else:
        logger.info("MarketCapProvider disabled")

    # tracker (optional)
    tracker_cfg = config.get("tracker", {})
    tracker = None
    tracker_thread = None
    cmd_listener = None
    cmd_thread = None

    if tracker_cfg.get("enabled", False):
        tracker = SignalTracker(config, binance, notifier, market_cap)

        tracker_thread = threading.Thread(
            target=tracker.run,
            name="tracker",
            daemon=True,
        )
        tracker_thread.start()

        cmd_listener = TelegramCommandListener(
            bot_token=config["telegram"]["bot_token"],
            chat_id=config["telegram"]["chat_id"],
            tracker=tracker,
            binance=binance,
            data_dir=config.get("tracker", {}).get("data_dir", "data"),
            # trader + strategy_mgr injected below after they are created
        )
        cmd_thread = threading.Thread(
            target=cmd_listener.run,
            name="commands",
            daemon=True,
        )
        cmd_thread.start()
        logger.info("Tracker + command listener started")
    else:
        logger.info("Tracker disabled")

    # trader (optional — requires trading.enabled = true + API credentials)
    trader = Trader(config, binance, notifier)
    trader_thread = None
    if trader.enabled:
        if not binance.has_trading_credentials():
            logger.warning(
                "Trading is enabled in config but BINANCE_API_KEY / BINANCE_API_SECRET "
                "are not set — trading will be skipped until credentials are provided."
            )
        trader_thread = threading.Thread(
            target=trader.run,
            name="trader",
            daemon=True,
        )
        trader_thread.start()
        logger.info("Trader monitoring loop started")

    # exit strategy manager (optional — requires exit_strategy.enabled = true)
    strategy_mgr = StrategyManager(config, binance, notifier, tracker=tracker)
    strategy_thread = None
    if strategy_mgr.enabled:
        if not binance.has_trading_credentials():
            logger.warning(
                "Exit strategy is enabled but BINANCE_API_KEY / BINANCE_API_SECRET "
                "are not set — strategy manager will be inactive until credentials provided."
            )
        strategy_thread = threading.Thread(
            target=strategy_mgr.run,
            name="strategy_mgr",
            daemon=True,
        )
        strategy_thread.start()
        logger.info("Exit strategy manager started")

    # Inject trader + strategy_mgr into command listener now that they exist
    if cmd_listener is not None:
        cmd_listener._trader       = trader
        cmd_listener._strategy_mgr = strategy_mgr
        logger.info("Test commands: trader + strategy_mgr injected into cmd_listener")

    # WebSocket real-time price monitor (started after strategy_mgr)
    ws_monitor = None
    if strategy_mgr.enabled:
        ws_monitor = WSPriceMonitor(strategy_manager=strategy_mgr, config=config)
        # Pass monitor to strategy_mgr so it can update symbols on trade open/close
        strategy_mgr.set_ws_monitor(ws_monitor)
        ws_monitor.start()
        logger.info("WebSocket real-time price monitor started")

    # scanner (main thread)
    scanner = Scanner(config, binance, notifier, tracker, market_cap, trader)

    def _shutdown(sig, _frame):
        logger.info("Received signal %s — shutting down …", sig)
        scanner.stop()
        if tracker:
            tracker.stop()
        if cmd_listener:
            cmd_listener.stop()
        trader.stop()
        strategy_mgr.stop()
        if ws_monitor:
            ws_monitor.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        scanner.run()
    except Exception:
        logger.critical("Fatal error", exc_info=True)
        sys.exit(1)

    logger.info("Shutdown complete.")


if __name__ == "__main__":
    main()
