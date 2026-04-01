"""
Téléchargeur Audio — Interface Streamlit
Dépendances : streamlit, yt-dlp, ffmpeg (système), requests, beautifulsoup4

Installation :
    pip install streamlit yt-dlp requests beautifulsoup4
    # ffmpeg doit être installé sur le système (apt install ffmpeg / brew install ffmpeg)

Lancement :
    streamlit run app.py
"""

import io
import os
import re
import tempfile
import zipfile
from urllib.parse import urljoin, urlparse

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

def sanitize_filename(name: str) -> str:
    """Nettoie un nom de fichier pour éviter les caractères illégaux."""
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    return name.strip()[:120]  # limite raisonnable


def get_ydl_opts(output_dir: str) -> dict:
    """Options yt-dlp communes pour l'extraction audio en MP3."""
    return {
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
        "ignoreerrors": True,   # continue même si une URL échoue
        "noplaylist": False,    # accepte les playlists si yt-dlp les détecte
    }


def download_single_audio(url: str, output_dir: str) -> tuple[bool, str, str]:
    """
    Télécharge l'audio d'une URL.
    Retourne (succès, titre, chemin_fichier_mp3).
    """
    try:
        with yt_dlp.YoutubeDL(get_ydl_opts(output_dir)) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return False, "", ""
            title = info.get("title", "audio_sans_titre")
            # yt-dlp renomme l'extension en .mp3 après post-traitement
            mp3_path = os.path.join(output_dir, sanitize_filename(title) + ".mp3")
            # Cherche le fichier réellement créé (le titre peut différer légèrement)
            for f in os.listdir(output_dir):
                if f.endswith(".mp3"):
                    mp3_path = os.path.join(output_dir, f)
                    break
            return True, title, mp3_path
    except Exception as exc:
        return False, "", str(exc)


def scrape_video_urls(page_url: str, session: requests.Session) -> list[str]:
    """
    Scrape une page HTML et extrait les URLs de vidéos candidates.
    Stratégie multi-couches :
      1. Balises <video> et <source>
      2. Attributs data-* contenant « video » ou « src »
      3. Liens <a href> pointant vers des extensions vidéo connues
      4. Pattern spécifique videos.senat.fr (liens /video.XXXXXXX)
    """
    found: list[str] = []

    try:
        resp = session.get(page_url, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        st.warning(f"Impossible de charger {page_url} : {exc}")
        return found

    soup = BeautifulSoup(resp.text, "html.parser")

    def add(url: str) -> None:
        full = urljoin(page_url, url)
        if full not in found:
            found.append(full)

    # 1. Balises vidéo HTML5
    for tag in soup.find_all(["video", "source"]):
        src = tag.get("src") or tag.get("data-src")
        if src:
            add(src)

    # 2. Liens <a> vers des fichiers vidéo ou pages vidéo
    video_extensions = re.compile(
        r"\.(mp4|webm|ogv|avi|mov|mkv|flv|m4v|ts)(\?.*)?$", re.IGNORECASE
    )
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if video_extensions.search(href):
            add(href)

    # 3. Pattern Sénat : liens vers /video.XXXXXX (page de la vidéo)
    senat_pattern = re.compile(r"/video\.\w+", re.IGNORECASE)
    for a in soup.find_all("a", href=True):
        if senat_pattern.search(a["href"]):
            add(a["href"])

    # 4. iframes embarquées (cas fréquent pour les players externes)
    for iframe in soup.find_all("iframe", src=True):
        iframe_src = iframe["src"]
        # On inclut les iframes qui ressemblent à des players vidéo
        if any(
            kw in iframe_src
            for kw in ["video", "player", "embed", "youtube", "vimeo", "dailymotion"]
        ):
            add(iframe_src)

    return found


def build_paginated_urls(base_url: str, num_pages: int) -> list[str]:
    """
    Génère les URLs paginées.
    Essaie d'abord le paramètre GET 'page', sinon 'p'.
    """
    separator = "&" if "?" in base_url else "?"
    pages = []
    for i in range(1, num_pages + 1):
        pages.append(f"{base_url}{separator}page={i}")
    return pages


def download_multiple(urls: list[str], output_dir: str, progress_bar, status_text) -> list[str]:
    """
    Télécharge l'audio pour une liste d'URLs.
    Retourne la liste des fichiers MP3 créés.
    """
    mp3_files: list[str] = []
    total = len(urls)

    for idx, url in enumerate(urls, 1):
        status_text.text(f"Téléchargement {idx}/{total} — {url[:80]}…")
        progress_bar.progress(idx / total)

        success, title, mp3_path = download_single_audio(url, output_dir)
        if success and mp3_path and os.path.isfile(mp3_path):
            mp3_files.append(mp3_path)
        else:
            st.warning(f"⚠️ Échec pour : {url}")

    return mp3_files


def create_zip(mp3_files: list[str]) -> bytes:
    """Crée un zip en mémoire contenant tous les MP3."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in mp3_files:
            zf.write(f, os.path.basename(f))
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────
# Interface — onglets
# ─────────────────────────────────────────────

tab_single, tab_multi = st.tabs(["🔗 URL unique", "📄 Page complète"])


# ── Onglet 1 : URL unique ──────────────────────────────────────────────────────
with tab_single:
    st.subheader("Télécharger l'audio d'une vidéo")
    st.markdown(
        "Fonctionne avec YouTube, Vimeo, Dailymotion, et des centaines d'autres "
        "plateformes supportées par **yt-dlp**."
    )

    url_input = st.text_input(
        "URL de la vidéo",
        placeholder="https://www.youtube.com/watch?v=...",
        key="single_url",
    )

    if st.button("⬇️ Télécharger l'audio", key="btn_single", type="primary"):
        if not url_input.strip():
            st.error("Saisis une URL valide.")
        else:
            with st.spinner("Extraction de l'audio en cours…"):
                with tempfile.TemporaryDirectory() as tmpdir:
                    success, title, mp3_path = download_single_audio(
                        url_input.strip(), tmpdir
                    )
                    if success and mp3_path and os.path.isfile(mp3_path):
                        with open(mp3_path, "rb") as f:
                            audio_bytes = f.read()
                        filename = os.path.basename(mp3_path)
                        st.success(f"✅ Audio extrait : **{title}**")
                        st.audio(audio_bytes, format="audio/mp3")
                        st.download_button(
                            label="💾 Télécharger le MP3",
                            data=audio_bytes,
                            file_name=filename,
                            mime="audio/mpeg",
                            type="primary",
                        )
                    else:
                        st.error(
                            "❌ Échec du téléchargement. "
                            "Vérifie que l'URL est accessible et que ffmpeg est installé."
                        )
                        if mp3_path:  # mp3_path contient le message d'erreur ici
                            st.code(mp3_path)


# ── Onglet 2 : Page complète ───────────────────────────────────────────────────
with tab_multi:
    st.subheader("Télécharger tous les audios d'une page")

    st.info(
        "🔍 L'outil scrape la page pour trouver les vidéos, "
        "puis télécharge chaque audio. Pour les sites paginés (ex. Sénat), "
        "indique le nombre de pages à parcourir."
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
            "Nb de pages",
            min_value=1,
            max_value=50,
            value=1,
            step=1,
            help="Si le site est paginé, indique le nombre total de pages à scraper.",
        )

    with st.expander("⚙️ Options avancées"):
        st.markdown(
            "**Note sur le scraping :** Le scraper détecte automatiquement les vidéos "
            "les plus courantes. Pour `videos.senat.fr`, il cherche les liens `/video.XXXXX` "
            "caractéristiques du site. Si aucune vidéo n't'est trouvée, inspecte le HTML "
            "de la page et ajuste le code dans `scrape_video_urls()`."
        )
        deduplicate = st.checkbox("Dédoublonner les URLs trouvées", value=True)
        max_videos = st.number_input(
            "Limite de vidéos (0 = illimité)",
            min_value=0,
            max_value=500,
            value=0,
        )

    if st.button("🔍 Analyser la page puis télécharger", key="btn_multi", type="primary"):
        if not page_url_input.strip():
            st.error("Saisis une URL valide.")
        else:
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; AudioDownloader/1.0; "
                        "+https://github.com)"
                    )
                }
            )

            # Étape 1 : scraping de toutes les pages
            all_video_urls: list[str] = []
            page_urls = build_paginated_urls(page_url_input.strip(), int(num_pages))

            scrape_progress = st.progress(0)
            scrape_status = st.empty()

            for i, p_url in enumerate(page_urls, 1):
                scrape_status.text(f"Scraping page {i}/{len(page_urls)}…")
                scrape_progress.progress(i / len(page_urls))
                found = scrape_video_urls(p_url, session)
                all_video_urls.extend(found)

            if deduplicate:
                all_video_urls = list(dict.fromkeys(all_video_urls))

            if max_videos and max_videos > 0:
                all_video_urls = all_video_urls[:max_videos]

            scrape_status.empty()
            scrape_progress.empty()

            if not all_video_urls:
                st.error(
                    "❌ Aucune vidéo trouvée sur cette page. "
                    "Le site utilise peut-être du JavaScript pour charger les vidéos "
                    "(rendu côté client). Dans ce cas, yt-dlp ne pourra pas les détecter "
                    "via le scraping HTML. Consulte la section ⚙️ Options avancées."
                )
            else:
                st.success(f"✅ {len(all_video_urls)} vidéo(s) trouvée(s).")

                with st.expander(f"📋 Liste des URLs ({len(all_video_urls)})"):
                    for u in all_video_urls:
                        st.markdown(f"- {u}")

                if st.button(
                    f"⬇️ Télécharger les {len(all_video_urls)} audio(s)",
                    key="btn_download_all",
                ):
                    dl_progress = st.progress(0)
                    dl_status = st.empty()

                    with tempfile.TemporaryDirectory() as tmpdir:
                        mp3_files = download_multiple(
                            all_video_urls, tmpdir, dl_progress, dl_status
                        )
                        dl_progress.empty()
                        dl_status.empty()

                        if not mp3_files:
                            st.error(
                                "❌ Aucun fichier MP3 créé. "
                                "Vérifie que ffmpeg est installé et que les URLs "
                                "sont accessibles."
                            )
                        elif len(mp3_files) == 1:
                            with open(mp3_files[0], "rb") as f:
                                data = f.read()
                            st.success("✅ 1 fichier MP3 prêt.")
                            st.download_button(
                                label="💾 Télécharger le MP3",
                                data=data,
                                file_name=os.path.basename(mp3_files[0]),
                                mime="audio/mpeg",
                                type="primary",
                            )
                        else:
                            st.success(
                                f"✅ {len(mp3_files)} fichiers MP3 prêts (sur {len(all_video_urls)} tentatives)."
                            )
                            zip_bytes = create_zip(mp3_files)
                            st.download_button(
                                label=f"📦 Télécharger l'archive ZIP ({len(mp3_files)} MP3)",
                                data=zip_bytes,
                                file_name="audios.zip",
                                mime="application/zip",
                                type="primary",
                            )


# ─────────────────────────────────────────────
# Pied de page
# ─────────────────────────────────────────────
st.divider()
st.caption(
    "Powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp) · "
    "Respecte les conditions d'utilisation des sites sources."
)
