import subprocess
import numpy as np
import whisper
from collections import deque
from threading import Thread, Lock
from flask import Flask, request, render_template_string, jsonify
import threading
import pika
 
app = Flask(__name__)
 
# Shared data structures with thread safety
live_transcriptions = deque(maxlen=10)
keyword_results = deque(maxlen=10)
lock = Lock()
KEYWORD = ""
 
def transcribe_audio(model, audio_np):
    """
    Transcribes audio data using Whisper.
    """
    try:
        result = model.transcribe(audio_np, fp16=False)
        return result['text'], result['segments']
    except Exception as e:
        print(f"Error during transcription: {e}")
        return "", []
 
def capture_and_transcribe_stream(ip_stream_url, keyword):
    """
    Captures audio from an IPTV stream and transcribes it while checking for a keyword.
    """
    print("Loading Whisper model...")
    try:
        model = whisper.load_model("base")
    except Exception as e:
        print(f"Error loading Whisper model: {e}")
        return
 
    # Start ffmpeg process
    ffmpeg_command = [
        "ffmpeg",
        "-i", ip_stream_url,
        "-f", "s16le",
        "-ac", "1",
        "-ar", "16000",
        "-acodec", "pcm_s16le",
        "-"
    ]
 
    process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10**7)
 
    print("Processing IPTV stream...")
    audio_buffer = b""
    chunk_size = 16000 * 2 * 5  # 5 seconds of audio for faster detection
    pre_keyword_buffer = deque(maxlen=16000 * 2 * 60)  # 1 minute buffer
 
    def transcribe_and_check():
        nonlocal audio_buffer
        while True:
            if audio_buffer:
                audio_np = np.frombuffer(audio_buffer, np.int16).astype(np.float32) / 32768.0
                if audio_np.size > 0:
                    text, segments = transcribe_audio(model, audio_np)
                    with lock:
                        live_transcriptions.append(ip_stream_url +": " + text)
                    print("Live Transcription:", text)
                    if keyword.lower() in text.lower():
                        print("Keyword detected!")
                        # Save 30 seconds before and continue capturing for 30 seconds after
                        post_keyword_buffer = b""
                        for _ in range(6):  # Approx 30 seconds after detection with 5-second chunks
                            post_chunk = process.stdout.read(chunk_size)
                            if not post_chunk:
                                break
                            post_keyword_buffer += post_chunk
 
                        # Combine pre and post buffers
                        full_audio = np.frombuffer(bytes(pre_keyword_buffer) + post_keyword_buffer, np.int16).astype(np.float32) / 32768.0
                        full_text, _ = transcribe_audio(model, full_audio)
 
                        with lock:
                            keyword_results.append(full_text)
                            print("Transcription (30 seconds before and after):", full_text)
 
                        pre_keyword_buffer.clear()
                audio_buffer = b""
 
    transcription_thread = Thread(target=transcribe_and_check, daemon=True)
    transcription_thread.start()
 
    try:
        while True:
            audio_chunk = process.stdout.read(chunk_size)
            if not audio_chunk:
                break
 
            audio_buffer += audio_chunk
            pre_keyword_buffer.extend(audio_chunk)
 
    except KeyboardInterrupt:
        print("Stopping transcription...")
    finally:
        process.terminate()
        process.wait()
        transcription_thread.join()

def callback(ch, method, properties, body):
    # Extract IPTV URL from the RabbitMQ message
    iptv_url = body.decode('utf-8')
    print(f"Received IPTV URL: {iptv_url}")

    # Create a new thread for each transcription task
    transcription_thread = threading.Thread(target=capture_and_transcribe_stream, args=(iptv_url,KEYWORD))
    transcription_thread.daemon = True  # Daemonize the thread to ensure it exits when the main program exits
    transcription_thread.start()

    # Acknowledge message processing
    ch.basic_ack(delivery_tag=method.delivery_tag)

# Main worker setup
def start_consumer():
    # Connect to RabbitMQ
    connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
    channel = connection.channel()

    # Declare the same queue
    channel.queue_declare(queue='transcription_tasks', durable=True)

    # Listen for messages
    channel.basic_qos(prefetch_count=1)  # Fair dispatch
    channel.basic_consume(queue='transcription_tasks', on_message_callback=callback)

    print("Waiting for transcription tasks...")
    channel.start_consuming()

# Start the consumer in a separate thread
def main():
    consumer_thread = threading.Thread(target=start_consumer)
    consumer_thread.daemon = True
    consumer_thread.start()

    # Start the Flask app
    app.run(host='0.0.0.0', port=5000)
    
@app.route('/', methods=['GET', 'POST'])
def index():
    keyword = ""
    global KEYWORD
    if request.method == 'POST':
        keyword = request.form['keyword']
        KEYWORD = keyword
        consumer_thread = threading.Thread(target=start_consumer)
        consumer_thread.daemon = True
        consumer_thread.start()
    return render_template_string('''
 <div id="chat-container">
    <form id="chat-form" method="post">
        <input type="text" id="keyword" name="keyword" placeholder="Type your keyword here..." required>
        <button type="submit">Search</button>
    </form>
</div>
<div id="transcription-result">
    <h3>Live Transcription</h3>
    <pre id="live-transcription"></pre>
    <h3>Keyword Results (30 seconds before and after)</h3>
    <pre id="keyword-results"></pre>
    <h3>Current Keyword</h3>
    <pre id="current-keyword">{{ keyword }}</pre>
</div>
 
<style>
    body {
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        background-color: #1e1e1e;
        color: #dcdcdc;
        margin: 0;
        padding: 0;
        line-height: 1.6;
    }
 
    #chat-container {
        display: flex;
        justify-content: center;
        align-items: center;
        width: 100%;
        max-width: 700px;
        margin: 30px auto;
        padding: 20px;
        border-radius: 10px;
        background-color: #2c2c2c;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
    }
 
    #chat-form {
        display: flex;
        width: 100%;
    }
 
    #chat-form input {
        flex: 1;
        padding: 10px 15px;
        border: 1px solid #444;
        border-radius: 10px 0 0 10px;
        outline: none;
        font-size: 16px;
        background-color: #3c3c3c;
        color: #dcdcdc;
        transition: border-color 0.3s;
    }
 
    #chat-form input:focus {
        border-color: #1e90ff;
    }
 
    #chat-form button {
        padding: 10px 20px;
        border: 1px solid #444;
        border-radius: 0 10px 10px 0;
        background-color: #1e90ff;
        color: white;
        cursor: pointer;
        font-size: 16px;
        font-weight: 600;
        transition: background-color 0.3s, transform 0.2s;
    }
 
    #chat-form button:hover {
        background-color: #1c86e0;
        transform: translateY(-2px);
    }
 
    #transcription-result {
        margin-top: 30px;
        width: 100%;
        padding: 20px;
        border-radius: 10px;
        border: 1px solid #444;
        background-color: #2c2c2c;
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.2);
    }
 
    h3 {
        margin-bottom: 15px;
        color: #dcdcdc;
        font-weight: 500;
    }
 
    pre {
        white-space: pre-wrap;
        word-wrap: break-word;
        color: #dcdcdc;
        font-family: 'Courier New', Courier, monospace;
        font-size: 14px;
        margin: 0;
    }
</style>
 
<script>
    function fetchLiveTranscription() {
        fetch('/live_transcription').then(response => response.json()).then(data => {
            document.getElementById('live-transcription').innerHTML = data.transcriptions.join('<br>');
        });
    }
 
    function fetchKeywordResults() {
        fetch('/keyword_results').then(response => response.json()).then(data => {
            document.getElementById('keyword-results').innerHTML = data.results.join('<br>');
        });
    }
 
    setInterval(fetchLiveTranscription, 1000);
    setInterval(fetchKeywordResults, 1000);
</script>
    ''', keyword=KEYWORD)
 
@app.route('/live_transcription')
def get_live_transcription():
    with lock:
        return jsonify({"transcriptions": list(live_transcriptions)})
 
@app.route('/keyword_results')
def keyword_results_view():
    with lock:
        return jsonify({"results": list(keyword_results)})
 
if __name__ == "__main__":
    app.run(debug=True, threaded=True)