"""
Téléchargeur Audio — Interface Streamlit

Dépendances : streamlit, yt-dlp, ffmpeg (système), requests, beautifulsoup4
packages.txt (Streamlit Cloud) : ffmpeg
"""

import os
import re
import shutil
import tempfile
from urllib.parse import urljoin

import requests
import streamlit as st
import yt_dlp
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
# Configuration de la page
# ─────────────────────────────────────────────

st.set_page_config(
    page_title="Téléchargeur Audio",
    page_icon="🎙️",
    layout="centered",
)

st.title("🎙️ Téléchargeur Audio")
st.caption(
    "Télécharge l'audio d'une vidéo en MP3 — URL unique ou toutes les vidéos d'une page."
)

# ─────────────────────────────────────────────
# Utilitaires
# ─────────────────────────────────────────────

def find_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return os.path.dirname(found)
    for path in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        if os.path.exists(os.path.join(path, "ffmpeg")):
            return path
    return None


FFMPEG_LOCATION = find_ffmpeg()

if FFMPEG_LOCATION:
    st.success(f"✅ ffmpeg détecté : `{FFMPEG_LOCATION}`")
else:
    st.error(
        "❌ ffmpeg introuvable. "
        "Sur Streamlit Cloud : ajoute `ffmpeg` dans un fichier `packages.txt` à la racine du repo. "
        "En local : `brew install ffmpeg` (Mac) ou `apt install ffmpeg` (Linux)."
    )


def get_ydl_opts(output_dir: str) -> dict:
    opts = {
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        "outtmpl": os.path.join(output_dir, "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "noplaylist": False,
    }
    if FFMPEG_LOCATION:
        opts["ffmpeg_location"] = FFMPEG_LOCATION
    return opts


def download_single_audio(url: str, output_dir: str) -> tuple[bool, str, str]:
    try:
        opts = get_ydl_opts(output_dir)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return False, "", "yt-dlp : aucune information extraite."
            title = info.get("title", "audio_sans_titre")
            for f in os.listdir(output_dir):
                if f.endswith(".mp3"):
                    return True, title, os.path.join(output_dir, f)
            return False, title, "MP3 non créé — ffmpeg a peut-être échoué."
    except Exception as exc:
        return False, "", str(exc)


def scrape_senat_videos(
    base_url: str, num_pages: int, session: requests.Session
) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    commission_match = re.search(r"commission=([^&]+)", base_url)
    commission = commission_match.group(1) if commission_match else "DIST"

    for page in range(1, num_pages + 1):
        search_url = (
            f"https://videos.senat.fr/senat_videos_search.php"
            f"?commission={commission}&page={page}"
        )
        try:
            resp = session.get(search_url, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            st.warning(f"Impossible de charger la page {page} : {exc}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        video_pattern = re.compile(r"^video\.\w+")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if video_pattern.match(href):
                full_url = f"https://videos.senat.fr/{href}"
                title = a.get("title") or a.get_text(strip=True) or href
                entry = (full_url, title)
                if entry not in results:
                    results.append(entry)
    return results


def scrape_generic_videos(
    page_url: str, num_pages: int, session: requests.Session
) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    separator = "&" if "?" in page_url else "?"

    for page in range(1, num_pages + 1):
        url = f"{page_url}{separator}page={page}" if num_pages > 1 else page_url
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            st.warning(f"Impossible de charger {url} : {exc}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")

        def add(href: str, title: str = "") -> None:
            full = urljoin(url, href)
            if (full, title) not in results:
                results.append((full, title))

        for tag in soup.find_all(["video", "source"]):
            src = tag.get("src") or tag.get("data-src")
            if src:
                add(src)

        video_ext = re.compile(
            r"\.(mp4|webm|ogv|avi|mov|mkv|flv|m4v|ts)(\?.*)?$", re.IGNORECASE
        )
        for a in soup.find_all("a", href=True):
            if video_ext.search(a["href"]):
                add(a["href"], a.get("title", ""))

        for iframe in soup.find_all("iframe", src=True):
            src = iframe["src"]
            if any(kw in src for kw in ["video", "player", "embed", "youtube", "vimeo"]):
                add(src)

    return results


# ─────────────────────────────────────────────
# Interface — onglets
# ─────────────────────────────────────────────

tab_single, tab_multi = st.tabs(["🔗 URL unique", "📄 Page complète"])

# ── Onglet 1 : URL unique ──────────────────────────────────────────────────────

with tab_single:
    st.subheader("Télécharger l'audio d'une vidéo")
    st.markdown(
        "Fonctionne avec YouTube, Vimeo, Dailymotion, videos.senat.fr, "
        "et des centaines d'autres plateformes supportées par **yt-dlp**."
    )

    url_input = st.text_input(
        "URL de la vidéo",
        placeholder="https://videos.senat.fr/video.5733212_69c510203522b.audition-daldi-france",
        key="single_url",
    )

    if st.button("⬇️ Télécharger l'audio", key="btn_single", type="primary"):
        if not url_input.strip():
            st.error("Saisis une URL valide.")
        else:
            audio_bytes = None
            mp3_filename = "audio.mp3"
            title = ""
            error_msg = ""

            with st.spinner("Extraction de l'audio en cours…"):
                with tempfile.TemporaryDirectory() as tmpdir:
                    success, title, mp3_path = download_single_audio(
                        url_input.strip(), tmpdir
                    )
                    if success and mp3_path and os.path.isfile(mp3_path):
                        with open(mp3_path, "rb") as f:
                            audio_bytes = f.read()
                        mp3_filename = os.path.basename(mp3_path)
                    else:
                        error_msg = mp3_path

            if audio_bytes:
                st.success(f"✅ Audio extrait : **{title}**")
                st.audio(audio_bytes, format="audio/mp3")
                st.download_button(
                    label="💾 Télécharger le MP3",
                    data=audio_bytes,
                    file_name=mp3_filename,
                    mime="audio/mpeg",
                    type="primary",
                )
            else:
                st.error(f"❌ Échec : {error_msg}")

# ── Onglet 2 : Page complète ───────────────────────────────────────────────────

with tab_multi:
    st.subheader("Télécharger tous les audios d'une page")
    st.info(
        "🎙️ Supporte nativement **videos.senat.fr** — colle l'URL de la commission "
        "et indique le nombre de pages."
    )

    col1, col2 = st.columns([3, 1])
    with col1:
        page_url_input = st.text_input(
            "URL de la page",
            placeholder="https://videos.senat.fr/videos.php?commission=DIST",
            key="multi_url",
        )
    with col2:
        num_pages = st.number_input(
            "Nb de pages", min_value=1, max_value=50, value=7, step=1,
        )

    # ── Étape 1 : Analyse ────────────────────────────────────────────────────

    if st.button("🔍 Analyser la page", key="btn_multi", type="primary"):
        if not page_url_input.strip():
            st.error("Saisis une URL valide.")
        else:
            # Réinitialisation complète de la session
            for key in ("entries", "queue", "processing_index", "results"):
                st.session_state.pop(key, None)

            session = requests.Session()
            session.headers.update(
                {"User-Agent": "Mozilla/5.0 (compatible; AudioDownloader/1.0)"}
            )

            with st.spinner("Scraping en cours…"):
                is_senat = "videos.senat.fr" in page_url_input
                entries = (
                    scrape_senat_videos(page_url_input.strip(), int(num_pages), session)
                    if is_senat
                    else scrape_generic_videos(page_url_input.strip(), int(num_pages), session)
                )

            st.session_state["entries"] = entries

    # ── Étape 2 : Sélection par cases à cocher ───────────────────────────────

    entries: list | None = st.session_state.get("entries")

    if entries is not None:
        if not entries:
            st.error("❌ Aucune vidéo trouvée.")
        else:
            total = len(entries)

            # Initialise les cases cochées si pas encore fait
            if "checked" not in st.session_state:
                st.session_state["checked"] = [False] * total

            # Remet à la bonne taille si on a relancé une analyse
            if len(st.session_state["checked"]) != total:
                st.session_state["checked"] = [False] * total

            # En cours de traitement automatique ?
            is_processing = st.session_state.get("processing_index") is not None

            if not is_processing:
                # ── Interface de sélection ────────────────────────────────────
                st.markdown(f"**{total} vidéo(s) trouvée(s)** — coche celles à télécharger :")

                col_a, col_b = st.columns(2)
                with col_a:
                    if st.button("☑️ Tout sélectionner"):
                        st.session_state["checked"] = [True] * total
                        st.rerun()
                with col_b:
                    if st.button("☐ Tout désélectionner"):
                        st.session_state["checked"] = [False] * total
                        st.rerun()

                st.divider()

                for idx, (url, title) in enumerate(entries):
                    st.session_state["checked"][idx] = st.checkbox(
                        f"**{idx + 1}.** {title}",
                        value=st.session_state["checked"][idx],
                        key=f"chk_{idx}",
                    )

                st.divider()

                nb_selected = sum(st.session_state["checked"])
                if nb_selected == 0:
                    st.info("Coche au moins une vidéo pour lancer le téléchargement.")
                else:
                    if st.button(
                        f"⬇️ Télécharger les {nb_selected} audio(s) sélectionné(s)",
                        type="primary",
                        key="btn_start_dl",
                    ):
                        # Construit la file d'attente à partir des cases cochées
                        queue = [
                            entries[i]
                            for i, checked in enumerate(st.session_state["checked"])
                            if checked
                        ]
                        st.session_state["queue"] = queue
                        st.session_state["processing_index"] = 0
                        st.session_state["results"] = []   # list[dict]
                        st.rerun()

            else:
                # ── Traitement automatique un par un ─────────────────────────
                queue: list = st.session_state["queue"]
                idx: int = st.session_state["processing_index"]
                results: list = st.session_state["results"]
                total_q = len(queue)

                # Affiche la progression globale
                st.markdown(f"### ⏳ Traitement en cours… {idx}/{total_q}")
                st.progress(idx / total_q)

                # Affiche les fichiers déjà prêts
                if results:
                    st.markdown("---")
                    st.markdown("**Fichiers prêts à télécharger :**")
                    for r in results:
                        if r["success"]:
                            st.download_button(
                                label=f"💾 {r['title']}",
                                data=r["data"],
                                file_name=r["filename"],
                                mime="audio/mpeg",
                                key=f"dl_done_{r['idx']}",
                            )
                        else:
                            st.error(f"❌ {r['title']} — {r['error']}")

                # Traite le fichier suivant dans la file
                if idx < total_q:
                    url, title = queue[idx]
                    with st.spinner(f"🎵 {idx + 1}/{total_q} — {title[:70]}…"):
                        with tempfile.TemporaryDirectory() as tmpdir:
                            success, dl_title, mp3_path = download_single_audio(url, tmpdir)
                            if success and mp3_path and os.path.isfile(mp3_path):
                                with open(mp3_path, "rb") as f:
                                    audio_data = f.read()
                                results.append({
                                    "idx": idx,
                                    "success": True,
                                    "title": dl_title or title,
                                    "data": audio_data,
                                    "filename": os.path.basename(mp3_path),
                                })
                            else:
                                results.append({
                                    "idx": idx,
                                    "success": False,
                                    "title": title,
                                    "error": mp3_path,
                                })

                    st.session_state["results"] = results
                    st.session_state["processing_index"] = idx + 1
                    st.rerun()  # → passe automatiquement au fichier suivant

                else:
                    # ── Tout est terminé ──────────────────────────────────────
                    nb_ok = sum(1 for r in results if r["success"])
                    nb_err = total_q - nb_ok

                    st.success(f"✅ Terminé — **{nb_ok} MP3** prêts"
                               + (f" · {nb_err} échec(s)" if nb_err else "") + ".")
                    st.progress(1.0)
                    st.markdown("---")
                    st.markdown("**Télécharge tes fichiers :**")

                    for r in results:
                        if r["success"]:
                            st.download_button(
                                label=f"💾 {r['title']}",
                                data=r["data"],
                                file_name=r["filename"],
                                mime="audio/mpeg",
                                key=f"dl_final_{r['idx']}",
                            )
                        else:
                            st.error(f"❌ {r['title']} — {r['error']}")

                    if st.button("🔄 Nouvelle sélection", key="btn_reset"):
                        for key in ("queue", "processing_index", "results", "checked"):
                            st.session_state.pop(key, None)
                        st.rerun()

# ─────────────────────────────────────────────
# Pied de page
# ─────────────────────────────────────────────

st.divider()
st.caption(
    "Powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp) · "
    "Respecte les conditions d'utilisation des sites sources."
)
