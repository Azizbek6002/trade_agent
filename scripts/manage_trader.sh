#!/usr/bin/env bash
# ==============================================================================
# 🤖 Robo Trader Boy (RTB v2.0) - All-in-One Process & Lifecycle Manager
# Commands: start | stop | restart | status | logs
# ==============================================================================

set -e

PROJECT_DIR="/home/azizbek/Desktop/Projects/trade_agent"
cd "$PROJECT_DIR"

LOG_DIR="$PROJECT_DIR/logs"
RUN_DIR="$PROJECT_DIR/data/run"
mkdir -p "$LOG_DIR" "$RUN_DIR"

MAIN_LOG="$LOG_DIR/main.log"
TUNNEL_LOG="$LOG_DIR/tunnel.log"
MAIN_PID_FILE="$RUN_DIR/main.pid"
TUNNEL_PID_FILE="$RUN_DIR/tunnel.pid"

# Colors
GREEN="\033[1;32m"
YELLOW="\033[1;33m"
RED="\033[1;31m"
CYAN="\033[1;36m"
BOLD="\033[1m"
RESET="\033[0m"

get_main_pid() {
    pgrep -f "python3.*main.py" || true
}

get_tunnel_pid() {
    pgrep -f "cloudflared.*tunnel" || true
}

get_mt5_pid() {
    pgrep -f "terminal64\.exe" || true
}

start_all() {
    echo -e "${CYAN}======================================================${RESET}"
    echo -e "${BOLD}🤖 ROBO TRADER BOY v2.0 - TIZIMNI ISHGA TUSHIRISH...${RESET}"
    echo -e "${CYAN}======================================================${RESET}"

    # 1. Start Exness MT5 Terminal if not already running
    MT5_PID=$(get_mt5_pid)
    if [ -z "$MT5_PID" ]; then
        echo -e "${YELLOW}⏳ Exness MT5 terminali ishga tushirilmoqda...${RESET}"
        DISPLAY=:0 wine "C:\\Program Files\\MetaTrader 5 EXNESS\\terminal64.exe" > /dev/null 2>&1 &
        sleep 3
        MT5_PID=$(get_mt5_pid)
        if [ -n "$MT5_PID" ]; then
            echo -e "${GREEN}✅ Exness MT5 terminali ishga tushdi (PID: $MT5_PID)${RESET}"
        else
            echo -e "${YELLOW}⚠️ Exness MT5 fon rejimida ochildi.${RESET}"
        fi
    else
        echo -e "${GREEN}✅ Exness MT5 terminali allaqachon ishlab turibdi (PID: $MT5_PID)${RESET}"
    fi

    # 2. Start Cloudflare Tunnel if not running
    TUNNEL_PID=$(get_tunnel_pid)
    if [ -z "$TUNNEL_PID" ]; then
        echo -e "${YELLOW}⏳ Cloudflare xavfsiz tunneli ishga tushirilmoqda...${RESET}"
        nohup ./cloudflared tunnel --protocol http2 --url http://localhost:8000 > "$TUNNEL_LOG" 2>&1 &
        TUNNEL_PID=$!
        echo "$TUNNEL_PID" > "$TUNNEL_PID_FILE"
        
        # Wait for tunnel URL
        TUNNEL_URL=""
        for i in {1..12}; do
            sleep 1
            TUNNEL_URL=$(grep -o "https://[a-zA-Z0-9.-]*\.trycloudflare\.com" "$TUNNEL_LOG" | tail -n 1 || true)
            if [ -n "$TUNNEL_URL" ]; then
                break
            fi
        done
        
        if [ -n "$TUNNEL_URL" ]; then
            echo -e "${GREEN}✅ Cloudflare tunneli tayyor:${RESET} ${CYAN}$TUNNEL_URL${RESET}"
            # Update .env
            if grep -q "WEBAPP_URL=" .env; then
                sed -i "s|WEBAPP_URL=.*|WEBAPP_URL=$TUNNEL_URL|" .env
            else
                echo "WEBAPP_URL=$TUNNEL_URL" >> .env
            fi
        else
            echo -e "${YELLOW}⚠️ Tunnel ishga tushdi, havola birozdan so'ng chiqadi.${RESET}"
        fi
    else
        echo -e "${GREEN}✅ Cloudflare tunneli ishlab turibdi (PID: $TUNNEL_PID)${RESET}"
    fi

    # 3. Start Python Backend & Telegram Bot (main.py)
    MAIN_PID=$(get_main_pid)
    if [ -z "$MAIN_PID" ]; then
        echo -e "${YELLOW}⏳ Python AI Tizimi va Telegram Bot ishga tushirilmoqda...${RESET}"
        nohup ./venv/bin/python3 main.py > "$MAIN_LOG" 2>&1 &
        MAIN_PID=$!
        echo "$MAIN_PID" > "$MAIN_PID_FILE"
        sleep 3
        echo -e "${GREEN}✅ AI Tizimi va Telegram Bot faol! (PID: $MAIN_PID)${RESET}"
    else
        echo -e "${GREEN}✅ AI Tizimi allaqachon ishlab turibdi (PID: $MAIN_PID)${RESET}"
    fi

    echo ""
    status_all
}

stop_all() {
    echo -e "${RED}======================================================${RESET}"
    echo -e "${BOLD}🛑 ROBO TRADER BOY - TIZIMNI TO'XTATISH...${RESET}"
    echo -e "${RED}======================================================${RESET}"

    MAIN_PID=$(get_main_pid)
    if [ -n "$MAIN_PID" ]; then
        echo -e "${YELLOW}Python backend to'xtatilmoqda (PID: $MAIN_PID)...${RESET}"
        kill -15 $MAIN_PID 2>/dev/null || kill -9 $MAIN_PID 2>/dev/null || true
        rm -f "$MAIN_PID_FILE"
        echo -e "${GREEN}✅ Python backend to'xtatildi.${RESET}"
    else
        echo "Python backend ishlamayapti."
    fi

    TUNNEL_PID=$(get_tunnel_pid)
    if [ -n "$TUNNEL_PID" ]; then
        echo -e "${YELLOW}Cloudflare tunnel to'xtatilmoqda (PID: $TUNNEL_PID)...${RESET}"
        kill -15 $TUNNEL_PID 2>/dev/null || kill -9 $TUNNEL_PID 2>/dev/null || true
        rm -f "$TUNNEL_PID_FILE"
        echo -e "${GREEN}✅ Cloudflare tunnel to'xtatildi.${RESET}"
    else
        echo "Cloudflare tunnel ishlamayapti."
    fi

    echo -e "${CYAN}💡 Eslatma: Exness MT5 ochiq qoldirildi. Agar MT5 ni ham yopmoqchi bo'lsangiz: 'killall terminal64.exe' buyrug'ini bering.${RESET}"
}

status_all() {
    echo -e "${CYAN}======================================================${RESET}"
    echo -e "${BOLD}📊 ROBO TRADER BOY v2.0 - MONITORING HOLATI${RESET}"
    echo -e "${CYAN}======================================================${RESET}"

    MT5_PID=$(get_mt5_pid)
    if [ -n "$MT5_PID" ]; then
        echo -e "📈 Exness MT5:       ${GREEN}ISHLAMOQDA 🟢${RESET} (PID: $MT5_PID)"
    else
        echo -e "📈 Exness MT5:       ${RED}TO'XTATILGAN 🔴${RESET}"
    fi

    MAIN_PID=$(get_main_pid)
    if [ -n "$MAIN_PID" ]; then
        echo -e "🤖 AI Bot & Backend: ${GREEN}ISHLAMOQDA 🟢${RESET} (PID: $MAIN_PID, Port: 8000)"
    else
        echo -e "🤖 AI Bot & Backend: ${RED}TO'XTATILGAN 🔴${RESET}"
    fi

    TUNNEL_PID=$(get_tunnel_pid)
    TUNNEL_URL=$(grep -o "https://[a-zA-Z0-9.-]*\.trycloudflare\.com" "$TUNNEL_LOG" 2>/dev/null | tail -n 1 || true)
    if [ -n "$TUNNEL_PID" ]; then
        echo -e "🌐 Cloudflare Tunnel:${GREEN}ISHLAMOQDA 🟢${RESET} (PID: $TUNNEL_PID)"
        echo -e "🔗 Jonli Mini App:   ${CYAN}${TUNNEL_URL:-https://luxury-for-exceptional-toolkit.trycloudflare.com}${RESET}"
    else
        echo -e "🌐 Cloudflare Tunnel:${RED}TO'XTATILGAN 🔴${RESET}"
    fi

    echo -e "${CYAN}------------------------------------------------------${RESET}"
    # Live API status check
    if curl -s --max-time 5 "http://127.0.0.1:8000/api/status?admin_key=7266764356" > /tmp/rtb_status.json 2>/dev/null; then
        BAL=$(python3 -c "import json; d=json.load(open('/tmp/rtb_status.json')); print(d.get('account',{}).get('balance','0'))" 2>/dev/null || echo "N/A")

        EQ=$(python3 -c "import json; d=json.load(open('/tmp/rtb_status.json')); print(d.get('account',{}).get('equity','0'))" 2>/dev/null || echo "N/A")
        ACC=$(python3 -c "import json; d=json.load(open('/tmp/rtb_status.json')); print(d.get('account',{}).get('account','N/A'))" 2>/dev/null || echo "N/A")
        EA_CONN=$(python3 -c "import json; d=json.load(open('/tmp/rtb_status.json')); print('ULANGAN 🟢' if d.get('ea_bridge',{}).get('connected') else 'OFLAYN 🟡')" 2>/dev/null || echo "N/A")
        echo -e "💼 Exness Hisob:     ${BOLD}#$ACC${RESET} (Balans: ${GREEN}\$$BAL${RESET} | Equity: ${GREEN}\$$EQ${RESET})"
        echo -e "🤖 MT5 EA Robotcha:  ${BOLD}$EA_CONN${RESET}"
    else
        echo -e "⚠️ Server bilan lokal aloqa mavjud emas (Server yuklanmoqda yoki to'xtatilgan)."
    fi
    echo -e "${CYAN}======================================================${RESET}"
}

show_logs() {
    echo -e "${CYAN}Jonli loglar ochilmoqda (chiqish uchun Ctrl+C bosing)...${RESET}"
    tail -n 50 -f "$MAIN_LOG"
}

case "$1" in
    start)
        start_all
        ;;
    stop)
        stop_all
        ;;
    restart)
        stop_all
        sleep 2
        start_all
        ;;
    status)
        status_all
        ;;
    logs)
        show_logs
        ;;
    *)
        echo "Foydalanish: $0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
