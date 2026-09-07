import asyncio
import logging
import sqlite3
from datetime import datetime, timezone, timedelta
from config import config
from database import init_db_sync, db
from ai_parser.groq_parser import groq_parser
from decision_engine.zone_aggregator import zone_aggregator
from decision_engine.execution_planner import execution_planner
from risk_manager.risk_manager import risk_manager, validate_and_clamp_risk_percent
from execution_engine.mt5_bridge import mt5_bridge
from news_filter.economic_calendar import news_filter
from reports.trade_journal import trade_journal_exporter
from telegram.listener import telegram_listener

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_rtb_v2_stage3")


async def test_rtb_v2_stage3():
    print("==================================================")
    print("🚀 TESTING RTB v2.0 STAGE 3: MT5 BRIDGE & NEWS FILTER")
    print("==================================================")

    # 1. Initialize DB
    print("\n--- 1. Testing Database Clean State ---")
    init_db_sync()
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = sorted([r[0] for r in cursor.fetchall()])
    conn.close()
    assert tables == ["channels", "settings", "signals", "trades"]
    print(f"Verified 4 Core Tables: {tables} ✅")

    # 2. Test Exness MT5 Bridge Operations
    print("\n--- 2. Testing Exness MT5 Bridge ---")
    mt5_bridge.initialize()
    sym_info = mt5_bridge.get_symbol_info()
    print(f"Symbol Specifications: Symbol={sym_info['symbol']}, Contract Size={sym_info['contract_size']} oz, Min Lot={sym_info['min_lot']}, Lot Step={sym_info['lot_step']}")
    assert sym_info["contract_size"] == 100.0, "Contract size for Gold must be 100.0"
    assert sym_info["min_lot"] == 0.01, "Min lot must be 0.01"

    tick = mt5_bridge.get_current_price()
    print(f"Current Price Tick: Bid={tick['bid']}, Ask={tick['ask']}, Mid={tick['mid']}")
    assert tick["mid"] > 2000.0, "Price tick must reflect valid gold price"

    # Test Market Order Execution
    order_res = mt5_bridge.execute_order(
        action="BUY",
        order_type="MARKET",
        price=2650.5,
        lot=0.50,
        sl=2645.0,
        tp=2665.0,
        comment="Test Market Order"
    )
    print(f"Market Order Result: {order_res}")
    assert order_res["success"] is True
    ticket = order_res["ticket"]

    # Test SL/TP Modification
    mod_ok = mt5_bridge.modify_sl_tp(ticket=ticket, new_sl=2648.0, new_tp=2668.0)
    print(f"Modify SL/TP Result: {mod_ok}")
    assert mod_ok is True

    # Test Partial & Full Position Close
    close_res = mt5_bridge.close_position(ticket=ticket, lot=0.25)
    print(f"Partial Close (0.25 lot): {close_res}")
    assert close_res["success"] is True

    full_close_res = mt5_bridge.close_position(ticket=ticket)
    print(f"Full Close: {full_close_res}")
    assert full_close_res["success"] is True

    # Test Pending Limit Order Execution & Cancellation
    limit_res = mt5_bridge.execute_order(
        action="SELL",
        order_type="LIMIT",
        price=2670.0,
        lot=0.20,
        sl=2675.0,
        tp=2655.0
    )
    limit_ticket = limit_res["ticket"]
    cancel_ok = mt5_bridge.cancel_order(limit_ticket)
    print(f"Cancel Pending Limit Order Result: {cancel_ok}")
    assert cancel_ok is True
    print("✅ Verified: Exness MT5 Bridge order execution and position lifecycle passed.")

    # 3. Test Automated Dynamic Lot Size Calculation Formula
    print("\n--- 3. Testing Dynamic Lot Size Calculation Formula ---")
    test_balances = [
        # (Balance, Entry, SL, Risk%, Expected Lot, Expected Risk USD)
        (1000.0, 2650.0, 2648.0, 1.0, 0.05, 10.0),    # $1000 * 1% = $10 / (100 * 2.0) = 0.05 lot
        (5000.0, 2650.0, 2646.0, 2.0, 0.25, 100.0),   # $5000 * 2% = $100 / (100 * 4.0) = 0.25 lot
        (10000.0, 2650.0, 2645.0, 5.0, 1.00, 500.0),  # $10000 * 5% = $500 / (100 * 5.0) = 1.00 lot
        (50000.0, 2650.0, 2647.5, 3.0, 6.00, 1500.0), # $50000 * 3% = $1500 / (100 * 2.5) = 6.00 lot
    ]

    for balance, entry, sl, risk_pct, exp_lot, exp_risk in test_balances:
        lot, risk_usd, eff_pct, err = risk_manager.calculate_lot_size(
            account_balance=balance,
            entry_price=entry,
            sl_price=sl,
            risk_percent=risk_pct
        )
        print(f"Balance: ${balance} | SL: {abs(entry-sl):.1f} pts | Risk: {risk_pct}% => Lot: {lot} (Exp: {exp_lot}), Risk USD: ${risk_usd}")
        assert lot == exp_lot, f"Lot size mismatch: {lot} != {exp_lot}"
        assert risk_usd == exp_risk, f"Risk USD mismatch: {risk_usd} != {exp_risk}"
        assert eff_pct == risk_pct

    # Test Guardrail Clamping in lot calculation
    clamped_lot, clamped_usd, clamped_pct, _ = risk_manager.calculate_lot_size(
        account_balance=10000.0,
        entry_price=2650.0,
        sl_price=2648.0,
        risk_percent=12.0  # Must clamp to 5.0%
    )
    assert clamped_pct == 5.0
    assert clamped_lot == 2.5
    print("✅ Verified: Automated Dynamic Lot Sizing Formula with Guardrail passed.")

    # 4. Test High-Impact News Filter (CPI, NFP, FOMC)
    print("\n--- 4. Testing High-Impact Economic News Filter ---")
    news_filter.clear_manual_events()

    # Case A: Normal conditions (No high impact news)
    is_paused, reason = await news_filter.is_news_pause_active()
    print(f"Normal Market Check: is_paused={is_paused} ({reason})")
    assert is_paused is False

    allowed, allow_reason = await news_filter.is_trading_allowed()
    assert allowed is True
    print("Normal Market Gatekeeper: Trading Allowed ✅")

    # Case B: Upcoming CPI news (10 minutes in the future -> inside 15 min pre-window)
    now_utc = datetime.now(timezone.utc)
    cpi_time = now_utc + timedelta(minutes=10)
    news_filter.add_manual_event(title="US Core CPI MoM", event_time=cpi_time, impact="HIGH")

    is_paused_cpi, cpi_reason = await news_filter.is_news_pause_active()
    print(f"Pre-CPI News Check (10 min before): is_paused={is_paused_cpi} | Reason: {cpi_reason}")
    assert is_paused_cpi is True, "Must pause 10 minutes before CPI"

    allowed_cpi, gate_cpi_reason = await news_filter.is_trading_allowed()
    assert allowed_cpi is False, "Trading must be blocked during CPI window"
    print(f"Gatekeeper during CPI: BLOCKED ✅ ({gate_cpi_reason})")

    # Case C: Post-NFP news (8 minutes after NFP -> inside 15 min post-window)
    news_filter.clear_manual_events()
    nfp_time = now_utc - timedelta(minutes=8)
    news_filter.add_manual_event(title="Nonfarm Payrolls (NFP)", event_time=nfp_time, impact="HIGH")

    is_paused_nfp, nfp_reason = await news_filter.is_news_pause_active()
    print(f"Post-NFP News Check (8 min after): is_paused={is_paused_nfp} | Reason: {nfp_reason}")
    assert is_paused_nfp is True, "Must pause 8 minutes after NFP"

    # Case D: News outside 15 min window (e.g., 40 minutes ahead) -> Should NOT pause
    news_filter.clear_manual_events()
    fomc_time = now_utc + timedelta(minutes=40)
    news_filter.add_manual_event(title="FOMC Statement", event_time=fomc_time, impact="HIGH")
    is_paused_fomc, fomc_reason = await news_filter.is_news_pause_active()
    assert is_paused_fomc is False, "Should NOT pause 40 minutes before FOMC"
    print("Outside Window Check (40 min before): NOT PAUSED ✅")
    print("✅ Verified: High-Impact News Filter (CPI, NFP, FOMC) 15-min auto-pause works accurately.")

    # 5. Test End-to-End Execution Flow via Execution Planner
    print("\n--- 5. Testing End-to-End Execution Flow ---")
    news_filter.clear_manual_events()

    sample_setup = {
        "direction": "BUY",
        "zone_min": 2648.0,
        "zone_max": 2652.0,
        "sl": 2645.0,
        "tp": 2665.0,
        "confidence_score": 80.0,
        "sources": [{"source": "TraderStage", "weight": 1.5}]
    }

    # Execute setup when market is clear
    exec_result = await execution_planner.evaluate_and_execute_setup(sample_setup, custom_risk_percent=2.0)
    print(f"Execution Result (Clear Market): {exec_result}")
    assert exec_result["success"] is True
    assert exec_result["risk_percent"] == 2.0
    trade_id = exec_result["trade_id"]

    # Verify open trade in SQLite DB
    open_trades = await db.get_open_trades()
    assert any(t["id"] == trade_id for t in open_trades)
    print(f"Trade confirmed in DB: Trade ID={trade_id} ✅")

    # Now simulate upcoming CPI and verify execution planner rejects new setups
    news_filter.add_manual_event(title="US CPI YoY", event_time=now_utc + timedelta(minutes=5))
    blocked_result = await execution_planner.evaluate_and_execute_setup(sample_setup)
    print(f"Execution Result (During CPI): {blocked_result}")
    assert blocked_result["success"] is False
    assert "NEWS PAUSE" in blocked_result["reason"]
    print("✅ Verified: Execution Planner successfully blocked trade during CPI window.")

    news_filter.clear_manual_events()
    print("\n🎉 ALL RTB v2.0 STAGE 3 TESTS PASSED SUCCESSFULLY!")


async def test_rtb_v2_stage4():
    """
    Verification suite for Stage 4: Telegram Mini App & FastAPI Backend.
    """
    from httpx import AsyncClient, ASGITransport
    from webapp.server import app as webapp_app

    print("\n==================================================")
    print("🧪 RUNNING RTB v2.0 STAGE 4 TEST SUITE (MINI APP & FASTAPI)")
    print("==================================================")

    transport = ASGITransport(app=webapp_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Test Static HTML Index Serving
        print("\n--- 1. Testing Mini App UI Serving (GET /) ---")
        resp = await client.get("/")
        print(f"GET / status code: {resp.status_code}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "RTB v2.0" in resp.text
        assert "Risk Guardrail" in resp.text
        assert "Master Kill-Switch" in resp.text
        print("✅ Verified: Mini App HTML Dark Mode template served successfully.")

        # 2. Test Real-time Status Endpoint (GET /api/status)
        print("\n--- 2. Testing System Status API (GET /api/status) ---")
        resp = await client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        print(f"Status API Response: Status={data.get('status')}, Symbol={data.get('symbol')}, Balance=${data.get('account', {}).get('balance')}")
        assert data["success"] is True
        assert "account" in data
        assert "price" in data
        assert "settings" in data
        assert "open_positions" in data
        assert "news_filter" in data
        assert "latest_signals" in data
        assert "trade_history" in data
        print("✅ Verified: /api/status returned comprehensive real-time trading state.")

        # 3. Test Strict Risk Setting Endpoint (POST /api/settings/risk)
        print("\n--- 3. Testing Risk Guardrail Endpoint (POST /api/settings/risk) ---")
        # Case A: Valid risk setting
        resp = await client.post("/api/settings/risk", json={"risk_percent": 2.5})
        assert resp.status_code == 200
        res_json = resp.json()
        assert res_json["risk_percent"] == 2.5
        assert res_json["was_clamped"] is False
        print(f"Valid Risk 2.5%: Applied={res_json['risk_percent']}% ✅")

        # Case B: Exceeds 5.0% guardrail limit -> must be clamped to 5.0%
        resp = await client.post("/api/settings/risk", json={"risk_percent": 8.5})
        assert resp.status_code == 200
        res_json = resp.json()
        assert res_json["risk_percent"] == 5.0
        assert res_json["was_clamped"] is True
        print(f"Over-limit Risk 8.5%: Clamped to {res_json['risk_percent']}% ✅")

        # Case C: Below 1.0% guardrail minimum -> must be clamped to 1.0%
        resp = await client.post("/api/settings/risk", json={"risk_percent": 0.4})
        assert resp.status_code == 200
        res_json = resp.json()
        assert res_json["risk_percent"] == 1.0
        assert res_json["was_clamped"] is True
        print(f"Under-limit Risk 0.4%: Clamped to {res_json['risk_percent']}% ✅")
        print("✅ Verified: Strict Risk Guardrail [1.0% - 5.0%] enforced on API.")

        # 4. Test Kill-switch Toggle (POST /api/settings/killswitch)
        print("\n--- 4. Testing Master Kill-switch (POST /api/settings/killswitch) ---")
        # Turn OFF
        resp = await client.post("/api/settings/killswitch", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["trading_enabled"] is False
        print("Kill-switch turned OFF (Paused) ✅")

        # Turn ON
        resp = await client.post("/api/settings/killswitch", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.json()["trading_enabled"] is True
        print("Kill-switch turned ON (Active) ✅")
        print("✅ Verified: Master Kill-switch controls bot execution state.")

        # 5. Test Position Close Endpoint (POST /api/trades/close)
        print("\n--- 5. Testing Manual Position Close (POST /api/trades/close) ---")
        # First place a simulated order
        order_res = mt5_bridge.execute_order(
            action="BUY",
            order_type="MARKET",
            price=2650.0,
            lot=0.10,
            sl=2640.0,
            tp=2670.0,
            comment="Mini App Close Test"
        )
        test_ticket = order_res["ticket"]
        print(f"Opened test position #{test_ticket} for close testing.")

        # Close position via API
        resp = await client.post("/api/trades/close", json={"ticket": test_ticket})
        assert resp.status_code == 200
        close_data = resp.json()
        print(f"Close API Result: {close_data}")
        assert close_data["success"] is True
        assert close_data["ticket"] == test_ticket
        print("✅ Verified: Position close API executed successfully.")

    print("\n🎉 ALL RTB v2.0 STAGE 4 TESTS PASSED ACCURATELY!")


async def run_all():
    await test_rtb_v2_stage3()
    await test_rtb_v2_stage4()


if __name__ == "__main__":
    asyncio.run(run_all())

