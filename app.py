"""
Téléchargeur Audio — Interface Streamlit

Dépendances : streamlit, yt-dlp, ffmpeg (système), requests, beautifulsoup4
"""

import io
import os
import re
import shutil
import tempfile
import zipfile
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
# Initialisation du session_state
# ─────────────────────────────────────────────

if "scraped_entries" not in st.session_state:
    st.session_state.scraped_entries = []
if "scrape_done" not in st.session_state:
    st.session_state.scrape_done = False

# ─────────────────────────────────────────────
# Utilitaires
# ─────────────────────────────────────────────

def find_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return os.path.dirname(found)
    if os.path.exists("/opt/homebrew/bin/ffmpeg"):
        return "/opt/homebrew/bin"
    if os.path.exists("/usr/local/bin/ffmpeg"):
        return "/usr/local/bin"
    if os.path.exists("/usr/bin/ffmpeg"):
        return "/usr/bin"
    return None


FFMPEG_LOCATION = find_ffmpeg()

if FFMPEG_LOCATION:
    st.success(f"✅ ffmpeg détecté : `{FFMPEG_LOCATION}`")
else:
    st.error(
        "❌ ffmpeg introuvable. "
        "Installe-le : `brew install ffmpeg` (Mac) ou `apt install ffmpeg` (Linux)."
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
        "ignoreerrors": True,
        "noplaylist": False,
    }
    if FFMPEG_LOCATION:
        opts["ffmpeg_location"] = FFMPEG_LOCATION
    return opts


def download_single_audio(url: str, output_dir: str) -> tuple[bool, str, str]:
    try:
        opts = get_ydl_opts(output_dir)
        opts["ignoreerrors"] = False
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                return False, "", "yt-dlp n'a pas pu extraire d'informations pour cette URL."
            title = info.get("title", "audio_sans_titre")
            for f in os.listdir(output_dir):
                if f.endswith(".mp3"):
                    return True, title, os.path.join(output_dir, f)
            return False, title, "Le fichier MP3 n'a pas été créé — ffmpeg a peut-être échoué."
    except Exception as exc:
        return False, "", str(exc)


def scrape_senat_videos(
    base_url: str, num_pages: int, session: requests.Session
) -> list[tuple[str, str]]:
    """
    Scrape spécifique pour videos.senat.fr.
    Utilise senat_videos_search.php pour récupérer les vidéos page par page.
    Retourne une liste de (url_video, titre).
    """
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

        # Pattern : href="video.XXXXXXX_YYYYYYY.titre-de-la-video"
        video_pattern = re.compile(r"^video\.")
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
    """
    Scraper générique pour les autres sites.
    """
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

        # Balises vidéo HTML5
        for tag in soup.find_all(["video", "source"]):
            src = tag.get("src") or tag.get("data-src")
            if src:
                add(src)

        # Liens vers fichiers vidéo
        video_ext = re.compile(
            r"\.(mp4|webm|ogv|avi|mov|mkv|flv|m4v|ts)(\?.*)?$", re.IGNORECASE
        )
        for a in soup.find_all("a", href=True):
            if video_ext.search(a["href"]):
                add(a["href"], a.get("title", ""))

        # iframes
        for iframe in soup.find_all("iframe", src=True):
            src = iframe["src"]
            if any(kw in src for kw in ["video", "player", "embed", "youtube", "vimeo"]):
                add(src)

    return results


def download_multiple(
    entries: list[tuple[str, str]], output_dir: str, progress_bar, status_text
) -> list[str]:
    mp3_files: list[str] = []
    total = len(entries)
    for idx, (url, title) in enumerate(entries, 1):
        label = title or url[:60]
        status_text.text(f"Téléchargement {idx}/{total} — {label}…")
        progress_bar.progress(idx / total)
        success, _, mp3_path = download_single_audio(url, output_dir)
        if success and mp3_path and os.path.isfile(mp3_path):
            mp3_files.append(mp3_path)
        else:
            st.warning(f"⚠️ Échec : {label}\n{mp3_path}")
    return mp3_files


def create_zip(mp3_files: list[str]) -> bytes:
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
            with st.spinner("Extraction de l'audio en cours…"):
                with tempfile.TemporaryDirectory() as tmpdir:
                    success, title, mp3_path = download_single_audio(
                        url_input.strip(), tmpdir
                    )
                    if success and mp3_path and os.path.isfile(mp3_path):
                        with open(mp3_path, "rb") as f:
                            audio_bytes = f.read()
                        st.success(f"✅ Audio extrait : **{title}**")
                        st.audio(audio_bytes, format="audio/mp3")
                        st.download_button(
                            label="💾 Télécharger le MP3",
                            data=audio_bytes,
                            file_name=os.path.basename(mp3_path),
                            mime="audio/mpeg",
                            type="primary",
                        )
                    else:
                        st.error("❌ Échec du téléchargement.")
                        if mp3_path:
                            st.code(mp3_path)

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
            "Nb de pages",
            min_value=1,
            max_value=50,
            value=7,
            step=1,
        )

    # ── Étape 1 : Analyser ────────────────────────────────────────────────────

    if st.button("🔍 Analyser la page", key="btn_scrape", type="secondary"):
        if not page_url_input.strip():
            st.error("Saisis une URL valide.")
        else:
            # Réinitialiser les résultats précédents
            st.session_state.scraped_entries = []
            st.session_state.scrape_done = False

            session = requests.Session()
            session.headers.update(
                {"User-Agent": "Mozilla/5.0 (compatible; AudioDownloader/1.0)"}
            )

            with st.spinner("Scraping en cours…"):
                is_senat = "videos.senat.fr" in page_url_input
                if is_senat:
                    entries = scrape_senat_videos(
                        page_url_input.strip(), int(num_pages), session
                    )
                else:
                    entries = scrape_generic_videos(
                        page_url_input.strip(), int(num_pages), session
                    )

            # ✅ Stocker dans session_state — survit au prochain rerun
            st.session_state.scraped_entries = entries
            st.session_state.scrape_done = True

    # ── Étape 2 : Afficher les résultats et proposer le téléchargement ────────

    if st.session_state.scrape_done:
        entries = st.session_state.scraped_entries

        if not entries:
            st.error("❌ Aucune vidéo trouvée sur cette page.")
        else:
            st.success(f"✅ {len(entries)} vidéo(s) trouvée(s).")

            with st.expander(f"📋 Liste des vidéos ({len(entries)})"):
                for url, title in entries:
                    st.markdown(f"- **{title}** — `{url}`")

            # ── Étape 3 : Télécharger ─────────────────────────────────────────
            # Ce bouton est en dehors du bloc du premier bouton :
            # il s'affiche à chaque rerun tant que scrape_done est True.

            if st.button(
                f"⬇️ Télécharger les {len(entries)} audio(s)",
                key="btn_download_all",
                type="primary",
            ):
                dl_progress = st.progress(0)
                dl_status = st.empty()

                with tempfile.TemporaryDirectory() as tmpdir:
                    mp3_files = download_multiple(
                        entries, tmpdir, dl_progress, dl_status
                    )
                    dl_progress.empty()
                    dl_status.empty()

                    if not mp3_files:
                        st.error("❌ Aucun fichier MP3 créé.")
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
                            f"✅ {len(mp3_files)} fichiers MP3 prêts "
                            f"(sur {len(entries)} tentatives)."
                        )
                        zip_bytes = create_zip(mp3_files)
                        st.download_button(
                            label=f"📦 Télécharger l'archive ZIP ({len(mp3_files)} MP3)",
                            data=zip_bytes,
                            file_name="audios_senat.zip",
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
