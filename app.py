"""
ML+ICT Trading Strategy — Application Streamlit
avec moteur d'alertes SMS (Termux:API)
Lancez avec : streamlit run app.py
"""

import streamlit as st
import os
import subprocess
import threading
import time
import requests
from datetime import datetime

# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION SMS — Modifiez ici votre numéro
# ═══════════════════════════════════════════════════════════════
SMS_CONFIG = {
    "numero"          : "+XXXXXXXXXXXX",   # ← Mettez votre numéro ici
    "actif"           : True,
    "cooldown_minutes": 15,
}

ALERTES_ACTIVES = {
    "signal_entree"    : True,
    "tendance_confirmee": True,
    "signal_sortie"    : True,
    "tp_sl"            : True,
}

# ═══════════════════════════════════════════════════════════════
#  ÉTAT SESSION
# ═══════════════════════════════════════════════════════════════
if "alert_state" not in st.session_state:
    st.session_state.alert_state = {
        "dernier_envoi"   : {},
        "historique"      : [],
        "monitoring_actif": False,
        "nb_sms_envoyes"  : 0,
    }
if "sms_numero" not in st.session_state:
    st.session_state.sms_numero = SMS_CONFIG["numero"]
if "sms_actif" not in st.session_state:
    st.session_state.sms_actif = SMS_CONFIG["actif"]

# ═══════════════════════════════════════════════════════════════
#  ENVOI SMS VIA TERMUX:API
# ═══════════════════════════════════════════════════════════════
def envoyer_sms(numero, message, type_alerte="", asset="", forcer=False):
    if not st.session_state.sms_actif and not forcer:
        return False
    if not numero or numero == "+XXXXXXXXXXXX":
        return False

    if not forcer:
        cle = f"{type_alerte}_{asset}"
        now = time.time()
        dernier = st.session_state.alert_state["dernier_envoi"].get(cle, 0)
        if now - dernier < SMS_CONFIG["cooldown_minutes"] * 60:
            return False

    try:
        result = subprocess.run(
            ["termux-sms-send", "-n", numero, message],
            capture_output=True, text=True, timeout=15
        )
        success = result.returncode == 0
        if success:
            cle = f"{type_alerte}_{asset}"
            st.session_state.alert_state["dernier_envoi"][cle] = time.time()
            st.session_state.alert_state["nb_sms_envoyes"] += 1
            st.session_state.alert_state["historique"].insert(0, {
                "heure"  : datetime.now().strftime("%H:%M:%S"),
                "asset"  : asset,
                "type"   : type_alerte,
                "message": message[:55] + "…" if len(message) > 55 else message,
                "statut" : "✅"
            })
            st.session_state.alert_state["historique"] = \
                st.session_state.alert_state["historique"][:20]
        return success
    except Exception:
        return False

# ═══════════════════════════════════════════════════════════════
#  HELPERS PRIX & SCORE
# ═══════════════════════════════════════════════════════════════
def _get_price(asset):
    try:
        if asset == "BTCUSD":
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT",
                timeout=6)
            d = r.json()
            return float(d["lastPrice"]), float(d["priceChangePercent"])
        elif asset == "XAUUSD":
            r = requests.get("https://api.metals.live/v1/spot/gold", timeout=6)
            d = r.json()
            prix = float(d[0]["price"]) if isinstance(d, list) else float(d.get("gold", d.get("price", 0)))
            return prix, 0.0
        elif asset == "USOIL":
            r = requests.get("https://api.metals.live/v1/spot/oil", timeout=6)
            d = r.json()
            prix = float(d[0]["price"]) if isinstance(d, list) else float(d.get("oil", d.get("price", 0)))
            return prix, 0.0
    except Exception:
        return None, None

def _calc_bull_score(chg_pct):
    score = 50
    score += max(-25, min(25, chg_pct * 5))
    rsi = min(90, max(10, 50 + chg_pct * 4))
    if rsi >= 65: score += 12
    elif rsi >= 55: score += 6
    elif rsi <= 35: score -= 12
    elif rsi <= 45: score -= 6
    if chg_pct >= 0: score += 12
    else: score -= 12
    return int(max(5, min(95, round(score))))

def _get_signal(bull):
    if bull >= 65: return "HAUSSIER"
    if bull <= 35: return "BAISSIER"
    return "INDECIS"

def _fmt(prix, asset):
    if asset == "USOIL": return f"${prix:.2f}/bbl"
    if asset == "BTCUSD": return f"${prix:,.0f}"
    return f"${prix:.2f}"

# ═══════════════════════════════════════════════════════════════
#  THREAD SURVEILLANCE (arrière-plan, toutes les 30 secondes)
# ═══════════════════════════════════════════════════════════════
_prev = {}

def _surveiller():
    ASSETS = {
        "XAUUSD": ("Or/USD",    "🥇"),
        "BTCUSD": ("BTC/USD",   "₿"),
        "USOIL" : ("Crude Oil", "🛢️"),
    }
    while st.session_state.get("sms_actif", False):
        for asset, (nom, emoji) in ASSETS.items():
            try:
                prix, chg = _get_price(asset)
                if prix is None:
                    continue
                bull   = _calc_bull_score(chg)
                signal = _get_signal(bull)
                prev   = _prev.get(asset, {"signal": None, "bull": 50})
                prev_s = prev["signal"]
                prev_b = prev["bull"]
                num    = st.session_state.sms_numero
                heure  = datetime.now().strftime("%H:%M")

                # ── Signal d'entrée ──────────────────────────────
                if ALERTES_ACTIVES["signal_entree"]:
                    if prev_s != signal and signal in ("HAUSSIER", "BAISSIER"):
                        dir_txt = "📈 HAUSSE" if signal == "HAUSSIER" else "📉 BAISSE"
                        envoyer_sms(num,
                            f"🔔 SIGNAL {emoji} {nom}\n"
                            f"{dir_txt} DÉTECTÉ\n"
                            f"Score: {bull}/100 | Prix: {_fmt(prix, asset)}\n"
                            f"Variation: {chg:+.2f}% [{heure}]",
                            "signal_entree", asset)

                # ── Tendance confirmée ───────────────────────────
                if ALERTES_ACTIVES["tendance_confirmee"]:
                    if bull >= 68 and prev_b < 68:
                        envoyer_sms(num,
                            f"✅ TENDANCE CONFIRMÉE {emoji} {nom}\n"
                            f"📈 HAUSSIER FORT — Score {bull}/100\n"
                            f"Prix: {_fmt(prix, asset)}\n"
                            f"Durée estimée: 15min–1h [{heure}]",
                            "tendance_confirmee", asset)
                    elif bull <= 32 and prev_b > 32:
                        envoyer_sms(num,
                            f"✅ TENDANCE CONFIRMÉE {emoji} {nom}\n"
                            f"📉 BAISSIER FORT — Score {bull}/100\n"
                            f"Prix: {_fmt(prix, asset)}\n"
                            f"Durée estimée: 15min–1h [{heure}]",
                            "tendance_confirmee", asset)

                # ── Signal de sortie ─────────────────────────────
                if ALERTES_ACTIVES["signal_sortie"]:
                    if prev_s == "HAUSSIER" and bull < 55 and prev_b >= 65:
                        envoyer_sms(num,
                            f"⚠️ SORTIE {emoji} {nom}\n"
                            f"Tendance HAUSSIÈRE s'affaiblit\n"
                            f"Score: {bull}/100 (était {prev_b})\n"
                            f"→ Alléger / Protéger profits [{heure}]",
                            "signal_sortie", asset)
                    elif prev_s == "BAISSIER" and bull > 45 and prev_b <= 35:
                        envoyer_sms(num,
                            f"⚠️ SORTIE {emoji} {nom}\n"
                            f"Tendance BAISSIÈRE s'affaiblit\n"
                            f"Score: {bull}/100 (était {prev_b})\n"
                            f"→ Racheter / Clôturer [{heure}]",
                            "signal_sortie", asset)

                _prev[asset] = {"signal": signal, "bull": bull}

            except Exception:
                pass
        time.sleep(30)

def demarrer_surveillance():
    if not st.session_state.alert_state["monitoring_actif"]:
        st.session_state.alert_state["monitoring_actif"] = True
        t = threading.Thread(target=_surveiller, daemon=True)
        t.start()

# ═══════════════════════════════════════════════════════════════
#  CONFIG PAGE STREAMLIT
# ═══════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="ML+ICT Strategy — XAU · BTC · Crude Oil",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  #MainMenu, footer { visibility: hidden; }
  .block-container { padding-top: 0.5rem !important; padding-bottom: 0 !important; }
  section[data-testid="stAppViewContainer"] { background: #060a12; }
  iframe { border: none !important; }
  section[data-testid="stSidebar"] {
    background: #0d1421 !important;
    border-right: 1px solid #1e2d42 !important;
  }
  .stButton > button {
    width: 100%; font-weight: 600; border-radius: 6px;
    background: linear-gradient(135deg,#0d1421,#1e2d42);
    border: 1px solid #00d4ff; color: #00d4ff !important;
  }
  .stButton > button:hover { background: rgba(0,212,255,0.15) !important; }
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════
#  SIDEBAR — PANNEAU SMS
# ═══════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 📲 Alertes SMS")
    st.markdown("---")

    numero = st.text_input(
        "📞 Numéro destinataire",
        value=st.session_state.sms_numero,
        placeholder="+33XXXXXXXXX",
        help="Format international avec indicatif pays ex: +33612345678"
    )
    st.session_state.sms_numero = numero

    st.markdown("---")

    sms_on = st.toggle("🔔 Activer les alertes SMS", value=st.session_state.sms_actif)
    if sms_on != st.session_state.sms_actif:
        st.session_state.sms_actif = sms_on
        if sms_on:
            demarrer_surveillance()

    if sms_on:
        st.markdown("🟢 **Surveillance active**")
    else:
        st.markdown("🔴 **Alertes désactivées**")

    st.markdown("---")
    st.markdown("**📋 Types d'alertes**")
    ALERTES_ACTIVES["signal_entree"]      = st.checkbox("🚀 Signal d'entrée",          value=True)
    ALERTES_ACTIVES["tendance_confirmee"] = st.checkbox("✅ Tendance confirmée 15min+", value=True)
    ALERTES_ACTIVES["signal_sortie"]      = st.checkbox("⚠️ Signal de sortie",         value=True)
    ALERTES_ACTIVES["tp_sl"]              = st.checkbox("🎯 TP / SL proche",           value=True)

    st.markdown("---")
    SMS_CONFIG["cooldown_minutes"] = st.slider(
        "⏱️ Délai min entre alertes (min)",
        min_value=5, max_value=60,
        value=SMS_CONFIG["cooldown_minutes"], step=5
    )

    st.markdown("---")

    # ── Bouton TEST ──────────────────────────────────────────
    if st.button("📤 Envoyer SMS Test"):
        if not numero or numero == "+XXXXXXXXXXXX":
            st.error("⚠️ Entrez votre numéro !")
        else:
            msg = (
                f"🔔 TEST ML+ICT STRATEGY\n"
                f"Alertes SMS opérationnelles ✅\n"
                f"XAU · BTC · Crude Oil\n"
                f"[{datetime.now().strftime('%H:%M:%S')}]"
            )
            try:
                subprocess.run(
                    ["termux-sms-send", "-n", numero, msg],
                    timeout=15, check=True
                )
                st.success("✅ SMS test envoyé !")
                st.session_state.alert_state["nb_sms_envoyes"] += 1
                st.session_state.alert_state["historique"].insert(0, {
                    "heure": datetime.now().strftime("%H:%M:%S"),
                    "asset": "TEST", "type": "test",
                    "message": "SMS Test envoyé", "statut": "✅"
                })
            except Exception as e:
                st.error(f"❌ Erreur : {e}")

    st.markdown("---")

    # ── Statistiques ─────────────────────────────────────────
    nb  = st.session_state.alert_state["nb_sms_envoyes"]
    mon = "🟢 Active" if st.session_state.alert_state["monitoring_actif"] else "🔴 Inactive"
    st.markdown(f"**📊 Stats**")
    st.markdown(f"SMS envoyés : **{nb}**")
    st.markdown(f"Surveillance : **{mon}**")

    st.markdown("---")

    # ── Historique ───────────────────────────────────────────
    st.markdown("**📜 Historique**")
    histo = st.session_state.alert_state["historique"]
    if not histo:
        st.caption("Aucune alerte envoyée")
    else:
        for h in histo[:8]:
            st.markdown(
                f"`{h['heure']}` **{h['asset']}** _{h['type']}_  \n"
                f"{h['statut']} {h['message']}",
            )

    # Démarrer automatiquement si activé
    if sms_on and not st.session_state.alert_state["monitoring_actif"]:
        demarrer_surveillance()

# ═══════════════════════════════════════════════════════════════
#  CONTENU PRINCIPAL
# ═══════════════════════════════════════════════════════════════
HTML_FILE = os.path.join(os.path.dirname(__file__), "trading_app.html")

try:
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html_content = f.read()

    st.components.v1.html(html_content, height=1200, scrolling=True)

except FileNotFoundError:
    st.error(f"❌ Fichier introuvable : {HTML_FILE}")
    st.info("Assurez-vous que **trading_app.html** est dans le même dossier que **app.py**")
