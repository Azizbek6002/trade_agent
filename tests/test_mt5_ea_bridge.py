import threading
import time
import requests
from config import config

BASE_URL = "http://127.0.0.1:8000"
ADMIN_KEY = str(config.ADMIN_TELEGRAM_ID)
HEADERS = {
    "X-Admin-Key": ADMIN_KEY,
    "Content-Type": "application/json"
}

def test_mt5_ea_bridge():
    print("==================================================")
    print("🚀 STARTING MT5 EA BRIDGE VERIFICATION TEST")
    print("==================================================")

    # 1. Test Download EA Endpoint
    print("\n1. Testing GET /api/mt5/download-ea...")
    r_dl = requests.get(f"{BASE_URL}/api/mt5/download-ea", headers=HEADERS)
    assert r_dl.status_code == 200, f"Download EA failed with status {r_dl.status_code}"
    assert "RTB_Exness_Bridge.mq5" in r_dl.text
    assert "SendHeartbeatAndPoll" in r_dl.text
    print(f"✅ EA Download endpoint working! ({len(r_dl.text)} bytes)")

    # 2. Mock EA telemetry
    heartbeat_payload = {
        "account": 88997711,
        "server": "Exness-MT5Real9",
        "broker": "Exness Technologies Ltd",
        "balance": 6250.75,
        "equity": 6250.75,
        "margin": 0.0,
        "free_margin": 6250.75,
        "leverage": 2000,
        "currency": "USD",
        "symbol": "XAUUSDm",
        "bid": 4392.116,
        "ask": 4392.376,
        "open_positions": []
    }

    # 3. Background EA Worker (simulates Exness MT5 EA polling loop)
    stop_worker = False

    def ea_poll_worker():
        while not stop_worker:
            try:
                r = requests.post(
                    f"{BASE_URL}/api/mt5/heartbeat?admin_key={ADMIN_KEY}",
                    json=heartbeat_payload,
                    headers=HEADERS,
                    timeout=2.0
                )
                if r.status_code == 200:
                    data = r.json()
                    for action in data.get("actions", []):
                        act_id = action["action_id"]
                        act_type = action["type"]
                        print(f"   [MOCK EA] Polled Action: {act_id} ({act_type})")
                        if act_type == "OPEN_ORDER":
                            confirm_payload = {
                                "action_id": act_id,
                                "success": True,
                                "ticket": 77889900,
                                "price": action["price"],
                                "lot": action["lot"],
                                "error": ""
                            }
                            requests.post(
                                f"{BASE_URL}/api/mt5/confirm?admin_key={ADMIN_KEY}",
                                json=confirm_payload,
                                headers=HEADERS,
                                timeout=2.0
                            )
                            print(f"   [MOCK EA] Executed & Confirmed OPEN_ORDER #{77889900} for {act_id}")
                        elif act_type == "CLOSE_ORDER":
                            confirm_payload = {
                                "action_id": act_id,
                                "success": True,
                                "ticket": action.get("ticket", 77889900),
                                "price": 4393.50,
                                "lot": 0.02,
                                "error": ""
                            }
                            requests.post(
                                f"{BASE_URL}/api/mt5/confirm?admin_key={ADMIN_KEY}",
                                json=confirm_payload,
                                headers=HEADERS,
                                timeout=2.0
                            )
                            print(f"   [MOCK EA] Executed & Confirmed CLOSE_ORDER #{action.get('ticket')} for {act_id}")
            except Exception as ex:
                pass
            time.sleep(0.15)

    worker_thread = threading.Thread(target=ea_poll_worker, daemon=True)
    worker_thread.start()

    # Give worker time to send initial heartbeat
    time.sleep(0.5)

    # 4. Check /api/status to verify telemetry is live from EA
    print("\n2. Verifying /api/status telemetry reflects Exness EA...")
    r_status = requests.get(f"{BASE_URL}/api/status", headers=HEADERS)
    assert r_status.status_code == 200
    st = r_status.json()
    assert st["ea_bridge"]["connected"] is True, "EA bridge should be marked connected"
    assert st["ea_bridge"]["account"] == 88997711
    assert st["account"]["balance"] == 6250.75
    assert st["price"]["bid"] == 4392.116
    assert st["price"]["ask"] == 4392.376
    print(f"✅ Telemetry verified! Balance: ${st['account']['balance']}, Bid: {st['price']['bid']}, Ask: {st['price']['ask']}")

    # 5. Dispatch Order via Server API (expecting EA execution & real ticket)
    print("\n3. Dispatching Order to /api/trades/open (expecting Exness EA execution)...")
    order_req = {
        "action": "BUY",
        "order_type": "MARKET",
        "price": 4392.25,
        "lot": 0.03,
        "sl": 4382.00,
        "tp": 4412.00
    }
    r_order = requests.post(f"{BASE_URL}/api/trades/open", json=order_req, headers=HEADERS)
    assert r_order.status_code == 200, f"Order request failed: {r_order.text}"
    res = r_order.json()
    print("Order execution result:", res)
    assert res.get("success") is True, f"Order failed: {res}"
    assert res.get("ticket") == 77889900, f"Expected ticket 77889900 from EA, got {res.get('ticket')}"
    assert res.get("comment") == "Exness EA Executed"
    print("✅ Order executed and confirmed directly by remote Exness EA! Real Ticket: #77889900")

    # 6. Test Position Close via /api/trades/close
    print("\n4. Dispatching Close Order to /api/trades/close (ticket #77889900)...")
    close_req = {
        "ticket": 77889900,
        "lot": 0.03
    }
    r_close = requests.post(f"{BASE_URL}/api/trades/close", json=close_req, headers=HEADERS)
    assert r_close.status_code == 200, f"Close request failed: {r_close.text}"
    close_res = r_close.json()
    print("Close position result:", close_res)
    assert close_res.get("success") is True, f"Close failed: {close_res}"
    print("✅ Position closed and confirmed by remote Exness EA!")

    stop_worker = True
    print("\n==================================================")
    print("🎉 ALL MT5 EA BRIDGE TESTS PASSED FLAWLESSLY!")
    print("==================================================")

if __name__ == "__main__":
    test_mt5_ea_bridge()
