import base64
import os
import uuid
import asyncio
import logging
import time
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
import yt_dlp
import ffmpeg

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "https://hqffhk-1j.myshopify.com"}})

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Temporary audio directory
TEMP_DIR = "temp_audio"
os.makedirs(TEMP_DIR, exist_ok=True)

def load_cookies():
    cookies_b64 = os.getenv('YOUTUBE_COOKIES')
    if not cookies_b64:
        app.logger.error("YOUTUBE_COOKIES environment variable not set")
        return
    try:
        with open('youtube_cookies.txt', 'wb') as f:
            f.write(base64.b64decode(cookies_b64))
        # Verify cookie file content
        with open('youtube_cookies.txt', 'r') as f:
            content = f.read()
            if not content.startswith('# Netscape HTTP Cookie File'):
                app.logger.error("Invalid cookie file format")
                return
        app.logger.info("Cookies loaded successfully")
    except Exception as e:
        app.logger.error(f"Failed to load cookies: {e}")

load_cookies()

async def pitch_shift_audio(input_path, output_path, timeout=60):
    try:
        app.logger.debug(f"Applying pitch shift to {input_path}")
        stream = ffmpeg.input(input_path)
        stream = ffmpeg.filter(stream, 'asetrate', '44100*432/440')
        stream = ffmpeg.output(stream, output_path, format='mp3', acodec='mp3')
        process = await asyncio.create_subprocess_exec('ffmpeg', *stream.compile())
        await asyncio.wait_for(process.wait(), timeout=timeout)
        app.logger.debug(f"Saved converted audio to {output_path}")
    except asyncio.TimeoutError:
        app.logger.error("FFmpeg timed out")
        raise Exception("Pitch shifting timed out")
    except Exception as e:
        app.logger.error(f"FFmpeg error: {e}")
        raise

@app.route('/api/convert', methods=['POST'])
async def convert_audio():
    original_file = None
    converted_file = None
    try:
        app.logger.debug("Received convert request")
        data = request.get_json()
        youtube_url = data.get('youtubeUrl')
        if not youtube_url:
            app.logger.warning("No YouTube URL provided")
            return jsonify({'success': False, 'error': 'Please provide a YouTube URL'}), 400

        file_id = str(uuid.uuid4())
        original_file = os.path.join(TEMP_DIR, f"{file_id}_original.mp3")
        converted_file = os.path.join(TEMP_DIR, f"{file_id}_432hz.mp3")

        app.logger.debug(f"Downloading YouTube audio: {youtube_url}")
        start_time = time.time()
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': original_file.replace('.mp3', ''),
            'cookiefile': 'youtube_cookies.txt',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([youtube_url])
        except Exception as e:
            app.logger.error(f"yt-dlp error: {e}")
            return jsonify({'success': False, 'error': 'Failed to download audio. Try another URL or try again later.'}), 400

        if not os.path.exists(original_file):
            app.logger.warning("Downloaded file not found")
            return jsonify({'success': False, 'error': 'Failed to download audio'}), 400

        download_duration = time.time() - start_time
        app.logger.debug(f"Download completed in {download_duration:.2f} seconds")

        await pitch_shift_audio(original_file, converted_file)

        # Log file sizes
        original_size = os.path.getsize(original_file) / (1024 * 1024)  # MB
        converted_size = os.path.getsize(converted_file) / (1024 * 1024)  # MB
        app.logger.debug(f"Original file size: {original_size:.2f} MB, Converted file size: {converted_size:.2f} MB")

        base_url = request.url_root
        audio_url = f"{base_url}api/listen/{file_id}"
        download_url = f"{base_url}api/download/{file_id}"
        share_url = f"{base_url}api/download/{file_id}"

        app.logger.debug("Conversion successful")
        return jsonify({
            'success': True,
            'audioUrl': audio_url,
            'downloadUrl': download_url,
            'shareUrl': share_url,
            'fileId': file_id
        }), 200

    except Exception as e:
        app.logger.error(f"Error in convert_audio: {e}")
        return jsonify({'success': False, 'error': f'Conversion failed: {str(e)}'}), 500

@app.route('/api/listen/<file_id>', methods=['GET'])
def listen_audio(file_id):
    try:
        file_path = os.path.join(TEMP_DIR, f"{file_id}_432hz.mp3")
        if not os.path.exists(file_path):
            app.logger.warning(f"File not found: {file_path}")
            return jsonify({'success': False, 'error': 'File not found'}), 404
        return send_file(file_path, mimetype='audio/mpeg', as_attachment=False)
    except Exception as e:
        app.logger.error(f"Error in listen_audio: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/download/<file_id>', methods=['GET'])
def download_audio(file_id):
    try:
        file_path = os.path.join(TEMP_DIR, f"{file_id}_432hz.mp3")
        if not os.path.exists(file_path):
            app.logger.warning(f"File not found: {file_path}")
            return jsonify({'success': False, 'error': 'File not found'}), 404
        return send_file(file_path, mimetype='audio/mpeg', as_attachment=True, download_name="converted_432hz.mp3")
    except Exception as e:
        app.logger.error(f"Error in download_audio: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/share/<file_id>', methods=['GET'])
def share_audio(file_id):
    try:
        file_path = os.path.join(TEMP_DIR, f"{file_id}_432hz.mp3")
        if not os.path.exists(file_path):
            app.logger.warning(f"File not found: {file_path}")
            return jsonify({'success': False, 'error': 'File not found'}), 404
        share_url = f"{request.url_root}api/download/{file_id}"
        return jsonify({'success': True, 'shareUrl': share_url}), 200
    except Exception as e:
        app.logger.error(f"Error in share_audio: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/cleanup/<file_id>', methods=['DELETE'])
def cleanup_audio(file_id):
    try:
        original_file = os.path.join(TEMP_DIR, f"{file_id}_original.mp3")
        converted_file = os.path.join(TEMP_DIR, f"{file_id}_432hz.mp3")
        for file_path in [original_file, converted_file]:
            if os.path.exists(file_path):
                os.remove(file_path)
                app.logger.debug(f"Deleted {file_path}")
        return jsonify({'success': True}), 200
    except Exception as e:
        app.logger.error(f"Error in cleanup_audio: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=3000)