import uuid
import os
import shutil
import requests
import logging
from flask import Flask, request, jsonify, send_from_directory, make_response
from werkzeug.utils import secure_filename
import yt_dlp
from pydub import AudioSegment
from flask_cors import CORS
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, error

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "https://hqffhk-1j.myshopify.com"}})

# Configure upload and output directories
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'output'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# yt-dlp options to download audio with cookies
ydl_opts = {
    'format': 'bestaudio/best',
    'outtmpl': os.path.join(UPLOAD_FOLDER, '%(id)s.%(ext)s'),
    'cookiefile': 'youtube_cookies.txt',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '96',
    }],
    'socket_timeout': 30,
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'retries': 5,
    'quiet': False,
    'no_warnings': False,
    'ignoreerrors': False,
    'format_sort': ['hasaud'],
    'ffmpeg_args': ['-bufsize', '500k'],
}

def sanitize_filename(filename):
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '')
    return filename.replace(' ', '_')

def download_thumbnail(thumbnail_url, output_path):
    logger.debug(f"Downloading thumbnail from: {thumbnail_url}")
    try:
        response = requests.get(thumbnail_url, stream=True)
        if response.status_code == 200:
            with open(output_path, 'wb') as f:
                shutil.copyfileobj(response.raw, f)
            logger.debug(f"Thumbnail downloaded to {output_path}, size: {os.path.getsize(output_path)} bytes")
            return True
        else:
            logger.error(f"Failed to download thumbnail, status code: {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Error downloading thumbnail: {e}")
        return False

def embed_thumbnail_in_mp3(mp3_path, thumbnail_path):
    logger.debug(f"Embedding thumbnail into MP3: {mp3_path}")
    try:
        audio = MP3(mp3_path, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        with open(thumbnail_path, 'rb') as f:
            image_data = f.read()
        audio.tags.add(
            APIC(
                encoding=3,
                mime='image/jpeg',
                type=3,
                desc='Cover',
                data=image_data
            )
        )
        audio.save()
        logger.debug("Thumbnail embedded successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to embed thumbnail: {e}")
        return False

def convert_to_432hz(input_path, output_path):
    logger.debug(f"Converting audio to 432Hz: {input_path} -> {output_path}")
    try:
        audio = AudioSegment.from_file(input_path, format="mp3")
        logger.debug(f"Input audio loaded, duration: {audio.duration_seconds} seconds")
        sample_rate = audio.frame_rate
        target_rate = int(sample_rate * (432 / 440))
        audio = audio.set_frame_rate(target_rate)
        audio.export(output_path, format="mp3", bitrate="96k")
        logger.debug(f"Audio converted, output size: {os.path.getsize(output_path)} bytes")
    except Exception as e:
        logger.error(f"Error during 432Hz conversion: {e}")
        raise

@app.route('/api/convert', methods=['POST'])
def convert_audio():
    data = request.get_json()
    if not data or 'youtubeUrl' not in data:
        return jsonify({"error": "Missing youtubeUrl in request"}), 400

    youtube_url = data['youtubeUrl']
    logger.debug(f"Received request to convert: {youtube_url}")
    try:
        # Download audio and extract metadata using yt-dlp
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=True)
            video_id = info['id']
            video_title = info.get('title', 'converted-audio')
            sanitized_title = sanitize_filename(video_title)
            input_file = os.path.join(UPLOAD_FOLDER, f"{video_id}.mp3")
            thumbnail_url = info.get('thumbnail', '')
            logger.debug(f"yt-dlp extracted info - title: {video_title}, thumbnail: {thumbnail_url}")
            logger.debug(f"Input file size after yt-dlp: {os.path.getsize(input_file)} bytes")

        # Convert to 432Hz
        output_audio_filename = f"{uuid.uuid4()}.mp3"
        output_audio_path = os.path.join(OUTPUT_FOLDER, output_audio_filename)
        convert_to_432hz(input_file, output_audio_path)

        # Download thumbnail and embed it into the MP3
        if thumbnail_url:
            thumbnail_filename = f"{uuid.uuid4()}.jpg"
            thumbnail_path = os.path.join(OUTPUT_FOLDER, thumbnail_filename)
            if download_thumbnail(thumbnail_url, thumbnail_path):
                embed_thumbnail_in_mp3(output_audio_path, thumbnail_path)
            else:
                logger.warning("Thumbnail download failed, skipping embedding")

        # Generate download URL
        audio_download_url = f"https://{request.host}/output/{output_audio_filename}"
        logger.debug(f"Generated audio download URL: {audio_download_url}")

        return jsonify({
            "audioUrl": audio_download_url,
            "downloadUrl": audio_download_url,
            "downloadFilename": f"{sanitized_title}_432hz.mp3",
            "status": "success"
        }), 200

    except Exception as e:
        logger.error(f"Error in convert_audio: {e}")
        return jsonify({"error": str(e)}), 500

    finally:
        # Clean up
        if 'input_file' in locals() and os.path.exists(input_file):
            logger.debug(f"Cleaning up input file: {input_file}")
            os.remove(input_file)
        if 'thumbnail_path' in locals() and os.path.exists(thumbnail_path):
            logger.debug(f"Cleaning up thumbnail file: {thumbnail_path}")
            os.remove(thumbnail_path)

@app.route('/output/<filename>')
def serve_file(filename):
    file_path = os.path.join(OUTPUT_FOLDER, filename)
    logger.debug(f"Serving file: {file_path}, size: {os.path.getsize(file_path)} bytes")
    response = make_response(send_from_directory(OUTPUT_FOLDER, filename))
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.headers['Access-Control-Allow-Origin'] = 'https://hqffhk-1j.myshopify.com'
    return response

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)