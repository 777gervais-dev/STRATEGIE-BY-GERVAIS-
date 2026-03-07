"""
ML+ICT Trading Strategy — Application Streamlit
avec moteur d'alertes SMS (Termux:API)
+ État persistant sur disque (survit au refresh navigateur)

Lancez avec : streamlit run app.py
"""

import streamlit as st
import os, json, subprocess, threading, time, requests, fcntl
from datetime import datetime

# ═══════════════════════════════════════════════════════════════
#  FICHIER D'ÉTAT PERSISTANT
#  → Écrit sur disque par le thread, relu à chaque refresh
# ═══════════════════════════════════════════════════════════════
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, ".trading_state.json")

ETAT_DEFAUT = {
    # ── Tendances en cours ───────────────────────────────────
    "tendances": {
        "XAUUSD": {"dir": None, "start_ts": None, "bull": 50, "peak_bull": 50, "weaken": 0},
        "BTCUSD": {"dir": None, "start_ts": None, "bull": 50, "peak_bull": 50, "weaken": 0},
        "USOIL" : {"dir": None, "start_ts": None, "bull": 50, "peak_bull": 50, "weaken": 0},
    },
    # ── Signaux précédents (anti-doublons alertes) ───────────
    "prev_signals": {
        "XAUUSD": {"signal": None, "bull": 50},
        "BTCUSD": {"signal": None, "bull": 50},
        "USOIL" : {"signal": None, "bull": 50},
    },
    # ── SMS ──────────────────────────────────────────────────
    "dernier_envoi" : {},      # { "type_asset": timestamp }
    "historique_sms": [],      # 20 derniers SMS envoyés
    "nb_sms"        : 0,
    # ── Monitoring ───────────────────────────────────────────
    "monitoring_pid": None,
    "derniere_maj"  : None,
}


def lire_etat() -> dict:
    """Lit l'état depuis le fichier JSON. Retourne l'état par défaut si absent."""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                data = json.load(f)
                fcntl.flock(f, fcntl.LOCK_UN)
            # Fusion avec le défaut pour les clés manquantes
            for k, v in ETAT_DEFAUT.items():
                if k not in data:
                    data[k] = v
            return data
    except Exception:
        pass
    return json.loads(json.dumps(ETAT_DEFAUT))  # deep copy


def sauver_etat(etat: dict):
    """Sauvegarde l'état dans le fichier JSON (thread-safe)."""
    try:
        etat["derniere_maj"] = datetime.now().strftime("%H:%M:%S")
        with open(STATE_FILE, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            json.dump(etat, f, indent=2)
            fcntl.flock(f, fcntl.LOCK_UN)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION SMS
# ═══════════════════════════════════════════════════════════════
SMS_CONFIG = {
    "numero"          : "+XXXXXXXXXXXX",   # ← Votre numéro ici
    "cooldown_minutes": 15,
}

ALERTES_ACTIVES = {
    "signal_entree"    : True,
    "tendance_confirmee": True,
    "signal_sortie"    : True,
    "tp_sl"            : True,
}

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
            p = float(d[0]["price"]) if isinstance(d, list) else float(d.get("gold", d.get("price", 0)))
            return p, 0.0
        elif asset == "USOIL":
            r = requests.get("https://api.metals.live/v1/spot/oil", timeout=6)
            d = r.json()
            p = float(d[0]["price"]) if isinstance(d, list) else float(d.get("oil", d.get("price", 0)))
            return p, 0.0
    except Exception:
        return None, None

def _calc_bull_score(chg_pct):
    score = 50 + max(-25, min(25, chg_pct * 5))
    rsi = min(90, max(10, 50 + chg_pct * 4))
    if   rsi >= 65: score += 12
    elif rsi >= 55: score += 6
    elif rsi <= 35: score -= 12
    elif rsi <= 45: score -= 6
    score += 12 if chg_pct >= 0 else -12
    return int(max(5, min(95, round(score))))

def _get_signal(bull):
    if bull >= 65: return "HAUSSIER"
    if bull <= 35: return "BAISSIER"
    return "INDECIS"

def _fmt(prix, asset):
    if asset == "USOIL":  return f"${prix:.2f}/bbl"
    if asset == "BTCUSD": return f"${prix:,.0f}"
    return f"${prix:.2f}"


# ═══════════════════════════════════════════════════════════════
#  ENVOI SMS (lit/écrit dans le fichier d'état)
# ═══════════════════════════════════════════════════════════════
def envoyer_sms(etat, numero, message, type_alerte="", asset="", forcer=False):
    """
    Envoie un SMS et met à jour l'état persistant.
    Retourne (success: bool, etat mis à jour)
    """
    if not numero or numero == "+XXXXXXXXXXXX":
        return False, etat
    if not forcer:
        cle     = f"{type_alerte}_{asset}"
        dernier = etat["dernier_envoi"].get(cle, 0)
        if time.time() - dernier < SMS_CONFIG["cooldown_minutes"] * 60:
            return False, etat
    try:
        result = subprocess.run(
            ["termux-sms-send", "-n", numero, message],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            cle = f"{type_alerte}_{asset}"
            etat["dernier_envoi"][cle] = time.time()
            etat["nb_sms"] += 1
            etat["historique_sms"].insert(0, {
                "heure"  : datetime.now().strftime("%H:%M:%S"),
                "asset"  : asset,
                "type"   : type_alerte,
                "message": (message[:55] + "…") if len(message) > 55 else message,
                "statut" : "✅"
            })
            etat["historique_sms"] = etat["historique_sms"][:20]
            return True, etat
    except Exception:
        pass
    return False, etat


# ═══════════════════════════════════════════════════════════════
#  MISE À JOUR TENDANCE (détection durée 15min–1h)
# ═══════════════════════════════════════════════════════════════
def maj_tendance(etat, asset, tdir, bull):
    """
    Met à jour la tendance d'un asset dans l'état persistant.
    Préserve le start_ts si la direction ne change pas.
    """
    td = etat["tendances"][asset]
    now = time.time()

    if td["dir"] != tdir:
        # Nouvelle direction → nouveau départ chrono
        td["dir"]       = tdir
        td["start_ts"]  = now if tdir in ("HAUSSIER", "BAISSIER") else None
        td["peak_bull"] = bull
        td["weaken"]    = 0
    else:
        # Même direction → on continue le chrono existant
        if bull > td["peak_bull"]:
            td["peak_bull"] = bull
        # Détection affaiblissement
        strength      = abs(bull - 50)
        peak_strength = abs(td["peak_bull"] - 50)
        if peak_strength > 10 and strength < peak_strength * 0.6:
            td["weaken"] += 1
        elif strength >= peak_strength * 0.6:
            td["weaken"] = max(0, td["weaken"] - 1)

    td["bull"] = bull
    etat["tendances"][asset] = td
    return etat


# ═══════════════════════════════════════════════════════════════
#  THREAD DE SURVEILLANCE (arrière-plan)
# ═══════════════════════════════════════════════════════════════
_thread_running = False

def _surveiller(numero_ref: list):
    """
    Tourne en arrière-plan.
    Lit/écrit le fichier JSON toutes les 30s.
    numero_ref est une liste à 1 élément pour partager le numéro.
    """
    ASSETS = {
        "XAUUSD": ("Or/USD",    "🥇"),
        "BTCUSD": ("BTC/USD",   "₿"),
        "USOIL" : ("Crude Oil", "🛢️"),
    }
    global _thread_running
    _thread_running = True

    while _thread_running:
        etat   = lire_etat()
        numero = numero_ref[0]
        heure  = datetime.now().strftime("%H:%M")

        for asset, (nom, emoji) in ASSETS.items():
            try:
                prix, chg = _get_price(asset)
                if prix is None:
                    continue

                bull   = _calc_bull_score(chg)
                signal = _get_signal(bull)
                prev   = etat["prev_signals"].get(asset, {"signal": None, "bull": 50})
                prev_s = prev["signal"]
                prev_b = prev["bull"]

                # ── Mise à jour tendance (conserve le chrono) ──
                etat = maj_tendance(etat, asset, signal, bull)

                # ── Alertes SMS ─────────────────────────────────
                if ALERTES_ACTIVES["signal_entree"]:
                    if prev_s != signal and signal in ("HAUSSIER", "BAISSIER"):
                        dir_txt = "📈 HAUSSE" if signal == "HAUSSIER" else "📉 BAISSE"
                        _, etat = envoyer_sms(etat, numero,
                            f"🔔 SIGNAL {emoji} {nom}\n"
                            f"{dir_txt} DÉTECTÉ\n"
                            f"Score: {bull}/100 | Prix: {_fmt(prix, asset)}\n"
                            f"Variation: {chg:+.2f}% [{heure}]",
                            "signal_entree", asset)

                if ALERTES_ACTIVES["tendance_confirmee"]:
                    if bull >= 68 and prev_b < 68:
                        _, etat = envoyer_sms(etat, numero,
                            f"✅ TENDANCE CONFIRMÉE {emoji} {nom}\n"
                            f"📈 HAUSSIER FORT — Score {bull}/100\n"
                            f"Prix: {_fmt(prix, asset)}\n"
                            f"Durée estimée: 15min–1h [{heure}]",
                            "tendance_confirmee", asset)
                    elif bull <= 32 and prev_b > 32:
                        _, etat = envoyer_sms(etat, numero,
                            f"✅ TENDANCE CONFIRMÉE {emoji} {nom}\n"
                            f"📉 BAISSIER FORT — Score {bull}/100\n"
                            f"Prix: {_fmt(prix, asset)}\n"
                            f"Durée estimée: 15min–1h [{heure}]",
                            "tendance_confirmee", asset)

                if ALERTES_ACTIVES["signal_sortie"]:
                    if prev_s == "HAUSSIER" and bull < 55 and prev_b >= 65:
                        _, etat = envoyer_sms(etat, numero,
                            f"⚠️ SORTIE {emoji} {nom}\n"
                            f"Tendance HAUSSIÈRE s'affaiblit\n"
                            f"Score: {bull}/100 (était {prev_b})\n"
                            f"→ Alléger / Protéger profits [{heure}]",
                            "signal_sortie", asset)
                    elif prev_s == "BAISSIER" and bull > 45 and prev_b <= 35:
                        _, etat = envoyer_sms(etat, numero,
                            f"⚠️ SORTIE {emoji} {nom}\n"
                            f"Tendance BAISSIÈRE s'affaiblit\n"
                            f"Score: {bull}/100 (était {prev_b})\n"
                            f"→ Racheter / Clôturer [{heure}]",
                            "signal_sortie", asset)

                etat["prev_signals"][asset] = {"signal": signal, "bull": bull}

            except Exception:
                pass

        sauver_etat(etat)
        time.sleep(30)


# ── Shared numero reference (mutable pour le thread) ─────────
_numero_ref = [SMS_CONFIG["numero"]]

def demarrer_surveillance():
    global _thread_running
    if not _thread_running:
        t = threading.Thread(target=_surveiller, args=(_numero_ref,), daemon=True)
        t.start()


# ═══════════════════════════════════════════════════════════════
#  HELPER AFFICHAGE CHRONO
# ═══════════════════════════════════════════════════════════════
def _chrono_html(asset: str, etat: dict) -> str:
    """Retourne le HTML du panneau tendance durée pour un asset."""
    td      = etat["tendances"].get(asset, ETAT_DEFAUT["tendances"]["XAUUSD"])
    start   = td.get("start_ts")
    tdir    = td.get("dir")
    weaken  = td.get("weaken", 0)

    dir_color = {"HAUSSIER": "#00ff88", "BAISSIER": "#ff3b5c"}.get(tdir, "#ffd700")
    dir_arrow = {"HAUSSIER": "▲", "BAISSIER": "▼"}.get(tdir, "◆")

    if not start or tdir not in ("HAUSSIER", "BAISSIER"):
        return (
            '<div style="font-family:monospace;padding:12px;background:#111927;'
            'border:1px solid #1e2d42;border-radius:8px;color:#6a8aaa">'
            '⏳ En attente de signal…</div>'
        )

    elapsed_s = time.time() - start
    el_min    = elapsed_s / 60
    mm        = int(elapsed_s // 60)
    ss        = int(elapsed_s % 60)
    progress  = min(100, (el_min / 60) * 100)

    # Déterminer la phase
    if el_min < 1:
        phase_txt = "🚀 DÉMARRAGE"
        phase_col = "#ffd700"
        sig_txt   = f"{dir_arrow} SIGNAL ENTRÉE"
    elif el_min < 15:
        phase_txt = f"⏳ DÉVELOPPEMENT ({mm}min)"
        phase_col = "#ffd700"
        sig_txt   = f"{dir_arrow} SURVEILLER"
    elif el_min < 60 and weaken < 3:
        phase_txt = "✅ TENDANCE CONFIRMÉE"
        phase_col = "#00ff88"
        sig_txt   = f"{dir_arrow} TRADER DANS LE SENS"
    elif weaken >= 3:
        phase_txt = "⚠️ FIN PROBABLE"
        phase_col = "#ff3b5c"
        sig_txt   = "◼ SORTIR / ALLÉGER"
    else:
        phase_txt = "⌛ EXPIRÉE (>1H)"
        phase_col = "#ff3b5c"
        sig_txt   = "◼ CLÔTURER"

    # Heure début + fin estimée
    h_start = datetime.fromtimestamp(start).strftime("%H:%M:%S")
    h_end   = datetime.fromtimestamp(start + 3600).strftime("%H:%M")

    return f"""
<div style="font-family:monospace;padding:14px;background:#111927;
  border:2px solid {phase_col};border-radius:8px;
  box-shadow:0 0 16px {phase_col}33;">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
    <div>
      <div style="font-size:0.65rem;color:#6a8aaa;letter-spacing:2px">PHASE DE TENDANCE</div>
      <div style="font-size:1rem;font-weight:700;color:{phase_col};margin-top:4px">{phase_txt}</div>
      <div style="font-size:0.8rem;color:{dir_color};margin-top:6px">{sig_txt}</div>
      <div style="font-size:0.68rem;color:#6a8aaa;margin-top:8px">
        Début: <b style="color:#00d4ff">{h_start}</b> &nbsp;|&nbsp;
        Fin estimée: <b style="color:#ffd700">{h_end}</b>
      </div>
    </div>
    <div style="text-align:right">
      <div style="font-size:0.65rem;color:#6a8aaa;letter-spacing:2px">DURÉE ÉCOULÉE</div>
      <div style="font-size:2rem;font-weight:900;color:{dir_color};letter-spacing:4px">
        {mm:02d}:{ss:02d}
      </div>
      <div style="font-size:0.68rem;color:#6a8aaa;margin-top:4px">
        Score Bull: <b style="color:#00d4ff">{td.get('bull',50)}/100</b>
      </div>
    </div>
  </div>
  <div style="margin-top:10px">
    <div style="display:flex;justify-content:space-between;font-size:0.65rem;color:#6a8aaa;margin-bottom:3px">
      <span>0 min</span><span style="color:{phase_col}">▼ 15min</span><span>60 min</span>
    </div>
    <div style="height:8px;background:#1e2d42;border-radius:4px;overflow:hidden">
      <div style="height:100%;width:{progress:.1f}%;border-radius:4px;
        background:linear-gradient(90deg,#ffd700,{phase_col});transition:width 1s ease"></div>
    </div>
  </div>
</div>
"""


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
  .block-container { padding-top:0.5rem !important; padding-bottom:0 !important; }
  section[data-testid="stAppViewContainer"] { background:#060a12; }
  iframe { border:none !important; }
  section[data-testid="stSidebar"] {
    background:#0d1421 !important;
    border-right:1px solid #1e2d42 !important;
  }
  .stButton > button {
    width:100%; font-weight:600; border-radius:6px;
    background:linear-gradient(135deg,#0d1421,#1e2d42);
    border:1px solid #00d4ff; color:#00d4ff !important;
  }
  .stButton > button:hover { background:rgba(0,212,255,0.15) !important; }
</style>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════
#  LECTURE ÉTAT PERSISTANT AU CHARGEMENT
# ═══════════════════════════════════════════════════════════════
etat = lire_etat()

# Démarrer le thread si pas encore actif
if not _thread_running:
    demarrer_surveillance()


# ═══════════════════════════════════════════════════════════════
#  SESSION STATE (paramètres UI seulement)
# ═══════════════════════════════════════════════════════════════
if "sms_numero" not in st.session_state:
    st.session_state.sms_numero = SMS_CONFIG["numero"]
if "sms_actif" not in st.session_state:
    st.session_state.sms_actif = True


# ═══════════════════════════════════════════════════════════════
#  SIDEBAR — PANNEAU SMS
# ═══════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 📲 Alertes SMS")
    st.markdown("---")

    numero = st.text_input(
        "📞 Numéro destinataire",
        value=st.session_state.sms_numero,
        placeholder="+XXXXXXXXXXX",
        help="Format international ex: +33612345678"
    )
    st.session_state.sms_numero = numero
    _numero_ref[0] = numero   # partage avec le thread

    st.markdown("---")

    sms_on = st.toggle("🔔 Activer les alertes SMS", value=st.session_state.sms_actif)
    st.session_state.sms_actif = sms_on
    if sms_on:
        st.markdown("🟢 **Surveillance active**")
        if not _thread_running:
            demarrer_surveillance()
    else:
        st.markdown("🔴 **Alertes désactivées**")

    st.markdown("---")
    st.markdown("**📋 Types d'alertes**")
    ALERTES_ACTIVES["signal_entree"]      = st.checkbox("🚀 Signal d'entrée",           value=True)
    ALERTES_ACTIVES["tendance_confirmee"] = st.checkbox("✅ Tendance confirmée 15min+",  value=True)
    ALERTES_ACTIVES["signal_sortie"]      = st.checkbox("⚠️ Signal de sortie",          value=True)
    ALERTES_ACTIVES["tp_sl"]              = st.checkbox("🎯 TP / SL proche",            value=True)

    st.markdown("---")
    SMS_CONFIG["cooldown_minutes"] = st.slider(
        "⏱️ Délai entre alertes (min)",
        min_value=5, max_value=60,
        value=SMS_CONFIG["cooldown_minutes"], step=5
    )

    st.markdown("---")

    # ── SMS Test ────────────────────────────────────────────
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
                subprocess.run(["termux-sms-send", "-n", numero, msg],
                               timeout=15, check=True)
                etat["nb_sms"] += 1
                etat["historique_sms"].insert(0, {
                    "heure": datetime.now().strftime("%H:%M:%S"),
                    "asset": "TEST", "type": "test",
                    "message": "SMS Test envoyé", "statut": "✅"
                })
                sauver_etat(etat)
                st.success("✅ SMS test envoyé !")
            except Exception as e:
                st.error(f"❌ Erreur : {e}")

    st.markdown("---")

    # ── Statistiques ─────────────────────────────────────────
    etat_frais = lire_etat()
    st.markdown("**📊 Stats**")
    st.markdown(f"SMS envoyés : **{etat_frais['nb_sms']}**")
    st.markdown(f"Surveillance : **{'🟢 Active' if _thread_running else '🔴 Inactive'}**")
    if etat_frais.get("derniere_maj"):
        st.caption(f"Dernière MAJ : {etat_frais['derniere_maj']}")

    st.markdown("---")

    # ── Historique SMS ────────────────────────────────────────
    st.markdown("**📜 Historique SMS**")
    histo = etat_frais.get("historique_sms", [])
    if not histo:
        st.caption("Aucune alerte envoyée")
    else:
        for h in histo[:8]:
            st.markdown(
                f"`{h['heure']}` **{h['asset']}** _{h['type']}_  \n"
                f"{h['statut']} {h['message']}"
            )


# ═══════════════════════════════════════════════════════════════
#  PANNEAUX TENDANCE DURÉE — lus depuis l'état persistant
# ═══════════════════════════════════════════════════════════════
etat_frais = lire_etat()

st.markdown(
    "<div style='font-family:monospace;font-size:0.7rem;color:#00d4ff;"
    "letter-spacing:3px;text-transform:uppercase;margin-bottom:8px;margin-top:4px'>"
    "◈ DÉTECTION TENDANCE — DURÉE 15MIN À 1H</div>",
    unsafe_allow_html=True
)

col1, col2, col3 = st.columns(3)
with col1:
    st.markdown("**🥇 XAUUSD**")
    st.markdown(_chrono_html("XAUUSD", etat_frais), unsafe_allow_html=True)
with col2:
    st.markdown("**₿ BTC/USD**")
    st.markdown(_chrono_html("BTCUSD", etat_frais), unsafe_allow_html=True)
with col3:
    st.markdown("**🛢️ Crude Oil**")
    st.markdown(_chrono_html("USOIL",  etat_frais), unsafe_allow_html=True)

st.markdown("---")

# ── Bouton refresh manuel des panneaux ───────────────────────
if st.button("🔄 Actualiser les panneaux tendance"):
    st.rerun()


# ═══════════════════════════════════════════════════════════════
#  APP TRADING HTML PRINCIPALE
# ═══════════════════════════════════════════════════════════════
HTML_FILE = os.path.join(BASE_DIR, "trading_app.html")

try:
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html_content = f.read()
    st.components.v1.html(html_content, height=1200, scrolling=True)
except FileNotFoundError:
    st.error(f"❌ Fichier introuvable : {HTML_FILE}")
    st.info("Placez **trading_app.html** dans le même dossier que **app.py**")
