import asyncio
import logging
from config import config
from database import init_db_sync, db
from execution_engine.mt5_bridge import mt5_bridge
from ai_parser.groq_parser import groq_parser
from decision_engine.execution_planner import execution_planner
from news_filter.economic_calendar import news_filter
from httpx import AsyncClient, ASGITransport
from webapp.server import app as webapp_app

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("STAGE5_TEST")


async def test_real_signal_pipeline():
    print("\n==================================================")
    print("🚀 RTB v2.0 - 5-BOSQICH: REAL SIGNAL VA INTEGRATSIYA TESTI")
    print("==================================================")

    # 1. Initialize SQLite Database
    init_db_sync()
    print("1. SQLite ma'lumotlar bazasi tayyorlandi ✅")

    # 2. Exness MT5 Bridge Initialization with user's demo account credentials
    print(f"\n2. Exness MT5 Schyot ma'lumotlari tekshirilmoqda:")
    print(f"   • Login / Account: {config.MT5_ACCOUNT}")
    print(f"   • Server: {config.MT5_SERVER}")
    print(f"   • Symbol: {config.MT5_SYMBOL}")

    mt5_ok = mt5_bridge.initialize()
    print(f"   • MT5 Bridge holati: {'Ulangan (Real / Simulator Mode)' if mt5_ok else 'Xato'}")

    acc_info = mt5_bridge.get_account_info()
    price_info = mt5_bridge.get_current_price()
    print(f"   • Balans: ${acc_info['balance']} | Equity: ${acc_info['equity']}")
    print(f"   • XAUUSD Jonli Narx: Bid={price_info['bid']}, Ask={price_info['ask']}, Mid={price_info['mid']}")

    # 3. Simulate Real Signal Incoming from TraderStage channel
    real_channel_message = (
        "🏆 TRADERSTAGE VIP GOLD SIGNAL\n\n"
        "XAUUSD BUY NOW\n"
        "Entry Zone: 2650.0 - 2652.5\n"
        "SL: 2646.0\n"
        "TP1: 2658.0\n"
        "TP2: 2665.0\n"
        "Risk: Strict 2%\n"
        "Good luck traders!"
    )
    print("\n3. TraderStage kanalidan real signal xabari qabul qilindi:")
    print("--------------------------------------------------")
    print(real_channel_message)
    print("--------------------------------------------------")

    # 4. Parse using Gemini Pro Smart Parser
    print("\n4. Gemini Pro orqali xabar tahlil qilinmoqda (Smart Parsing)...")
    parsed = groq_parser.parse_message(real_channel_message)
    print(f"   • Parsed Natija: {parsed}")
    assert parsed.get("is_signal") is True
    assert parsed.get("direction") == "BUY"
    assert parsed.get("zone_min") == 2650.0
    assert parsed.get("zone_max") == 2652.5
    assert parsed.get("sl") == 2646.0
    print("   • Gemini Pro tahlili: A'lo darajada toza JSON formatiga o'tkazildi ✅")

    # 5. Save Signal to SQLite
    sig_id = await db.save_signal(
        source_type="CHANNEL",
        source_name="TraderStage VIP",
        direction=parsed["direction"],
        zone_min=parsed["zone_min"],
        zone_max=parsed["zone_max"],
        sl=parsed["sl"],
        tp_targets=parsed["tp_targets"],
        raw_text=real_channel_message
    )
    print(f"5. Signal bazaga saqlandi (Signal ID: {sig_id}) ✅")

    # 6. Execute via Decision Engine & Exness MT5 Bridge
    print("\n6. Execution Planner & MT5 Bridge orqali Exness schyotda order ochilmoqda...")
    setup = {
        "direction": parsed["direction"],
        "zone_min": parsed["zone_min"],
        "zone_max": parsed["zone_max"],
        "sl": parsed["sl"],
        "tp": parsed["tp_targets"][1] if len(parsed["tp_targets"]) > 1 else parsed["tp_targets"][0],
        "tp_targets": parsed["tp_targets"],
        "confidence_score": 85.0,
        "sources": [{"source": "TraderStage VIP", "weight": 1.5}]
    }

    exec_res = await execution_planner.evaluate_and_execute_setup(setup, custom_risk_percent=2.0)
    print(f"   • Order Natijasi: {exec_res}")
    assert exec_res.get("success") is True
    ticket = exec_res.get("ticket")
    lot = exec_res.get("lot")
    print(f"   • Exness MT5 Pozitsiyasi ochildi: Ticket #{ticket} | Hajmi: {lot} lot ✅")

    # 7. Verify Open Position via Mini App API
    print("\n7. Telegram Mini App API orqali tizim va ochiq pozitsiyalar tekshirilmoqda (/api/status)...")
    transport = ASGITransport(app=webapp_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        status_resp = await client.get("/api/status")
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        print(f"   • Mini App Status: {status_data['status']}")
        print(f"   • Balans: ${status_data['account']['balance']}")
        print(f"   • Ochiq pozitsiyalar soni: {len(status_data['open_positions'])}")

        pos_match = any(p["ticket"] == ticket for p in status_data["open_positions"])
        assert pos_match is True
        print(f"   • #{ticket} pozitsiyasi Mini Appda real vaqtda ko'rindi ✅")

        # 8. Close Trade via Mini App
        print(f"\n8. Mini App orqali #{ticket} pozitsiyasini yopish testi...")
        close_resp = await client.post("/api/trades/close", json={"ticket": ticket})
        assert close_resp.status_code == 200
        print(f"   • Yopish javobi: {close_resp.json()['message']} ✅")

    print("\n🎉 5-BOSQICH: BARCHA TIZIM INTEGRATSIYASI VA REAL SIGNAL TESTI 100% MUVAFFAQITYATLI YAKUNLANDI!")


if __name__ == "__main__":
    asyncio.run(test_real_signal_pipeline())
