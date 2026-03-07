"""
app.py — Interface Streamlit (affichage uniquement)
NE gère PAS le monitoring — lit seulement .trading_state.json
Le refresh navigateur n'affecte RIEN.

Lancez avec : streamlit run app.py
"""

import streamlit as st
import os, json, fcntl, time, subprocess
from datetime import datetime

# ═══════════════════════════════════════════════════════════════
#  FICHIER D'ÉTAT (écrit par monitor.py, lu ici)
# ═══════════════════════════════════════════════════════════════
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, ".trading_state.json")
PID_FILE   = os.path.join(BASE_DIR, ".monitor.pid")

ETAT_DEFAUT = {
    "tendances": {
        "XAUUSD": {"dir": None, "start_ts": None, "bull": 50, "peak_bull": 50, "weaken": 0},
        "BTCUSD": {"dir": None, "start_ts": None, "bull": 50, "peak_bull": 50, "weaken": 0},
        "USOIL" : {"dir": None, "start_ts": None, "bull": 50, "peak_bull": 50, "weaken": 0},
    },
    "dernier_envoi" : {},
    "historique_sms": [],
    "nb_sms"        : 0,
    "derniere_maj"  : None,
    "monitor_actif" : False,
}

def lire_etat():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                data = json.load(f)
                fcntl.flock(f, fcntl.LOCK_UN)
            for k, v in ETAT_DEFAUT.items():
                if k not in data:
                    data[k] = v
            return data
    except Exception:
        pass
    return json.loads(json.dumps(ETAT_DEFAUT))

def monitor_actif():
    """Vérifie si monitor.py tourne vraiment."""
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)   # signal 0 = juste vérifier l'existence
            return True, pid
    except Exception:
        pass
    return False, None

# ═══════════════════════════════════════════════════════════════
#  PANNEAU CHRONO TENDANCE
# ═══════════════════════════════════════════════════════════════
def chrono_html(asset, etat):
    td     = etat["tendances"].get(asset, ETAT_DEFAUT["tendances"]["XAUUSD"])
    start  = td.get("start_ts")
    tdir   = td.get("dir")
    weaken = td.get("weaken", 0)
    bull   = td.get("bull", 50)

    col_dir = {"HAUSSIER": "#00ff88", "BAISSIER": "#ff3b5c"}.get(tdir, "#ffd700")
    arrow   = {"HAUSSIER": "▲", "BAISSIER": "▼"}.get(tdir, "◆")

    if not start or tdir not in ("HAUSSIER", "BAISSIER"):
        return (
            '<div style="font-family:monospace;padding:14px;background:#111927;'
            'border:2px solid #1e2d42;border-radius:8px;color:#6a8aaa;text-align:center">'
            '⏳ En attente de signal confirmé…</div>'
        )

    elapsed_s = time.time() - start
    el_min    = elapsed_s / 60
    mm        = int(elapsed_s // 60)
    ss        = int(elapsed_s % 60)
    progress  = min(100, (el_min / 60) * 100)

    if el_min < 1:
        phase, p_col = "🚀 DÉMARRAGE",        "#ffd700"
        signal_txt   = f"{arrow} SIGNAL ENTRÉE"
    elif el_min < 15:
        phase, p_col = f"⏳ DÉVELOPPEMENT",   "#ffd700"
        signal_txt   = f"{arrow} SURVEILLER ({mm}min/{15}min)"
    elif el_min < 60 and weaken < 3:
        phase, p_col = "✅ TENDANCE CONFIRMÉE", "#00ff88"
        signal_txt   = f"{arrow} TRADER DANS LE SENS"
    elif weaken >= 3:
        phase, p_col = "⚠️ FIN PROBABLE",     "#ff3b5c"
        signal_txt   = "◼ SORTIR / ALLÉGER"
    else:
        phase, p_col = "⌛ EXPIRÉE (>1H)",    "#ff3b5c"
        signal_txt   = "◼ CLÔTURER POSITION"

    h_start = datetime.fromtimestamp(start).strftime("%H:%M:%S")
    h_end   = datetime.fromtimestamp(start + 3600).strftime("%H:%M")

    return f"""
<div style="font-family:monospace;padding:14px;background:#111927;
  border:2px solid {p_col};border-radius:8px;
  box-shadow:0 0 14px {p_col}33;margin-bottom:4px">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
    <div>
      <div style="font-size:0.62rem;color:#6a8aaa;letter-spacing:2px;text-transform:uppercase">Phase</div>
      <div style="font-size:0.95rem;font-weight:700;color:{p_col};margin-top:3px">{phase}</div>
      <div style="font-size:0.78rem;color:{col_dir};margin-top:5px;font-weight:600">{signal_txt}</div>
      <div style="font-size:0.65rem;color:#6a8aaa;margin-top:8px">
        ▶ <b style="color:#00d4ff">{h_start}</b> &nbsp; ⏹ est. <b style="color:#ffd700">{h_end}</b>
      </div>
    </div>
    <div style="text-align:right">
      <div style="font-size:0.62rem;color:#6a8aaa;letter-spacing:2px">DURÉE</div>
      <div style="font-size:2.2rem;font-weight:900;color:{col_dir};letter-spacing:3px;line-height:1">
        {mm:02d}:{ss:02d}
      </div>
      <div style="font-size:0.68rem;color:#6a8aaa;margin-top:4px">
        Bull <b style="color:#00d4ff">{bull}/100</b>
      </div>
    </div>
  </div>
  <div style="margin-top:10px">
    <div style="display:flex;justify-content:space-between;font-size:0.6rem;color:#6a8aaa;margin-bottom:3px">
      <span>0</span>
      <span style="color:{p_col}">▼15min</span>
      <span>60min</span>
    </div>
    <div style="height:8px;background:#1e2d42;border-radius:4px;overflow:hidden">
      <div style="height:100%;width:{progress:.1f}%;border-radius:4px;
        background:linear-gradient(90deg,#ffd700,{p_col})"></div>
    </div>
  </div>
</div>"""

# ═══════════════════════════════════════════════════════════════
#  PAGE CONFIG
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
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════
#  LECTURE ÉTAT (à chaque refresh — lit le fichier JSON)
# ═══════════════════════════════════════════════════════════════
etat      = lire_etat()
mon_ok, mon_pid = monitor_actif()

# ═══════════════════════════════════════════════════════════════
#  SIDEBAR
# ═══════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 📲 Alertes SMS")
    st.markdown("---")

    # ── Statut Monitor ───────────────────────────────────────
    if mon_ok:
        st.markdown(f"🟢 **Monitor actif** (PID {mon_pid})")
    else:
        st.markdown("🔴 **Monitor inactif**")
        st.warning("Lancez `python monitor.py &` dans Termux !")
        if st.button("▶ Démarrer monitor"):
            try:
                p = subprocess.Popen(
                    ["python", os.path.join(BASE_DIR, "monitor.py")],
                    stdout=open(os.path.join(BASE_DIR, "monitor.log"), "a"),
                    stderr=subprocess.STDOUT,
                    start_new_session=True
                )
                st.success(f"✅ Monitor lancé (PID {p.pid})")
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.error(f"❌ {e}")

    if mon_ok:
        if st.button("⏹ Arrêter monitor"):
            try:
                import signal as sig
                os.kill(mon_pid, sig.SIGTERM)
                st.success("✅ Monitor arrêté")
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.error(f"❌ {e}")

    st.markdown("---")

    # ── Test SMS manuel ──────────────────────────────────────
    numero_test = st.text_input(
        "📞 Numéro test SMS",
        placeholder="+XXXXXXXXXXX",
        help="Format international ex: +33612345678"
    )
    if st.button("📤 Envoyer SMS Test"):
        if not numero_test:
            st.error("⚠️ Entrez un numéro !")
        else:
            msg = (
                f"🔔 TEST ML+ICT STRATEGY\n"
                f"Monitor: {'✅ Actif' if mon_ok else '❌ Inactif'}\n"
                f"XAU · BTC · Crude Oil\n"
                f"[{datetime.now().strftime('%H:%M:%S')}]"
            )
            try:
                subprocess.run(["termux-sms-send", "-n", numero_test, msg],
                               timeout=15, check=True)
                st.success("✅ SMS envoyé !")
            except Exception as e:
                st.error(f"❌ {e}")

    st.markdown("---")

    # ── Statistiques ─────────────────────────────────────────
    st.markdown("**📊 Statistiques**")
    st.markdown(f"SMS envoyés : **{etat['nb_sms']}**")
    if etat.get("derniere_maj"):
        st.caption(f"Dernière MAJ : {etat['derniere_maj']}")

    st.markdown("---")

    # ── Historique SMS ────────────────────────────────────────
    st.markdown("**📜 Historique SMS**")
    histo = etat.get("historique_sms", [])
    if not histo:
        st.caption("Aucune alerte envoyée")
    else:
        for h in histo[:10]:
            st.markdown(
                f"`{h['heure']}` **{h['asset']}**  \n"
                f"{h['statut']} {h['message']}"
            )

    st.markdown("---")
    if st.button("🔄 Rafraîchir"):
        st.rerun()

# ═══════════════════════════════════════════════════════════════
#  PANNEAUX TENDANCE DURÉE (lus depuis JSON — jamais remis à 0)
# ═══════════════════════════════════════════════════════════════
st.markdown(
    "<div style='font-family:monospace;font-size:0.7rem;color:#00d4ff;"
    "letter-spacing:3px;text-transform:uppercase;margin-bottom:8px;margin-top:4px'>"
    "◈ DÉTECTION TENDANCE — DURÉE 15MIN À 1H</div>",
    unsafe_allow_html=True
)

col1, col2, col3 = st.columns(3)
with col1:
    st.markdown("**🥇 XAUUSD**")
    st.markdown(chrono_html("XAUUSD", etat), unsafe_allow_html=True)
with col2:
    st.markdown("**₿ BTC/USD**")
    st.markdown(chrono_html("BTCUSD", etat), unsafe_allow_html=True)
with col3:
    st.markdown("**🛢️ Crude Oil**")
    st.markdown(chrono_html("USOIL",  etat), unsafe_allow_html=True)

st.markdown("---")

# ═══════════════════════════════════════════════════════════════
#  APP TRADING HTML
# ═══════════════════════════════════════════════════════════════
HTML_FILE = os.path.join(BASE_DIR, "trading_app.html")
try:
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        html_content = f.read()
    st.components.v1.html(html_content, height=1200, scrolling=True)
except FileNotFoundError:
    st.error(f"❌ Fichier introuvable : {HTML_FILE}")
    st.info("Placez **trading_app.html** dans le même dossier que **app.py**")
