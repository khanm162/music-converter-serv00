import os
import uuid
import subprocess
import logging
import re
import time
from flask import Flask, request, send_file, jsonify, url_for
from werkzeug.utils import secure_filename
from flask_cors import CORS
from io import BytesIO
import yt_dlp

# Setup
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Use /tmp for temporary files on Render
TEMP_DIR = "/tmp/temp_audio"
os.makedirs(TEMP_DIR, exist_ok=True)

# Use the PORT environment variable for Render
port = int(os.getenv("PORT", 8080))

# Path to the cookies file on Render
COOKIES_FILE = "/etc/secrets/youtube_cookies.txt"

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
        
        # Check if cookies file exists and log details
        if not os.path.exists(COOKIES_FILE):
            logger.warning(f"Cookies file not found at {COOKIES_FILE}. Proceeding without cookies.")
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': original_file_base,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'http_headers': {
                    'Referer': 'https://www.youtube.com/',
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.9',
                },
            }
        else:
            logger.info(f"Cookies file found at: {COOKIES_FILE}")
            # Log file size
            file_size = os.path.getsize(COOKIES_FILE)
            logger.info(f"Cookies file size: {file_size} bytes")
            # Log first few lines (avoid logging sensitive data)
            with open(COOKIES_FILE, 'r') as f:
                lines = f.readlines()
                preview_lines = lines[:3] if len(lines) >= 3 else lines
                sanitized_preview = [line.strip() if line.startswith('#') else '<cookie line>' for line in preview_lines]
                logger.info(f"Cookies file preview (first 3 lines): {sanitized_preview}")
            
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': original_file_base,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'cookies': COOKIES_FILE,
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'http_headers': {
                    'Referer': 'https://www.youtube.com/',
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.9',
                },
            }

        logger.info(f"yt-dlp options: {ydl_opts}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            logger.info("Starting download with yt-dlp...")
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'unknown_title')
            sanitized_title = secure_filename(title)
            logger.info(f"Video title: {title}")

        if not os.path.exists(original_file_path):
            logger.error(f"Downloaded file not found: {original_file_path}")
            raise Exception("Downloaded file not found")

        logger.info(f"Download successful. File saved at: {original_file_path}")
        return {
            "audio_id": audio_id,
            "original_path": original_file_path,
            "converted_path": converted_file_path,
            "title": title,
            "sanitized_title": sanitized_title
        }

    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        logger.error(f"yt-dlp download error: {error_msg}")
        if "sign in to confirm" in error_msg.lower() or "bot" in error_msg.lower():
            return {"error": "This video cannot be downloaded. YouTube requires authentication to access it, and the provided cookies may be invalid or expired."}
        if "player response" in error_msg.lower():
            return {"error": "Unable to access this YouTube video. It may be restricted or unavailable."}
        return {"error": "Failed to download the video. Please try another URL."}
    except Exception as e:
        logger.error(f"Unexpected error downloading audio: {str(e)}")
        return {"error": "An unexpected error occurred while downloading the video."}

def convert_to_432hz(input_path, output_path):
    try:
        cmd = [
            "ffmpeg", "-i", input_path, "-af",
            "asetrate=44100*432/440,aresample=44100",
            output_path
        ]
        logger.info(f"Running FFmpeg command: {' '.join(cmd)}")
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
        return jsonify({"error": "Invalid or missing YouTube URL"}), 400

    try:
        ydl_opts = {'quiet': True, 'no_warnings': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
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
