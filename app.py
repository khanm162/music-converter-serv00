from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
import yt_dlp
import librosa
import soundfile as sf
import os
import uuid
import logging
import tempfile

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})  # Allow Shopify front end

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Folder for temporary audio files
TEMP_DIR = "temp_audio"
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

# Function to convert audio from 440Hz to 432Hz
def pitch_shift_audio(input_path, output_path):
    try:
        app.logger.debug(f"Loading audio from {input_path}")
        y, sr = librosa.load(input_path, sr=None)
        semitones = -0.3176665363342977  # 440Hz to 432Hz
        app.logger.debug("Applying pitch shift")
        y_shifted = librosa.effects.pitch_shift(y=y, sr=sr, n_steps=semitones)
        app.logger.debug(f"Saving converted audio to {output_path}")
        sf.write(output_path, y_shifted, sr)
    except Exception as e:
        app.logger.error(f"Error in pitch_shift_audio: {str(e)}")
        raise

# Convert endpoint using yt-dlp
@app.route('/api/convert', methods=['POST'])
def convert_audio():
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
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': original_file.replace('.mp3', ''),  # yt-dlp adds extension
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
            app.logger.error(f"yt-dlp error: {str(e)}")
            return jsonify({'success': False, 'error': 'Failed to download audio. Try another URL or try again later.'}), 400

        # Check if file was downloaded
        if not os.path.exists(original_file):
            app.logger.warning("Downloaded file not found")
            return jsonify({'success': False, 'error': 'Failed to download audio'}), 400

        app.logger.debug("Converting audio to 432Hz")
        pitch_shift_audio(original_file, converted_file)

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
        app.logger.error(f"Error in convert_audio: {str(e)}")
        return jsonify({'success': False, 'error': f'Conversion failed: {str(e)}'}), 500

# Listen endpoint
@app.route('/api/listen/<file_id>', methods=['GET'])
def listen_audio(file_id):
    try:
        file_path = os.path.join(TEMP_DIR, f"{file_id}_432hz.mp3")
        if not os.path.exists(file_path):
            app.logger.warning(f"File not found: {file_path}")
            return jsonify({'success': False, 'error': 'File not found'}), 404
        return send_file(file_path, mimetype='audio/mpeg', as_attachment=False)
    except Exception as e:
        app.logger.error(f"Error in listen_audio: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Download endpoint
@app.route('/api/download/<file_id>', methods=['GET'])
def download_audio(file_id):
    try:
        file_path = os.path.join(TEMP_DIR, f"{file_id}_432hz.mp3")
        if not os.path.exists(file_path):
            app.logger.warning(f"File not found: {file_path}")
            return jsonify({'success': False, 'error': 'File not found'}), 404
        return send_file(file_path, mimetype='audio/mpeg', as_attachment=True, download_name="converted_432hz.mp3")
    except Exception as e:
        app.logger.error(f"Error in download_audio: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Share endpoint
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
        app.logger.error(f"Error in share_audio: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=3000)
