#!/usr/bin/env bash
# =============================================================================
#  SDN Network Delay Measurement Tool – Demo Runner
#  Automates the full demo sequence for teacher presentation.
#
#  Usage:  sudo bash run_demo.sh [--auto]
#    --auto  skip interactive CLI, run fully automated
# =============================================================================

set -e
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; NC='\033[0m'

AUTO=false
[[ "$1" == "--auto" ]] && AUTO=true

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║   SDN Network Delay Measurement Tool – Demo Script       ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Check dependencies ────────────────────────────────────────────
check_cmd() {
    command -v "$1" &>/dev/null || { echo "✘ '$1' not found. Install it."; exit 1; }
}
check_cmd mn
check_cmd ryu-manager
check_cmd ovs-ofctl
echo -e "${GREEN}✔ Dependencies found${NC}"

# ── Clean any leftover Mininet state ──────────────────────────────
echo -e "\n${YELLOW}[1/5] Cleaning previous Mininet state …${NC}"
sudo mn -c 2>/dev/null || true
sleep 1

# ── Start Ryu controller in background ────────────────────────────
echo -e "\n${YELLOW}[2/5] Starting Ryu controller (background) …${NC}"
pkill -f "ryu-manager controller.py" 2>/dev/null || true
sleep 1
ryu-manager controller.py > ryu.log 2>&1 &
RYU_PID=$!
echo "    Ryu PID: $RYU_PID  (logs → ryu.log)"
sleep 4   # Wait for controller to be ready

if ! ps -p $RYU_PID > /dev/null 2>&1; then
    echo "✘ Ryu failed to start. Check ryu.log"
    exit 1
fi
echo -e "${GREEN}✔ Ryu controller running${NC}"

# ── Run measurement script ─────────────────────────────────────────
echo -e "\n${YELLOW}[3/5] Running automated measurements …${NC}"
sudo python3 measure_delay.py

# ── Analyze & plot ────────────────────────────────────────────────
echo -e "\n${YELLOW}[4/5] Generating analysis graphs …${NC}"
python3 analyze_delay.py

# ── Show REST API demo ─────────────────────────────────────────────
echo -e "\n${YELLOW}[5/5] Controller REST API demo …${NC}"
echo "  GET /stats:"
curl -s http://127.0.0.1:8080/stats | python3 -m json.tool 2>/dev/null \
    | head -40 || echo "  (controller may have stopped after CLI exited)"

# ── Stop controller ────────────────────────────────────────────────
kill $RYU_PID 2>/dev/null || true
echo -e "\n${GREEN}✔ Demo complete!${NC}"
echo ""
echo "  Output files:"
echo "    delay_results.json   – raw RTT data"
echo "    delay_results.csv    – RTT spreadsheet"
echo "    delay_analysis.png   – analysis dashboard"
echo "    controller.log       – controller event log"
echo "    ryu.log              – Ryu startup log"
echo ""
