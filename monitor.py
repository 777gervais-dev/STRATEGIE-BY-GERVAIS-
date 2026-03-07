"""
monitor.py — Processus de surveillance indépendant
Tourne en fond, INDÉPENDANT de Streamlit et du navigateur.
Lancez avec : python monitor.py &

Écrit l'état dans .trading_state.json toutes les 30 secondes.
Streamlit lit seulement ce fichier — jamais affecté par un refresh.
"""

import os, json, time, subprocess, requests, fcntl, signal, sys
from datetime import datetime

# ═══════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, ".trading_state.json")
PID_FILE   = os.path.join(BASE_DIR, ".monitor.pid")

# ── Chargez votre numéro ici ──────────────────────────────────
NUMERO          = "+XXXXXXXXXXXX"   # ← Votre numéro
COOLDOWN_MIN    = 15                # Délai entre 2 alertes du même type (minutes)
INTERVALLE_SEC  = 30                # Fréquence de vérification (secondes)

ALERTES = {
    "signal_entree"    : True,
    "tendance_confirmee": True,
    "signal_sortie"    : True,
}

# ═══════════════════════════════════════════════════════════════
#  ÉTAT PAR DÉFAUT
# ═══════════════════════════════════════════════════════════════
ETAT_DEFAUT = {
    "tendances": {
        "XAUUSD": {"dir": None, "start_ts": None, "bull": 50, "peak_bull": 50, "weaken": 0},
        "BTCUSD": {"dir": None, "start_ts": None, "bull": 50, "peak_bull": 50, "weaken": 0},
        "USOIL" : {"dir": None, "start_ts": None, "bull": 50, "peak_bull": 50, "weaken": 0},
    },
    "prev_signals": {
        "XAUUSD": {"signal": None, "bull": 50},
        "BTCUSD": {"signal": None, "bull": 50},
        "USOIL" : {"signal": None, "bull": 50},
    },
    "dernier_envoi" : {},
    "historique_sms": [],
    "nb_sms"        : 0,
    "derniere_maj"  : None,
    "monitor_actif" : False,
}

# ═══════════════════════════════════════════════════════════════
#  LECTURE / ÉCRITURE JSON (thread-safe)
# ═══════════════════════════════════════════════════════════════
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

def sauver_etat(etat):
    try:
        etat["derniere_maj"]  = datetime.now().strftime("%H:%M:%S")
        etat["monitor_actif"] = True
        with open(STATE_FILE, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            json.dump(etat, f, indent=2)
            fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        print(f"[ERREUR] Sauvegarde état : {e}")

# ═══════════════════════════════════════════════════════════════
#  PRIX & SCORE
# ═══════════════════════════════════════════════════════════════
def get_price(asset):
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

def calc_bull(chg):
    score = 50 + max(-25, min(25, chg * 5))
    rsi   = min(90, max(10, 50 + chg * 4))
    if   rsi >= 65: score += 12
    elif rsi >= 55: score += 6
    elif rsi <= 35: score -= 12
    elif rsi <= 45: score -= 6
    score += 12 if chg >= 0 else -12
    return int(max(5, min(95, round(score))))

def get_signal(bull):
    if bull >= 65: return "HAUSSIER"
    if bull <= 35: return "BAISSIER"
    return "INDECIS"

def fmt_prix(prix, asset):
    if asset == "USOIL":  return f"${prix:.2f}/bbl"
    if asset == "BTCUSD": return f"${prix:,.0f}"
    return f"${prix:.2f}"

# ═══════════════════════════════════════════════════════════════
#  MISE À JOUR TENDANCE (préserve le chrono)
# ═══════════════════════════════════════════════════════════════
def maj_tendance(etat, asset, tdir, bull):
    td  = etat["tendances"][asset]
    now = time.time()

    if td["dir"] != tdir:
        # Changement de direction → nouveau chrono
        td["dir"]       = tdir
        td["start_ts"]  = now if tdir in ("HAUSSIER", "BAISSIER") else None
        td["peak_bull"] = bull
        td["weaken"]    = 0
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {asset} → {tdir} | Bull: {bull}")
    else:
        # Même direction → chrono continue, on met à jour le score
        if bull > td["peak_bull"]:
            td["peak_bull"] = bull
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
#  ENVOI SMS
# ═══════════════════════════════════════════════════════════════
def envoyer_sms(etat, message, type_alerte, asset):
    if NUMERO == "+XXXXXXXXXXXX":
        return etat
    cle     = f"{type_alerte}_{asset}"
    dernier = etat["dernier_envoi"].get(cle, 0)
    if time.time() - dernier < COOLDOWN_MIN * 60:
        return etat   # cooldown actif
    try:
        r = subprocess.run(
            ["termux-sms-send", "-n", NUMERO, message],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
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
            print(f"[SMS ✅] {asset} | {type_alerte} | {NUMERO}")
        else:
            print(f"[SMS ❌] Erreur returncode={r.returncode}")
    except Exception as e:
        print(f"[SMS ❌] {e}")
    return etat

# ═══════════════════════════════════════════════════════════════
#  BOUCLE PRINCIPALE
# ═══════════════════════════════════════════════════════════════
ASSETS = {
    "XAUUSD": ("Or/USD",    "🥇"),
    "BTCUSD": ("BTC/USD",   "₿"),
    "USOIL" : ("Crude Oil", "🛢️"),
}

def boucle():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ✅ Monitor démarré — PID {os.getpid()}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📂 État : {STATE_FILE}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 📲 Numéro : {NUMERO}")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ⏱️  Intervalle : {INTERVALLE_SEC}s")

    while True:
        etat  = lire_etat()
        heure = datetime.now().strftime("%H:%M")

        for asset, (nom, emoji) in ASSETS.items():
            try:
                prix, chg = get_price(asset)
                if prix is None:
                    print(f"[{heure}] ⚠️  {asset} : API indisponible")
                    continue

                bull   = calc_bull(chg)
                signal = get_signal(bull)
                prev   = etat["prev_signals"].get(asset, {"signal": None, "bull": 50})
                prev_s = prev["signal"]
                prev_b = prev["bull"]

                print(f"[{heure}] {emoji} {asset} | {signal} | Bull:{bull} | Prix:{fmt_prix(prix,asset)} | Δ{chg:+.2f}%")

                # ── Mise à jour tendance ──────────────────────
                etat = maj_tendance(etat, asset, signal, bull)

                # ── Alertes SMS ───────────────────────────────
                if ALERTES["signal_entree"]:
                    if prev_s != signal and signal in ("HAUSSIER", "BAISSIER"):
                        dir_txt = "📈 HAUSSE" if signal == "HAUSSIER" else "📉 BAISSE"
                        etat = envoyer_sms(etat,
                            f"🔔 SIGNAL {emoji} {nom}\n"
                            f"{dir_txt} DÉTECTÉ\n"
                            f"Score: {bull}/100 | Prix: {fmt_prix(prix,asset)}\n"
                            f"Var: {chg:+.2f}% [{heure}]",
                            "signal_entree", asset)

                if ALERTES["tendance_confirmee"]:
                    if bull >= 68 and prev_b < 68:
                        etat = envoyer_sms(etat,
                            f"✅ TENDANCE {emoji} {nom}\n"
                            f"📈 HAUSSIER FORT — {bull}/100\n"
                            f"Prix: {fmt_prix(prix,asset)}\n"
                            f"Durée estimée 15min–1h [{heure}]",
                            "tendance_confirmee", asset)
                    elif bull <= 32 and prev_b > 32:
                        etat = envoyer_sms(etat,
                            f"✅ TENDANCE {emoji} {nom}\n"
                            f"📉 BAISSIER FORT — {bull}/100\n"
                            f"Prix: {fmt_prix(prix,asset)}\n"
                            f"Durée estimée 15min–1h [{heure}]",
                            "tendance_confirmee", asset)

                if ALERTES["signal_sortie"]:
                    if prev_s == "HAUSSIER" and bull < 55 and prev_b >= 65:
                        etat = envoyer_sms(etat,
                            f"⚠️ SORTIE {emoji} {nom}\n"
                            f"Tendance HAUSSIÈRE s'affaiblit\n"
                            f"Score: {bull}/100 (était {prev_b})\n"
                            f"→ Alléger / Protéger [{heure}]",
                            "signal_sortie", asset)
                    elif prev_s == "BAISSIER" and bull > 45 and prev_b <= 35:
                        etat = envoyer_sms(etat,
                            f"⚠️ SORTIE {emoji} {nom}\n"
                            f"Tendance BAISSIÈRE s'affaiblit\n"
                            f"Score: {bull}/100 (était {prev_b})\n"
                            f"→ Racheter / Clôturer [{heure}]",
                            "signal_sortie", asset)

                etat["prev_signals"][asset] = {"signal": signal, "bull": bull}

            except Exception as e:
                print(f"[{heure}] ❌ {asset} erreur : {e}")

        sauver_etat(etat)
        print(f"[{heure}] 💾 État sauvegardé — prochain check dans {INTERVALLE_SEC}s\n")
        time.sleep(INTERVALLE_SEC)

# ═══════════════════════════════════════════════════════════════
#  GESTION ARRÊT PROPRE (Ctrl+C / kill)
# ═══════════════════════════════════════════════════════════════
def arreter(sig, frame):
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 🛑 Monitor arrêté proprement")
    # Marquer comme inactif dans le fichier
    try:
        etat = lire_etat()
        etat["monitor_actif"] = False
        sauver_etat(etat)
    except Exception:
        pass
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    sys.exit(0)

signal.signal(signal.SIGTERM, arreter)
signal.signal(signal.SIGINT,  arreter)

# ─── Écrire le PID ───────────────────────────────────────────
with open(PID_FILE, "w") as f:
    f.write(str(os.getpid()))

if __name__ == "__main__":
    boucle()
