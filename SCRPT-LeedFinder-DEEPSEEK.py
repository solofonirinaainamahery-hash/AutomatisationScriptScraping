import os
import json
import time
import random
import logging
import imageio_ffmpeg
import urllib.request
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from yt_dlp import YoutubeDL
from pymongo import MongoClient

# --- ffmpeg via imageio ---
os.environ["FFMPEG_BINARY"] = imageio_ffmpeg.get_ffmpeg_exe()
DOSSIER_SCRIPT = os.path.dirname(os.path.abspath(__file__))
# ══════════════════════════════════════════════
# 🔧 FIX PATH DENO
# ══════════════════════════════════════════════
deno_path = os.path.expandvars(r"%USERPROFILE%\.deno\bin")
os.environ["PATH"] = deno_path + os.pathsep + os.environ.get("PATH", "")

# ══════════════════════════════════════════════
# ⏱️  DÉLAI ALÉATOIRE
# ══════════════════════════════════════════════
def wait_randomly(min_delay=8.6, max_delay=30):
    delay = random.uniform(min_delay, max_delay)
    print(f"   ⏳ Attente de {delay:.2f} secondes...")
    time.sleep(delay)

def wait_web(min_delay=1, max_delay=4):
    time.sleep(random.uniform(min_delay, max_delay))

# ══════════════════════════════════════════════════════════════════════
# ⚙️  CONFIGURATION — ÉTAPE 1
# ══════════════════════════════════════════════════════════════════════
URI_MONGODB      = "mongodb://uprospectprodetails:15JYU76fd2Z085auXt@57.129.18.234:27017/prospectdatasetprodetails?authSource=prospectdatasetprodetails"
NOM_BASE_DONNEES = "prospectdatasetprodetails"
NOM_COLLECTION   = "companies"

# Les fichiers se créeront directement dans le dossier du script
FICHIER_SORTIE   = os.path.join(DOSSIER_SCRIPT, "leedfinder.json")
CHEMIN_COOKIES   = os.path.join(DOSSIER_SCRIPT, "cookies.txt")

# ══════════════════════════════════════════════════════════════════════
# ⚙️  CONFIGURATION — ÉTAPE 2 : Scraping & Recherche Web
# ══════════════════════════════════════════════════════════════════════

CHANNELS_FILE   = FICHIER_SORTIE
MAX_COMMENTS    = 50000
LOG_FILE        = os.path.join(DOSSIER_SCRIPT, "scraper.log")
CHECKPOINT_FILE = os.path.join(DOSSIER_SCRIPT, "progress.json")

# Dossiers de sortie pour les résultats
BACKUP_DIR      = os.path.join(DOSSIER_SCRIPT, "data_youtub_structure")
WEB_BACKUP_DIR  = os.path.join(DOSSIER_SCRIPT, "data_web_structure-leefinder")

TABS = [
    ("videos",    "📹 Videos"),
    ("shorts",    "🩳 Shorts"),
    ("streams",   "🔴 En direct"),
    ("releases",  "🎵 Sorties"),
    ("playlists", "📋 Playlists"),
]

# ══════════════════════════════════════════════════════════════════════
# ⏸️  INTERRUPTEUR — Sociétés sans chaîne YouTube ("web_search")
# ══════════════════════════════════════════════════════════════════════
# False (par défaut) = MODE STANDBY :
#   - traiter_societe_sans_youtube() N'EST PAS appelée
#   - donc AUCUNE écriture dans MongoDB (prospectdatasetpro.sortez_youtube,
#     documents avec type_traitement="web_search")
#   - et AUCUN fichier créé/modifié dans WEB_BACKUP_DIR
#   Les sociétés concernées sont juste comptées (compteur_standby) puis ignorées.
#
# True = comportement d'origine : ces sociétés sont traitées normalement
#   (Mongo + fichier local), comme avant.
#
# ➜ Pour réactiver ce traitement, repasser cette valeur à True.
TRAITER_SOCIETES_SANS_YOUTUBE = False

# ══════════════════════════════════════════════
# 📋  LOGGER
# ══════════════════════════════════════════════
def setup_logger() -> logging.Logger:
    logger = logging.getLogger("ytb_scraper")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger

log = setup_logger()

# ══════════════════════════════════════════════
# 🗄️  CONNEXION MONGODB — prospectdatasetpro
# ══════════════════════════════════════════════
MONGO_URI = "mongodb://uprospectpro:15NO90m2Z085auXt@57.129.18.234:27017/prospectdatasetpro?authSource=prospectdatasetpro"
NOM_BASE_YOUTUBE = "uprospectpro"   # base où chercher/écrire la collection "you_tube"
mongo_available = False

try:
    mongo_client = MongoClient(MONGO_URI)
    db = mongo_client[NOM_BASE_YOUTUBE]
    channels_collection = db["you_tube"]
    log.info("🔌 Connexion réussie à la base MongoDB (prospectdatasetpro)")
    mongo_available = True
except Exception as e:
    log.error(f"❌ Impossible de se connecter à MongoDB prospectdatasetpro. Mode local activé. Détails : {e}")


# ══════════════════════════════════════════════════════════════════════
# 💾  SAUVEGARDE & RÉPARATION JSON
# ══════════════════════════════════════════════════════════════════════
def sauvegarder_atomique(chemin: str, donnees: list) -> None:
    chemin_tmp = chemin + ".tmp"
    with open(chemin_tmp, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=4)
    os.replace(chemin_tmp, chemin)

def reparer_json_si_necessaire(chemin: str) -> list:
    if not os.path.exists(chemin):
        return []
    taille_mo = os.path.getsize(chemin) / 1_048_576
    print(f"📂 Fichier existant détecté ({taille_mo:.1f} Mo). Vérification de l'intégrité...")
    with open(chemin, "r", encoding="utf-8") as f:
        contenu = f.read()
    try:
        data = json.loads(contenu)
        print(f"✅ Fichier valide — {len(data)} documents déjà présents. Reprise directe.")
        return data
    except json.JSONDecodeError:
        pass
    print("🔧 Fichier corrompu détecté. Réparation automatique en cours...")
    derniere_accolade = contenu.rfind("}")
    if derniere_accolade == -1:
        return []
    try:
        data = json.loads(contenu[:derniere_accolade + 1] + "\n]")
        print(f"✅ Réparation réussie — {len(data)} documents récupérés !")
        sauvegarder_atomique(chemin, data)
        return data
    except json.JSONDecodeError:
        pass
    pos = derniere_accolade
    for tentative in range(1, 501):
        try:
            data = json.loads(contenu[:pos + 1] + "\n]")
            print(f"✅ Réparation réussie après {tentative} recul(s) — {len(data)} documents récupérés !")
            sauvegarder_atomique(chemin, data)
            return data
        except json.JSONDecodeError:
            pos = contenu.rfind("}", 0, pos)
            if pos == -1:
                break
    print("❌ Réparation impossible. Repart de zéro.")
    return []


# ══════════════════════════════════════════════════════════════════════
# 🎥  MOTEUR YOUTUBE — ÉTAPE 1
# ══════════════════════════════════════════════════════════════════════
def extraire_url_chaine(url_video):
    options = {
        'extract_flat': True, 'skip_download': True,
        'quiet': True, 'no_warnings': True, 'cookiefile': CHEMIN_COOKIES
    }
    try:
        with YoutubeDL(options) as ydl:
            infos = ydl.extract_info(url_video, download=False)
            if infos and 'channel_url' in infos:
                return infos['channel_url']
    except Exception as e:
        msg = str(e).lower()
        if "private" in msg:
            return "Erreur : Vidéo passée en privée"
        elif "unavailable" in msg or "not found" in msg or "404" in msg:
            return "Erreur : Vidéo supprimée ou introuvable"
        elif "sign in" in msg:
            return "Erreur : Limite d'âge (connexion requise)"
        else:
            return "Erreur : Problème technique ou blocage YouTube"
    return "Erreur : La chaîne n'est pas exposée sur cette vidéo"


# ══════════════════════════════════════════════════════════════════════
# 🔗  Extraction d'un lien social depuis le champ "socialAccounts"
# ══════════════════════════════════════════════════════════════════════
def extraire_lien_social(doc: dict, plateforme: str) -> str:
    """
    Le schéma "companies" stocke les réseaux sociaux dans un dict du type :
        "linkedin": [{"link": "...", "name": "..."}],
            ...
        }
    Retourn    socialAccounts = {
            "youtube":  [{"link": "https://youtube.com/...", "name": "..."}],
        e le premier lien trouvé pour la plateforme demandée (ex: "youtube"),
    ou une chaîne vide si absent/mal formé.
    """
    comptes = doc.get("socialAccounts") or {}
    entrees = comptes.get(plateforme) or []
    if isinstance(entrees, list) and entrees:
        premiere = entrees[0]
        if isinstance(premiere, dict):
            return str(premiere.get("link") or "").strip()
    return ""


# ══════════════════════════════════════════════════════════════════════
# 🚀  ÉTAPE 1 : extraction MongoDB + Recherche par paliers DDG
# ══════════════════════════════════════════════════════════════════════
def executer_extraction():
    print("=" * 70)
    print("  🛠️  ÉTAPE 1 — EXTRACTION MONGODB & RECHERCHE DDG")
    print("=" * 70)

    if not os.path.exists(CHEMIN_COOKIES):
        print(f"⚠️  Cookies introuvables : {CHEMIN_COOKIES}")

    resultats_finals = reparer_json_si_necessaire(FICHIER_SORTIE)
    ids_deja_traites = {item["id"] for item in resultats_finals if "id" in item}

    if ids_deja_traites:
        print(f"ℹ️  Reprise active : {len(ids_deja_traites)} documents déjà traités.")

    print("\n🔌 Connexion au serveur MongoDB (sortezcommercants)...")
    try:
        client = MongoClient(URI_MONGODB, serverSelectionTimeoutMS=5000)
        client.server_info()
    except Exception as e:
        print(f"❌ Erreur de connexion :\n{e}")
        return

    collection = client[NOM_BASE_DONNEES][NOM_COLLECTION]

    # ──────────────────────────────────────────────────────────────────
    # 🗺️  MAPPING DE SCHÉMA — collection "companies" (prospectdatasetprodetails)
    # ──────────────────────────────────────────────────────────────────
    # Cette collection N'A PAS le même schéma que l'ancienne base
    # "sortezcommercants". Voici la correspondance vérifiée via
    # diagnostic_mongo.py :
    #   NomSociete       -> companyName (fallback: tradeName)
    #   SiteWeb          -> url (fallback: domains[0])
    #   CodePostal       -> zip
    #   Adresse1         -> street
    #   TelFixe          -> phone
    #   Email_decideur   -> email
    #   Siret            -> registerId (⚠️ c'est en réalité un SIREN, pas un SIRET)
    #   code_ape         -> naceCodes.codes[0]
    #   lien YouTube     -> socialAccounts.youtube[0].link (PAS de champ "Video"
    #                       ni "page_web_marchand" dans ce schéma)
    #   Dirigeant/Nom/Prenom, TelMobile, Civilite, Horaires
    #                    -> N'EXISTENT PAS dans ce schéma. Restent vides.
    #                       Conséquence : le "Palier 3" (recherche avec le nom
    #                       du dirigeant) ne pourra jamais se déclencher ici,
    #                       c'est normal, pas un bug.
    champs_utiles = {
        "_id": 1, "id": 1, "companyName": 1, "tradeName": 1,
        "registerId": 1, "naceCodes": 1, "email": 1, "street": 1,
        "phone": 1, "zip": 1, "url": 1, "domains": 1, "socialAccounts": 1,
    }
    # faux_dirigeants : supprimé — ce schéma (companies) n'a pas de champ
    # dirigeant/contact, donc ce filtre n'a plus d'utilité ici. À réintroduire
    # si un jour cette collection ou une autre source fournit ces données.

    print("🚀 Démarrage du pipeline complet !")
    print("-" * 70)

    compteur_nouveaux = 0

    try:
        filtre = {"socialAccounts.youtube": {"$exists": True, "$ne": []}}
        for index, doc in enumerate(collection.find(filtre, champs_utiles), 1):
            id_texte = str(doc.get("_id"))
            if id_texte in ids_deja_traites:
                continue

            # ── Nom de la société : companyName en priorité, tradeName en repli ──
            nom_societe = str(doc.get("companyName") or doc.get("tradeName") or "").strip()

            # ── Dirigeant : ce schéma n'a pas de champ contact/dirigeant.
            # On garde les variables (utilisées plus bas et dans le JSON de
            # sortie) mais elles resteront toujours vides ici. ──
            nom               = ""
            prenom            = ""
            dirigeant_propre  = ""

            # ── Lien YouTube : vient de socialAccounts.youtube, pas de "Video" ──
            lien_video = extraire_lien_social(doc, "youtube")

            # ── Site web : champ "url", repli sur le premier domaine connu ──
            site_web = str(doc.get("url") or "").strip()
            if not site_web:
                domaines = doc.get("domains") or []
                if domaines:
                    site_web = str(domaines[0]).strip()

            # ── Détection YouTube ──
            a_youtube = lien_video and "youtube" in lien_video.lower()

            if a_youtube:
                est_une_chaine = ("/channel/" in lien_video or "/user/" in lien_video or "/c/" in lien_video or "@" in lien_video)
                if est_une_chaine:
                    print(f"✅ [{index}] YouTube (chaîne directe) : {nom_societe or 'Société anonyme'}")
                    lien_final = lien_video
                else:
                    print(f"🔄 [{index}] YouTube (extraction chaîne) : {nom_societe or 'Société anonyme'}...")
                    lien_final = extraire_url_chaine(lien_video)
                    wait_randomly()
                type_traitement = "youtube"
            else:
                # Pas de lien YouTube : mise en standby (conformément à TRAITER_SOCIETES_SANS_YOUTUBE = False)
                print(f"⏸️  [{index}] Pas de lien YouTube d'origine pour : {nom_societe or 'Société anonyme'} "
                      f"— standby, recherche DDG ignorée.")
                lien_final = "Recherche non effectuee (standby)"
                type_traitement = "web_search"

            compteur_nouveaux += 1

            # ── code_ape : naceCodes.codes[0] si présent ──
            nace = doc.get("naceCodes") or {}
            codes_nace = nace.get("codes") or []
            code_ape = str(codes_nace[0]).strip() if codes_nace else ""

            # ⚠️ Dans ce schéma, beaucoup de champs valent explicitement `None`
            # (et pas juste "absents"). `doc.get(champ, "")` ne suffit pas dans
            # ce cas : il renverrait `None`, et `str(None)` donnerait la chaîne
            # "None" au lieu d'une chaîne vide. D'où le `or ""` ci-dessous sur
            # chaque champ concerné.
            resultats_finals.append({
                "id":               id_texte,
                "id_proleadfeeder":     doc.get("id"),       # identifiant texte du schéma "companies" (pas un int)
                "user_ionauth_id":  None,                # n'existe pas dans ce schéma
                "NomSociete":       nom_societe,
                "Siret":            str(doc.get("registerId") or "").strip(),  # ⚠️ SIREN, pas un vrai SIRET
                "code_ape":         code_ape,
                "Civilite":         None,                # n'existe pas dans ce schéma
                "Dirigeant":        dirigeant_propre,    # toujours vide ici (voir note plus haut)
                "Nom":              nom,
                "Prenom":           prenom,
                "Email_decideur":   str(doc.get("email") or "").strip(),
                "Adresse1":         str(doc.get("street") or "").strip(),
                "TelFixe":          str(doc.get("phone") or "").strip(),
                "TelMobile":        "",                  # n'existe pas dans ce schéma
                "CodePostal":       str(doc.get("zip") or "").strip(),
                "SiteWeb":          site_web,
                "Horaires":         "",                  # n'existe pas dans ce schéma
                "type_traitement":  type_traitement,
                "Donnees_YouTube": {
                    "Lien_Video_Origine":     lien_video,
                    "ChaineYouTube_Extraite": lien_final
                }
            })

            if compteur_nouveaux % 20 == 0:
                sauvegarder_atomique(FICHIER_SORTIE, resultats_finals)
                print(f"   💾 Checkpoint : {len(resultats_finals)} sociétés extraites")

    except Exception as e:
        print(f"\n⚠️  Flux interrompu : {e}")

    if resultats_finals:
        sauvegarder_atomique(FICHIER_SORTIE, resultats_finals)
        total_yt  = sum(1 for x in resultats_finals if x.get("type_traitement") == "youtube")
        total_web = sum(1 for x in resultats_finals if x.get("type_traitement") == "web_search")
        print("-" * 70)
        print(f"🎉 Étape 1 terminée !")
        print(f"   📹 Avec YouTube  : {total_yt}")
        print(f"   🌐 Sans YouTube  : {total_web}")
        print(f"   📊 Total         : {len(resultats_finals)}")

    client.close()


# ══════════════════════════════════════════════
# 💾  CHECKPOINT
# ══════════════════════════════════════════════
def load_checkpoint(channel_id: str) -> set:
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return set(data.get(channel_id, []))
        except Exception:
            pass
    return set()

def save_checkpoint(channel_id: str, done_ids: set):
    data = {}
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data[channel_id] = list(done_ids)
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ══════════════════════════════════════════════
# 🔁  RETRY
# ══════════════════════════════════════════════
def with_retry(fn, *args, max_retries=4, base_delay=5, **kwargs):
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e).lower()
            if any(k in msg for k in ["429", "too many", "rate limit", "blocked"]):
                wait = base_delay * (2 ** attempt) + random.uniform(1, 3)
                log.warning(f"Rate limit détecté, attente {wait:.1f}s...")
                time.sleep(wait)
            else:
                log.error(f"Erreur non-retry ({fn.__name__}) : {e}")
                break
    return None

def format_duration(seconds) -> str:
    try:
        return datetime.utcfromtimestamp(int(seconds or 0)).strftime("%H:%M:%S")
    except Exception:
        return "00:00:00"

def load_channels(file_path):
    print(f"📂 Chargement des sociétés depuis : {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"✅ {len(data)} sociétés chargées.")
    return data


# ══════════════════════════════════════════════════════════════════════
# 📺  RÉCUPÉRATION DONNÉES YOUTUBE
# ══════════════════════════════════════════════════════════════════════
def get_channel_videos(channel_url: str):
    ydl_opts = {
        "quiet": True, "extract_flat": True, "skip_download": True,
        "ignoreerrors": True, "nocheckcertificate": True,
        "cookiefile": CHEMIN_COOKIES,
        "extractor_args": {"youtubetab": {"skip": ["authcheck"]}},
        "remote_components": ["ejs:github"],
    }
    all_entries, channel_info = [], {}
    for tab_key, tab_label in TABS:
        tab_url = channel_url.rstrip("/") + "/" + tab_key
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(tab_url, download=False)
            if not info: continue
            if not channel_info:
                channel_info = {
                    "id":          info.get("id", ""),
                    "title":       info.get("title", info.get("uploader", "")),
                    "description": info.get("description", ""),
                    "channel_url": info.get("channel_url", info.get("url", "")),
                    "nb_videos":   info.get("playlist_count", 0),
                }
            entries = info.get("entries", []) or []
            count = len([e for e in entries if e])
            print(f"     {tab_label} → {count} entrées trouvées")
            for entry in entries:
                if not entry: continue
                if entry.get("_type") == "playlist":
                    for sub in entry.get("entries", []) or []:
                        if sub and sub.get("id") and len(sub.get("id", "")) == 11:
                            sub["tab"] = tab_key
                            all_entries.append(sub)
                else:
                    eid = entry.get("id", "")
                    if eid and len(eid) == 11:
                        entry["tab"] = tab_key
                        all_entries.append(entry)
        except Exception: pass
    seen, videos = set(), []
    for entry in all_entries:
        vid_id = entry.get("id", "")
        if vid_id and vid_id not in seen:
            seen.add(vid_id)
            videos.append({"id": vid_id, "title": entry.get("title", ""), "url": f"https://www.youtube.com/watch?v={vid_id}", "tab": entry.get("tab", "")})
    return channel_info, videos

def get_community_posts(channel_url: str) -> list:
    ydl_opts = {
        "quiet": True, "extract_flat": True, "skip_download": True,
        "ignoreerrors": True, "nocheckcertificate": True,
        "cookiefile": CHEMIN_COOKIES, "remote_components": ["ejs:github"],
    }
    posts = []
    try:
        url = channel_url.rstrip("/") + "/community"
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info: return []
        for entry in (info.get("entries", []) or []):
            if not entry: continue
            posts.append({"post_id": entry.get("id", ""), "content": entry.get("content", entry.get("title", "")), "timestamp": entry.get("timestamp", ""), "like_count": entry.get("like_count", 0)})
    except Exception: pass
    return posts

def get_video_details(video_url: str) -> dict:
    ydl_opts = {
        "quiet": True, "skip_download": True, "ignoreerrors": True,
        "nocheckcertificate": True, "cookiefile": CHEMIN_COOKIES,
        "remote_components": ["ejs:github"],
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
    if not info: return {}
    return {
        "id": info.get("id", ""), "title": info.get("title", ""),
        "description": info.get("description", ""),
        "duration_str": format_duration(info.get("duration")),
        "view_count": info.get("view_count", 0), "like_count": info.get("like_count", 0),
        "duration_sec": info.get("duration"), "comment_count": info.get("comment_count"),
        "upload_date": info.get("upload_date"), "thumbnail": info.get("thumbnail", ""),
        "tags": info.get("tags") or [], "categories": info.get("categories") or [],
        "language": info.get("language"), "chapters": info.get("chapters"),
    }

def get_video_subtitles(video_url: str) -> dict | None:
    ydl_opts = {
        "quiet": True, "skip_download": True, "ignoreerrors": True,
        "writesubtitles": True, "writeautomaticsub": True, "subtitlesformat": "json3",
        "cookiefile": CHEMIN_COOKIES, "remote_components": ["ejs:github"],
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
        if not info: return None
        auto_caps   = info.get("automatic_captions", {}) or {}
        manual_subs = info.get("subtitles", {}) or {}
        all_subs    = {**auto_caps, **manual_subs}
        if not all_subs: return None
        chosen_lang  = next((l for l in ["fr", "en", "de", "es"] if l in all_subs), list(all_subs.keys())[0])
        chosen_entry = next((e for e in all_subs[chosen_lang] if e.get("ext") == "json3"), None)
        if not chosen_entry: return None
        req = urllib.request.Request(chosen_entry["url"], headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            sub_json = json.loads(r.read().decode("utf-8"))
        text_parts = ["".join(s.get("utf8", "") for s in ev.get("segs", [])).strip() for ev in sub_json.get("events", [])]
        full_text  = " ".join([t for t in text_parts if t and t != "\n"]).strip()
        if not full_text: return None
        return {"lang": chosen_lang, "text": full_text, "source": "auto" if chosen_lang in auto_caps else "manual", "format": "json3", "available_langs": list(all_subs.keys())}
    except Exception: return None

def get_video_comments(video_url: str, max_comments=1000) -> list:
    ydl_opts = {
        "getcomments": True, "skip_download": True, "quiet": True,
        "no_warnings": True, "cookiefile": CHEMIN_COOKIES,
        "remote_components": ["ejs:github"],
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            comments = ydl.extract_info(video_url, download=False).get("comments", []) or []
    except Exception: return []
    top_comments, all_replies = {}, []
    for i, c in enumerate(comments):
        if max_comments and i >= max_comments: break
        try:
            is_reply = c.get("parent", "root") != "root"
            data = {"comment_id": c.get("id"), "author": c.get("author"), "text": c.get("text"), "likes": c.get("like_count", 0)}
            if not is_reply:
                data["replies"] = []
                top_comments[data["comment_id"]] = data
            else:
                data["parent_id"] = c.get("parent")
                all_replies.append(data)
        except Exception: continue
    for r in all_replies:
        if r["parent_id"] in top_comments:
            top_comments[r["parent_id"]]["replies"].append({"comment_id": r["comment_id"], "author": r["author"], "text": r["text"], "likes": r["likes"]})
    return list(top_comments.values())


# ══════════════════════════════════════════════════════════════════════
# 💾  SAUVEGARDE LOCALE COMMUNE
# ═══════════════════════════════════════════════
def sauvegarder_localement(backup_dir: str, safe_name: str, doc_data: dict):
    os.makedirs(backup_dir, exist_ok=True)
    backup_file = os.path.join(backup_dir, f"{safe_name}.json")
    current = {}
    if os.path.exists(backup_file):
        with open(backup_file, "r", encoding="utf-8") as f:
            try: current = json.load(f)
            except: pass
    current.update(doc_data)
    with open(backup_file, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)
    return backup_file


# ══════════════════════════════════════════════════════════════════════
# 🌐  TRAITEMENT SOCIÉTÉ SANS YOUTUBE
# ══════════════════════════════════════════════════════════════════════
def traiter_societe_sans_youtube(societe: dict, index: int, total: int):
    nom       = societe.get("NomSociete", "Inconnu")
    mongo_id  = societe.get("id_proleadfeeder", societe.get("id", ""))

    print(f"\n{'═'*55}")
    print(f"🌐 [{index}/{total}] Enregistrement Web Search (Pas de chaîne) : {nom}")
    print(f"   📮 Code postal : {societe.get('CodePostal', '')}")
    print(f"{'═'*55}")

    doc_data = {
        "id_proleadfeeder": mongo_id,
        "company_name":        nom,
        "type_traitement":     "web_search",
        "adresse":             societe.get("Adresse1", ""),
        "code_postal":         societe.get("CodePostal", ""),
        "telephone":           societe.get("TelFixe", ""),
        "site_web":            societe.get("SiteWeb", ""),
        "last_updated":        datetime.now().isoformat(),
        "status_recherche":    "Aucune chaine trouvee via recherche par paliers"
    }

    # MongoDB
    saved_mongo = False
    if mongo_available:
        try:
            channels_collection.update_one(
                {"id_proleadfeeder": mongo_id, "type_traitement": "web_search"},
                {"$set": doc_data},
                upsert=True
            )
            saved_mongo = True
        except Exception as e:
            log.error(f"    ❌ Erreur MongoDB : {e}")

    # Local
    safe_name = "".join([c for c in nom if c.isalnum() or c in (' ', '_', '-')]).strip()
    sauvegarder_localement(WEB_BACKUP_DIR, safe_name, doc_data)

    label = "✅ MongoDB + 💾 Local" if saved_mongo else "💾 Local uniquement"
    print(f"   📂 Sauvegarde : {label}")
    print(f"   ✅ Terminé : {nom}\n")


# ══════════════════════════════════════════════════════════════════════
# 🚀  SCRAPING YOUTUBE DIRECT
# ══════════════════════════════════════════════════════════════════════
def scrape_channel(societe: dict, channel_index: int, total_channels: int):
    channel_url = societe.get("Donnees_YouTube", {}).get("ChaineYouTube_Extraite", "")
    mongo_db_id = societe.get("id_proleadfeeder", societe.get("id", ""))

    channel_info, videos = get_channel_videos(channel_url)
    if not channel_info:
        print(f"  ⚠️  Impossible de récupérer les infos de la chaîne : {channel_url}")
        return

    community_posts = get_community_posts(channel_url)
    channel_id   = channel_info.get("id", "unknown")
    channel_name = channel_info.get("title", "Inconnu")
    nb_videos    = len(videos)

    print(f"\n{'═'*55}")
    print(f"🎬 [{channel_index}/{total_channels}] Chaîne : {channel_name}")
    print(f"   🆔 ID      : {channel_id}")
    print(f"   🔗 URL     : {channel_url}")
    print(f"   📹 Vidéos  : {nb_videos} trouvées")
    if community_posts:
        print(f"   💬 Posts   : {len(community_posts)} posts communautaires")
    print(f"{'═'*55}")

    # ── Initialisation MongoDB ──
    if mongo_available:
        try:
            update_data = {
                "id_proleadfeeder": mongo_db_id,
                "channel_name":        channel_name,
                "type_traitement":     "youtube",
                "description":         channel_info.get("description", ""),
                "last_updated":        datetime.now().isoformat(),
                "channel_url":         channel_info.get("channel_url", ""),
                "nb_videos":           channel_info.get("nb_videos", 0),
            }
            if community_posts:
                update_data["data.community_posts"] = community_posts
            channels_collection.update_one(
                {"channel_id": channel_id},
                {"$set": update_data},
                upsert=True
            )
        except Exception as e:
            log.error(f"    ❌ Erreur insertion MongoDB : {e}")

    # ── Vidéos ──
    done_ids     = load_checkpoint(channel_id)
    already_done = len(done_ids)
    if already_done > 0:
        print(f"  ⏩ {already_done} vidéo(s) déjà traitées (checkpoint), on reprend...")

    for idx, video in enumerate(videos, 1):
        vid_id = video["id"]
        title  = video.get("title", "Sans titre")
        tab    = video.get("tab", "videos") or "videos"

        if vid_id in done_ids:
            print(f"  ⏩ [{idx}/{nb_videos}] ({tab}) \"{title}\" → déjà traité, skip")
            continue

        print(f"  ▶  [{idx}/{nb_videos}] ({tab}) \"{title}\"")

        details   = with_retry(get_video_details, video["url"]) or {}
        subtitles = with_retry(get_video_subtitles, video["url"])
        comments  = with_retry(get_video_comments, video["url"], max_comments=MAX_COMMENTS) or []

        sub_status = f"✅ {subtitles['lang']}" if subtitles else "❌ aucune"
        print(f"       💬 Commentaires : {len(comments)} | 🗒️  Transcription : {sub_status}")

        video_data = {
            "video_id": vid_id, "title": details.get("title", title), "url": video["url"],
            "duration": details.get("duration_str", ""), "views": details.get("view_count", 0),
            "likes": details.get("like_count", 0), "transcription": subtitles, "comments": comments,
            "scraped_at": datetime.now().isoformat(), "description": details.get("description", ""),
            "thumbnail": details.get("thumbnail", ""), "tab": tab,
            "duration_sec": details.get("duration_sec"), "view_count": details.get("view_count", 0),
            "like_count": details.get("like_count", 0), "comment_count": details.get("comment_count"),
            "upload_date": details.get("upload_date"), "uploader": channel_name,
            "tags": details.get("tags") or [], "categories": details.get("categories") or [],
            "language": details.get("language"), "chapters": details.get("chapters")
        }

        saved_mongo = False
        if mongo_available:
            try:
                channels_collection.update_one(
                    {"channel_id": channel_id},
                    {"$push": {f"data.{tab}": video_data}},
                    upsert=True
                )
                saved_mongo = True
            except Exception as e:
                log.error(f"    ❌ Erreur MongoDB : {e}")

        # Sauvegarde locale YouTube
        safe_name = "".join([c for c in channel_name if c.isalnum() or c in (' ', '_', '-')]).strip()
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            backup_file = os.path.join(BACKUP_DIR, f"{safe_name}.json")
            current_data = {}
            if os.path.exists(backup_file):
                with open(backup_file, "r", encoding="utf-8") as f:
                    try: current_data = json.load(f)
                    except: pass
            current_data.update({
                "channel_id": channel_id, "id_proleadfeeder": mongo_db_id,
                "channel_name": channel_name, "type_traitement": "youtube",
                "description": channel_info.get("description", ""),
                "channel_url": channel_info.get("channel_url", ""),
                "nb_videos": channel_info.get("nb_videos", 0),
                "last_updated": datetime.now().isoformat()
            })
            if "data" not in current_data: current_data["data"] = {}
            if tab not in current_data["data"]: current_data["data"][tab] = []
            if community_posts and "community_posts" not in current_data["data"]:
                current_data["data"]["community_posts"] = community_posts
            current_data["data"][tab].append(video_data)
            with open(backup_file, "w", encoding="utf-8") as f:
                json.dump(current_data, f, indent=2, ensure_ascii=False)
            mongo_label = "✅ MongoDB + 💾 Local" if saved_mongo else "💾 Local uniquement"
            print(f"       📂 Sauvegarde : {mongo_label}")
        except Exception as e:
            log.error(f"    🚨 Erreur locale : {e}")

        done_ids.add(vid_id)
        save_checkpoint(channel_id, done_ids)
        wait_randomly()

    print(f"\n  ✅ YouTube terminé : \"{channel_name}\" — {nb_videos} vidéos traitées.\n")


# ══════════════════════════════════════════════════════════════════════
# 🎯  MAIN
# ══════════════════════════════════════════════════════════════════════
def main():
    # ── Étape 1 : extraction MongoDB → JSON ──
    executer_extraction()

    # ── Étape 2 : traitement de toutes les sociétés ──
    print("\n" + "=" * 70)
    print("  📺🌐  ÉTAPE 2 — TRAITEMENT (Scraping des chaînes validées)")
    print("=" * 70)

    societes = load_channels(CHANNELS_FILE)
    total    = len(societes)

    total_yt  = sum(1 for s in societes if s.get("type_traitement") == "youtube")
    total_web = sum(1 for s in societes if s.get("type_traitement") == "web_search")
    print(f"\n📊 Répartition : {total_yt} YouTube | {total_web} Sans Chaîne | {total} total\n")

    compteur_standby = 0

    for i, societe in enumerate(societes, 1):
        type_t = societe.get("type_traitement", "web_search")
        lien   = societe.get("Donnees_YouTube", {}).get("ChaineYouTube_Extraite", "")

        if type_t == "youtube" and lien and not lien.startswith("Erreur"):
            scrape_channel(societe, channel_index=i, total_channels=total)
        else:
            # --- STANDBY : voir l'interrupteur TRAITER_SOCIETES_SANS_YOUTUBE ---
            # Si False, on ne touche ni à MongoDB (sortez_youtube/web_search)
            # ni aux fichiers locaux de WEB_BACKUP_DIR pour cette société.
            if TRAITER_SOCIETES_SANS_YOUTUBE:
                traiter_societe_sans_youtube(societe, index=i, total=total)
            else:
                compteur_standby += 1

    if not TRAITER_SOCIETES_SANS_YOUTUBE and compteur_standby:
        print(f"\n⏸️  {compteur_standby} société(s) 'web_search' mises en STANDBY "
              f"(aucune écriture Mongo/locale). "
              f"Repasse TRAITER_SOCIETES_SANS_YOUTUBE à True pour les traiter.")

    print(f"\n🎉 Traitement terminé — {total} société(s) traitées.")

if __name__ == "__main__":
    main()
