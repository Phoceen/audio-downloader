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
                return False, "", "yt-dlp n'a pas pu extraire d'informations."
            title = info.get("title", "audio_sans_titre")
            for f in os.listdir(output_dir):
                if f.endswith(".mp3"):
                    return True, title, os.path.join(output_dir, f)
            return False, title, "Fichier MP3 non créé — ffmpeg a peut-être échoué."
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
                st.error(f"❌ Échec du téléchargement : {error_msg}")

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
            st.session_state.pop("entries", None)
            st.session_state.pop("download_result", None)

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

            st.session_state["entries"] = entries

    # ── Étape 2 : Affichage de la liste (persiste entre reruns) ─────────────

    entries = st.session_state.get("entries")

    if entries is not None:
        if not entries:
            st.error("❌ Aucune vidéo trouvée.")
        else:
            st.success(f"✅ {len(entries)} vidéo(s) trouvée(s).")

            with st.expander(f"📋 Liste des vidéos ({len(entries)})"):
                for url, title in entries:
                    st.markdown(f"- **{title}** — `{url}`")

            # ── Étape 3 : Téléchargement ─────────────────────────────────────

            if st.button(
                f"⬇️ Télécharger les {len(entries)} audio(s)",
                key="btn_download_all",
            ):
                st.session_state.pop("download_result", None)

                dl_progress = st.progress(0)
                dl_status = st.empty()
                log_placeholder = st.empty()
                logs: list[str] = []
                # Stockage (nom_fichier, octets) en mémoire — pas de tmpdir inter-runs
                mp3_data: list[tuple[str, bytes]] = []
                total = len(entries)

                with tempfile.TemporaryDirectory() as tmpdir:
                    for idx, (url, title) in enumerate(entries, 1):
                        label = title or url[:60]
                        dl_status.text(f"⏳ {idx}/{total} — {label[:70]}…")
                        dl_progress.progress(idx / total)

                        success, _, mp3_path = download_single_audio(url, tmpdir)

                        if success and mp3_path and os.path.isfile(mp3_path):
                            with open(mp3_path, "rb") as f:
                                mp3_data.append((os.path.basename(mp3_path), f.read()))
                            logs.append(f"✅ {idx}/{total} — {label[:60]}")
                        else:
                            logs.append(f"❌ {idx}/{total} — {label[:60]} → {mp3_path}")

                        # Affiche les 10 dernières lignes de log en temps réel
                        log_placeholder.text("\n".join(logs[-10:]))

                dl_progress.empty()
                dl_status.empty()
                log_placeholder.empty()

                if not mp3_data:
                    st.error(
                        "❌ Aucun fichier MP3 créé. "
                        "Vérifiez que ffmpeg est bien installé (voir message en haut) "
                        "et que les URLs sont accessibles depuis ce serveur."
                    )
                    with st.expander("📋 Journal complet des erreurs"):
                        st.text("\n".join(logs))
                else:
                    if len(mp3_data) == 1:
                        st.session_state["download_result"] = {
                            "type": "single",
                            "data": mp3_data[0][1],
                            "filename": mp3_data[0][0],
                            "count": 1,
                            "total": total,
                            "logs": logs,
                        }
                    else:
                        buf = io.BytesIO()
                        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                            for fname, fbytes in mp3_data:
                                zf.writestr(fname, fbytes)
                        buf.seek(0)
                        st.session_state["download_result"] = {
                            "type": "zip",
                            "data": buf.read(),
                            "filename": "audios_senat.zip",
                            "count": len(mp3_data),
                            "total": total,
                            "logs": logs,
                        }

            # ── Étape 4 : Bouton de téléchargement final ─────────────────────

            result = st.session_state.get("download_result")
            if result:
                nb_echecs = result["total"] - result["count"]
                if nb_echecs > 0:
                    st.warning(f"⚠️ {nb_echecs} fichier(s) n'ont pas pu être téléchargés.")
                    with st.expander("📋 Voir le journal"):
                        st.text("\n".join(result.get("logs", [])))

                if result["type"] == "single":
                    st.success("✅ 1 fichier MP3 prêt.")
                    st.download_button(
                        label="💾 Télécharger le MP3",
                        data=result["data"],
                        file_name=result["filename"],
                        mime="audio/mpeg",
                        type="primary",
                    )
                elif result["type"] == "zip":
                    st.success(
                        f"✅ {result['count']} fichiers MP3 prêts "
                        f"(sur {result['total']} tentatives)."
                    )
                    st.download_button(
                        label=f"📦 Télécharger l'archive ZIP ({result['count']} MP3)",
                        data=result["data"],
                        file_name=result["filename"],
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
