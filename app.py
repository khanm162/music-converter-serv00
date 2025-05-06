import os
import uuid
import subprocess
import logging
import re
import time
import threading
import requests
from flask import Flask, request, send_file, jsonify, url_for
from werkzeug.utils import secure_filename
from flask_cors import CORS
from io import BytesIO
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

# Setup
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Use /tmp for temporary files on Render
TEMP_DIR = "/tmp/temp_audio"
os.makedirs(TEMP_DIR, exist_ok=True)

# Use the PORT environment variable for Render
port = int(os.getenv("PORT", 8080))

# Path to the cookies file on Render
COOKIES_FILE = "/etc/secrets/youtube_cookies.txt"

# Check if cookies file exists
if not os.path.exists(COOKIES_FILE):
    logger.error(f"Cookie file {COOKIES_FILE} not found")
    raise FileNotFoundError(f"Cookie file {COOKIES_FILE} not found")

# Log cookies file details
file_size = os.path.getsize(COOKIES_FILE)
logger.info(f"Cookies file size: {file_size} bytes")
with open(COOKIES_FILE, 'r') as f:
    lines = f.readlines()
    preview_lines = lines[:3] if len(lines) >= 3 else lines
    sanitized_preview = [line.strip() if line.startswith('#') else '<cookie line>' for line in preview_lines]
    logger.info(f"Cookies file preview (first 3 lines): {sanitized_preview}")

# Custom YoutubeDL class to prevent saving cookies
class NoSaveCookiesYDL(YoutubeDL):
    def save_cookies(self, *args, **kwargs):
        pass  # Prevent yt-dlp from trying to save cookies

# Timeout handler for operations
class TimeoutException(Exception):
    pass

def timeout_wrapper(func, timeout_duration, error_message):
    result = [None]
    exception = [None]
    
    def target():
        try:
            result[0] = func()
        except Exception as e:
            exception[0] = e
    
    thread = threading.Thread(target=target)
    thread.daemon = True
    thread.start()
    thread.join(timeout_duration)
    
    if thread.is_alive():
        raise TimeoutException(error_message)
    if exception[0]:
        raise exception[0]
    return result[0]

def validate_youtube_url(url):
    youtube_regex = r'^(https?:\/\/)?(www\.)?(youtube\.com|youtu\.be)\/.+$'
    return bool(re.match(youtube_regex, url))

def download_audio_from_youtube(url):
    try:
        logger.info(f"Attempting to download audio from URL: {url}")
        
        audio_id = f"audio_{uuid.uuid4().hex[:8]}"
        original_file_base = os.path.join(TEMP_DIR, f"{audio_id}_original")
        original_file_path = f"{original_file_base}.mp3"
        converted_file_path = os.path.join(TEMP_DIR, f"{audio_id}_432hz.mp3")
        
        # Step 1: Extract video info and audio URL
        user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
        ydl_opts_info = {
            'quiet': True,
            'cookiefile': COOKIES_FILE,
            'user_agent': user_agent,
            'format': 'bestaudio/best',
            'noplaylist': True,
            'socket_timeout': 10,
            'extractor_retries': 2,
            'no-cache-dir': True,
            'geturl': True,  # Only extract the direct URL
        }
        logger.debug(f"yt-dlp extract info options: {ydl_opts_info}")

        audio_url = None
        title = 'unknown_title'
        with NoSaveCookiesYDL(ydl_opts_info) as ydl:
            try:
                audio_url = timeout_wrapper(
                    lambda: ydl.extract_info(url, download=False),
                    12,  # Increased to 12 seconds
                    "Failed to extract video info due to timeout. Please try again later."
                )
                logger.info(f"Extracted audio URL: {audio_url}")
            except TimeoutException as e:
                # Fallback: Try minimal extraction
                logger.warning("Falling back to minimal extraction due to timeout")
                ydl_opts_minimal = {
                    'quiet': True,
                    'cookiefile': COOKIES_FILE,
                    'user_agent': user_agent,
                    'format': 'bestaudio/best',
                    'noplaylist': True,
                    'socket_timeout': 5,
                    'extractor_retries': 1,
                    'no-cache-dir': True,
                    'geturl': True,
                }
                audio_url = timeout_wrapper(
                    lambda: ydl.extract_info(url, download=False),
                    5,
                    "Failed to extract video info even with minimal settings. Please try again later."
                )
                logger.info(f"Extracted audio URL with minimal settings: {audio_url}")

        # Try to get the title separately
        try:
            ydl_opts_title = {
                'quiet': True,
                'cookiefile': COOKIES_FILE,
                'user_agent': user_agent,
                'noplaylist': True,
                'socket_timeout': 5,
                'no-cache-dir': True,
            }
            with NoSaveCookiesYDL(ydl_opts_title) as ydl_title:
                info = ydl_title.extract_info(url, download=False)
                title = info.get('title', 'unknown_title')
                logger.info(f"Video title: {title}")
        except Exception as e:
            logger.warning(f"Failed to extract title: {str(e)}")

        sanitized_title = secure_filename(title)

        if not audio_url:
            return {"error": "Failed to extract audio URL from the video."}

        # Step 2: Download audio directly from the extracted URL
        logger.info(f"Downloading audio from extracted URL")
        response = requests.get(audio_url, headers={'User-Agent': user_agent}, stream=True, timeout=10)
        response.raise_for_status()

        with open(f"{original_file_base}.webm", 'wb') as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):  # 1 MB chunks
                if chunk:
                    f.write(chunk)

        # Step 3: Convert the downloaded file to MP3
        cmd_convert = [
            "ffmpeg", "-y", "-i", f"{original_file_base}.webm",
            "-c:a", "mp3", "-b:a", "128k",
            original_file_path
        ]
        logger.debug(f"Running FFmpeg command to convert to MP3: {' '.join(cmd_convert)}")
        subprocess.run(cmd_convert, check=True, capture_output=True, text=True)

        # Clean up the temporary webm file
        os.remove(f"{original_file_base}.webm")

        if not os.path.exists(original_file_path):
            logger.error(f"Converted file not found: {original_file_path}")
            raise Exception("Converted file not found")

        return {
            "audio_id": audio_id,
            "original_path": original_file_path,
            "converted_path": converted_file_path,
            "title": title,
            "sanitized_title": sanitized_title
        }

    except DownloadError as e:
        error_msg = str(e)
        logger.error(f"yt-dlp download error: {error_msg}")
        # Handle HTTP 403 Forbidden specifically
        if "http error 403" in error_msg.lower():
            return {"error": "Access denied (HTTP 403). The video may be restricted (e.g., region-locked or age-restricted)."}
        if "sign in to confirm" in error_msg.lower() or "bot" in error_msg.lower():
            return {"error": "This video cannot be downloaded. YouTube requires authentication to access it, and the provided cookies may be invalid or expired."}
        if "player response" in error_msg.lower():
            return {"error": "Unable to access this YouTube video. It may be restricted or unavailable."}
        return {"error": "Failed to download the video. Please try another URL."}
    except TimeoutException as e:
        logger.error(f"Timeout error: {str(e)}")
        return {"error": str(e)}
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading audio from URL: {str(e)}")
        return {"error": "Failed to download the audio stream. Please try again later."}
    except Exception as e:
        logger.error(f"Unexpected error downloading audio: {str(e)}")
        return {"error": "An unexpected error occurred while downloading the video."}

def convert_to_432hz(input_path, output_path):
    try:
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-af", "asetrate=44100*432/440,aresample=44100",
            output_path
        ]
        logger.debug(f"Running FFmpeg command: {' '.join(cmd)}")
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        logger.info(f"Conversion to 432Hz successful. Output saved at: {output_path}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg conversion error: {e.stderr}")
        return False

def cleanup_files(*file_paths):
    for file_path in file_paths:
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Deleted file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to delete file {file_path}: {e}")

@app.route("/api/convert", methods=["POST"])
def convert_audio():
    data = request.get_json()
    youtube_url = data.get("youtubeUrl")

    if not youtube_url or not validate_youtube_url(youtube_url):
        logger.error(f"Invalid or missing YouTube URL: {youtube_url}")
        return jsonify({"success": False, "error": "Invalid or missing YouTube URL"}), 400

    logger.info(f"Received request to convert YouTube URL: {youtube_url}")
    result = download_audio_from_youtube(youtube_url)
    if "error" in result:
        return jsonify({"success": False, "error": result["error"]}), 400
    if not result:
        return jsonify({"success": False, "error": "Failed to process YouTube video"}), 500

    if not convert_to_432hz(result["original_path"], result["converted_path"]):
        cleanup_files(result["original_path"])
        return jsonify({"success": False, "error": "Conversion to 432Hz failed"}), 500

    audio_id = result["audio_id"]
    sanitized_title = result["sanitized_title"]
    audio_url = url_for('stream_audio', audio_id=audio_id, _external=True)
    download_url = url_for('download_audio', audio_id=audio_id, _external=True)
    share_url = audio_url

    cleanup_files(result["original_path"])

    return jsonify({
        "success": True,
        "audioUrl": audio_url,
        "downloadUrl": download_url,
        "shareUrl": share_url,
        "title": result["title"],
        "sanitized_title": sanitized_title
    })

@app.route("/api/stream/<audio_id>", methods=["GET"])
def stream_audio(audio_id):
    audio_path = os.path.join(TEMP_DIR, f"{audio_id}_432hz.mp3")
    if not os.path.exists(audio_path):
        logger.error(f"Audio file not found: {audio_path}")
        return jsonify({"error": "Audio not found"}), 404

    with open(audio_path, 'rb') as f:
        return send_file(
            BytesIO(f.read()),
            mimetype="audio/mpeg",
            as_attachment=False
        )

@app.route("/api/download/<audio_id>", methods=["GET"])
def download_audio(audio_id):
    audio_path = os.path.join(TEMP_DIR, f"{audio_id}_432hz.mp3")
    if not os.path.exists(audio_path):
        logger.error(f"Audio file not found: {audio_path}")
        return jsonify({"error": "Audio not found"}), 404

    sanitized_title = request.args.get('title', 'converted_432hz')

    response = send_file(
        audio_path,
        mimetype="audio/mpeg",
        as_attachment=True,
        download_name=f"{sanitized_title}.mp3"
    )
    cleanup_files(audio_path)
    return response

@app.route("/api/info", methods=["POST"])
def get_info():
    data = request.get_json()
    youtube_url = data.get("youtubeUrl")

    if not youtube_url or not validate_youtube_url(youtube_url):
        logger.error(f"Invalid or missing YouTube URL: {youtube_url}")
        return jsonify({"error": "Invalid or missing YouTube URL"}), 400

    try:
        user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
        ydl_opts = {
            'quiet': True,
            'cookiefile': COOKIES_FILE,
            'user_agent': user_agent,
            'socket_timeout': 5,
            'no-cache-dir': True,
        }
        with NoSaveCookiesYDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            return jsonify({
                "title": info.get('title', 'unknown_title')
            })
    except Exception as e:
        logger.error(f"Error fetching video info: {e}")
        return jsonify({"error": "Invalid YouTube URL"}), 500

@app.teardown_request
def cleanup_temp_files(exception=None):
    for filename in os.listdir(TEMP_DIR):
        file_path = os.path.join(TEMP_DIR, filename)
        if os.path.isfile(file_path):
            try:
                if os.path.getmtime(file_path) < time.time() - 3600:
                    os.remove(file_path)
                    logger.info(f"Deleted old file: {file_path}")
            except Exception as e:
                logger.warning(f"Failed to delete old file {file_path}: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=port)
