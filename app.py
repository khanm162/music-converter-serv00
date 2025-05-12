import uuid
import os
import shutil
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
import yt_dlp
from pydub import AudioSegment

app = Flask(__name__)

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
        'preferredquality': '192',
    }],
    'socket_timeout': 30,  # Add timeout to prevent hanging
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
}

def convert_to_432hz(input_path, output_path):
    audio = AudioSegment.from_file(input_path)
    sample_rate = audio.frame_rate
    target_rate = int(sample_rate * (432 / 440))
    audio = audio.set_frame_rate(target_rate)
    audio.export(output_path, format="mp3")

@app.route('/api/convert', methods=['POST'])
def convert_audio():
    data = request.get_json()
    if not data or 'youtubeUrl' not in data:
        return jsonify({"error": "Missing youtubeUrl in request"}), 400

    youtube_url = data['youtubeUrl']
    try:
        # Download audio using yt-dlp
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=True)
            video_id = info['id']
            input_file = os.path.join(UPLOAD_FOLDER, f"{video_id}.mp3")

        # Convert to 432Hz
        output_filename = f"{uuid.uuid4()}.mp3"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        convert_to_432hz(input_file, output_path)

        # Generate download URL
        download_url = f"https://{request.host}/output/{output_filename}"
        return jsonify({
            "audioUrl": download_url,
            "downloadUrl": download_url,
            "status": "success"
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        # Clean up
        if 'input_file' in locals() and os.path.exists(input_file):
            os.remove(input_file)

@app.route('/output/<filename>')
def serve_file(filename):
    return send_from_directory(OUTPUT_FOLDER, filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)