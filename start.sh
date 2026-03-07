#!/data/data/com.termux/files/usr/bin/bash
# ═══════════════════════════════════════════════════════════════
#  start.sh — Démarre monitor.py + Streamlit
#  Usage : bash start.sh
# ═══════════════════════════════════════════════════════════════

cd ~/trading

echo "🔒 Wake lock activé..."
termux-wake-lock

# ── Arrêter les anciens processus si existants ───────────────
echo "🛑 Arrêt anciens processus..."
pkill -f "monitor.py"  2>/dev/null
pkill -f "streamlit"   2>/dev/null
sleep 2

# ── Démarrer monitor.py en arrière-plan ─────────────────────
echo "📡 Démarrage monitor.py..."
python monitor.py >> monitor.log 2>&1 &
echo "✅ Monitor PID: $!"
sleep 3

# ── Démarrer Streamlit ───────────────────────────────────────
echo "🌐 Démarrage Streamlit..."
streamlit run app.py \
  --server.port 8501 \
  --server.headless true \
  --server.address 0.0.0.0 \
  --browser.gatherUsageStats false

echo "✅ App disponible sur http://localhost:8501"
