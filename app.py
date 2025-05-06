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

# Use /tmp for temporary files (Fly.io has a writable /tmp directory)
TEMP_DIR = "/tmp/temp_audio"
os.makedirs(TEMP_DIR, exist_ok=True)

# Use the PORT environment variable for Fly.io
port = int(os.getenv("PORT", 8080))

# Path to the cookies file (we'll set this as an environment variable on Fly.io)
COOKIES_FILE = os.getenv("COOKIES_FILE_PATH", "/app/youtube_cookies.txt")

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

def extract_audio_url_manually(url):
    user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
    headers = {'User-Agent': user_agent}
    
    # Step 1: Fetch the webpage
    logger.info("Fetching YouTube webpage manually")
    response = requests.get(url, headers=headers, timeout=20)  # Increased timeout for Fly.io
    response.raise_for_status()
    webpage = response.text
    
    # Step 2: Extract ytInitialData
    yt_initial_data_match = re.search(r'ytInitialData\s*=\s*({.*?});', webpage, re.DOTALL)
    if not yt_initial_data_match:
        raise Exception("Could not find ytInitialData in webpage")
    
    # Step 3: Extract audio URL (simplified approach)
    audio_url_match = re.search(r'"url":"(https:\/\/[^"]+\.googlevideo\.com\/[^"]+)"', webpage)
    if not audio_url_match:
        raise Exception("Could not find audio URL in webpage")
    
    audio_url = audio_url_match.group(1)
    logger.info(f"Manually extracted audio URL: {audio_url}")
    return audio_url

def extract_title(url):
    user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
    ydl_opts = {
        'quiet': True,
        'cookiefile': COOKIES_FILE,
        'user_agent': user_agent,
        'noplaylist': True,
        'socket_timeout': 5,
        'no-cache-dir': True,
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get('title', 'unknown_title')
    except Exception as e:
        logger.warning(f"Failed to extract title: {str(e)}")
        return 'unknown_title'

def download_audio_from_youtube(url):
    try:
        logger.info(f"Attempting to download audio from URL: {url}")
        
        audio_id = f"audio_{uuid.uuid4().hex[:8]}"
        original_file_base = os.path.join(TEMP_DIR, f"{audio_id}_original")
        original_file_path = f"{original_file_base}.mp3"
        converted_file_path = os.path.join(TEMP_DIR, f"{audio_id}_432hz.mp3")
        
        # Step 1: Extract audio URL manually
        audio_url = timeout_wrapper(
            lambda: extract_audio_url_manually(url),
            20,  # Increased timeout for Fly.io
            "Failed to extract audio URL due to timeout. Please try again later."
        )

        # Step 2: Extract title
        title = extract_title(url)
        sanitized_title = secure_filename(title)
        logger.info(f"Video title: {title}")

        # Step 3: Download audio directly from the extracted URL
        user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
        logger.info(f"Downloading audio from extracted URL: {audio_url}")
        response = requests.get(audio_url, headers={'User-Agent': user_agent}, stream=True, timeout=20)  # Increased timeout
        response.raise_for_status()

        # Limit file size to 10 MB
        max_file_size = 10 * 1024 * 1024  # 10 MB
        downloaded_size = 0
        temp_file_path = f"{original_file_base}.webm"
        
        with open(temp_file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):  # 1 MB chunks
                if chunk:
                    downloaded_size += len(chunk)
                    if downloaded_size > max_file_size:
                        raise Exception("Audio file exceeds 10 MB limit")
                    f.write(chunk)

        # Step 4: Convert the downloaded file to MP3
        cmd_convert = [
            "ffmpeg", "-y", "-i", temp_file_path,
            "-c:a", "mp3", "-b:a", "128k",
            original_file_path
        ]
        logger.debug(f"Running FFmpeg command to convert to MP3: {' '.join(cmd_convert)}")
        subprocess.run(cmd_convert, check=True, capture_output=True, text=True)

        # Clean up the temporary webm file
        os.remove(temp_file_path)

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

    except TimeoutException as e:
        logger.error(f"Timeout error: {str(e)}")
        return {"error": str(e)}
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading audio from URL: {str(e)}")
        return {"error": "Failed to download the audio stream. Please try again later."}
    except Exception as e:
        logger.error(f"Unexpected error downloading audio: {str(e)}")
        if "exceeds 10 MB limit" in str(e):
            return {"error": "The audio file is too large to download. Please try a shorter video."}
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
        title = extract_title(youtube_url)
        return jsonify({
            "title": title
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