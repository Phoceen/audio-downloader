"""
Téléchargeur Audio — Interface Streamlit

Dépendances : streamlit, yt-dlp, ffmpeg (système), requests, beautifulsoup4
packages.txt (Streamlit Cloud) : ffmpeg
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
# Utilitaires
# ─────────────────────────────────────────────

BATCH_SIZE = 5  # Nombre de fichiers traités par lot (évite le crash serveur)


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


def build_zip(mp3_data: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, fbytes in mp3_data:
            zf.writestr(fname, fbytes)
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
            # Réinitialisation complète
            for key in ("entries", "batch_index", "mp3_data", "dl_logs"):
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
            st.session_state["batch_index"] = 0
            st.session_state["mp3_data"] = []   # list[tuple[str, bytes]]
            st.session_state["dl_logs"] = []

    # ── Étape 2 : Affichage de la liste ──────────────────────────────────────

    entries: list | None = st.session_state.get("entries")

    if entries is not None:
        if not entries:
            st.error("❌ Aucune vidéo trouvée.")
        else:
            batch_index: int = st.session_state.get("batch_index", 0)
            mp3_data: list = st.session_state.get("mp3_data", [])
            total = len(entries)
            done = batch_index >= total

            # Barre de progression globale
            if batch_index > 0:
                st.progress(min(batch_index / total, 1.0))
                st.caption(
                    f"**{len(mp3_data)} MP3 récupérés** sur {batch_index} tentées "
                    f"({total - batch_index} restantes)"
                    if not done
                    else f"**Terminé — {len(mp3_data)} MP3** sur {total} tentatives."
                )

            with st.expander(f"📋 Liste des vidéos ({total})", expanded=(batch_index == 0)):
                for url, title in entries:
                    st.markdown(f"- **{title}** — `{url}`")

            # ── Bouton de lancement / continuation ───────────────────────────

            if not done:
                remaining = total - batch_index
                label = (
                    f"⬇️ Démarrer le téléchargement ({total} audios, par lots de {BATCH_SIZE})"
                    if batch_index == 0
                    else f"▶️ Continuer — lot suivant ({remaining} restants)"
                )

                if st.button(label, key="btn_batch", type="primary"):
                    batch_end = min(batch_index + BATCH_SIZE, total)
                    batch = entries[batch_index:batch_end]

                    dl_progress = st.progress(0)
                    dl_status = st.empty()
                    logs: list = st.session_state["dl_logs"]

                    with tempfile.TemporaryDirectory() as tmpdir:
                        for i, (url, title) in enumerate(batch, 1):
                            label_short = (title or url)[:70]
                            dl_status.text(
                                f"⏳ {batch_index + i}/{total} — {label_short}…"
                            )
                            dl_progress.progress(i / len(batch))

                            success, _, mp3_path = download_single_audio(url, tmpdir)

                            if success and mp3_path and os.path.isfile(mp3_path):
                                with open(mp3_path, "rb") as f:
                                    st.session_state["mp3_data"].append(
                                        (os.path.basename(mp3_path), f.read())
                                    )
                                logs.append(f"✅ {batch_index + i}/{total} — {label_short}")
                            else:
                                logs.append(
                                    f"❌ {batch_index + i}/{total} — {label_short} → {mp3_path}"
                                )

                    dl_progress.empty()
                    dl_status.empty()
                    st.session_state["batch_index"] = batch_end
                    st.session_state["dl_logs"] = logs
                    st.rerun()

            # ── Téléchargement final (ZIP) dès qu'il y a des fichiers ─────────

            mp3_data = st.session_state.get("mp3_data", [])

            if mp3_data:
                st.divider()

                # Journal des erreurs éventuelles
                logs = st.session_state.get("dl_logs", [])
                errors = [l for l in logs if l.startswith("❌")]
                if errors:
                    with st.expander(f"⚠️ {len(errors)} échec(s) — voir le journal"):
                        st.text("\n".join(logs))

                if done:
                    st.success(f"✅ Tous les lots traités — **{len(mp3_data)} MP3** prêts.")
                else:
                    st.info(
                        f"💡 **{len(mp3_data)} MP3 déjà disponibles.** "
                        "Tu peux les télécharger maintenant ou continuer pour en récupérer plus."
                    )

                if len(mp3_data) == 1:
                    st.download_button(
                        label="💾 Télécharger le MP3",
                        data=mp3_data[0][1],
                        file_name=mp3_data[0][0],
                        mime="audio/mpeg",
                        type="primary",
                    )
                else:
                    zip_bytes = build_zip(mp3_data)
                    st.download_button(
                        label=f"📦 Télécharger l'archive ZIP ({len(mp3_data)} MP3)",
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
