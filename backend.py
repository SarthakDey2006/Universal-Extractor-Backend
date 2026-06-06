import os
import re
import shutil
import time
import base64
import asyncio
import requests
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
import uvicorn
import yt_dlp
from contextlib import asynccontextmanager

# --- 1. SERVER CONFIGURATION & SECURITY ---
TEMP_DIR = os.path.join(os.getcwd(), "temp_downloads")
os.makedirs(TEMP_DIR, exist_ok=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
COOKIE_FILE = os.path.join(os.getcwd(), "cookies.txt")
# UPGRADE: Fetch Proxy URL from Environment Variables
PROXY_URL = os.getenv("PROXY_URL", "") 

# Global memory states
tasks_progress = {}
task_files = {} 
video_to_task = {} 

def check_dependencies():
    if not shutil.which("ffmpeg"):
        print("⚠️ WARNING: FFmpeg missing! High quality merging disabled.")
    else:
        print(f"✅ FFmpeg detected. Server ready.")

async def auto_sweeper():
    while True:
        try:
            current_time = time.time()
            for filename in os.listdir(TEMP_DIR):
                filepath = os.path.join(TEMP_DIR, filename)
                if os.path.isfile(filepath):
                    if current_time - os.path.getmtime(filepath) > 7200:
                        os.remove(filepath)
        except Exception:
            pass
        await asyncio.sleep(3600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Booting Universal Extractor...")
    check_dependencies()
    
    if PROXY_URL:
        print("🛡️ PROXY ROUTING ENABLED: Shielding server IP.")
    else:
        print("⚠️ NO PROXY DETECTED: Server IP is exposed to platforms.")

    cookie_b64 = os.getenv("YOUTUBE_COOKIES_BASE64", "")
    if cookie_b64:
        try:
            clean_b64 = cookie_b64.replace("-----BEGIN CERTIFICATE-----", "").replace("-----END CERTIFICATE-----", "")
            clean_b64 = re.sub(r'\s+', '', clean_b64)
            padding_needed = len(clean_b64) % 4
            if padding_needed > 0:
                clean_b64 += '=' * (4 - padding_needed)

            with open(COOKIE_FILE, "wb") as f:
                f.write(base64.b64decode(clean_b64))
            print("✅ YouTube Cookies loaded securely.")
        except Exception as e:
            print(f"⚠️ Failed to decode YOUTUBE_COOKIES_BASE64: {e}")

    sweeper_task = asyncio.create_task(auto_sweeper())
    yield
    sweeper_task.cancel()
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)

app = FastAPI(title="Universal Extractor API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 2. DATA MODELS ---
class VideoRequest(BaseModel): 
    url: str
    @field_validator('url')
    @classmethod
    def validate_url(cls, v):
        parsed = urlparse(v)
        if parsed.scheme not in ["http", "https"]:
            raise ValueError('Invalid protocol. URL must start with http or https.')
        return v

class DownloadRequest(VideoRequest): 
    quality: str = "best"
    
class AIRequest(BaseModel): 
    title: str; platform: str; uploader: str

# --- 3. HELPER FUNCTIONS ---
def cleanup_task_data(task_id: str, filepath: str):
    try:
        if os.path.exists(filepath): os.remove(filepath)
        if task_id in tasks_progress: del tasks_progress[task_id]
        if task_id in task_files: del task_files[task_id]
        keys_to_delete = [k for k, v in video_to_task.items() if v == task_id]
        for k in keys_to_delete: del video_to_task[k]
    except Exception: pass

def calculate_sizes(info):
    formats = info.get('formats', [])
    duration = info.get('duration', 0)
    sizes = {"1080p": "", "720p": "", "480p": "", "audio": ""}
    if not formats: return sizes

    def get_size(f):
        if f.get('filesize'): return f['filesize']
        if f.get('filesize_approx'): return f['filesize_approx']
        if f.get('tbr') and duration: return (f['tbr'] * 1000 * duration) / 8
        return 0

    audios = [f for f in formats if f.get('vcodec') == 'none' and f.get('acodec') != 'none']
    best_audio_size = get_size(sorted(audios, key=lambda x: x.get('tbr') or 0)[-1]) if audios else 0
    if best_audio_size > 0: sizes["audio"] = f" (~{best_audio_size / (1024*1024):.1f} MB)"

    for height in [1080, 720, 480]:
        videos = [f for f in formats if f.get('height') == height and f.get('vcodec') != 'none']
        if videos:
            best_video = sorted(videos, key=lambda x: x.get('tbr') or 0)[-1]
            total_size = get_size(best_video) + best_audio_size
            if total_size > 0: sizes[f"{height}p"] = f" (~{total_size / (1024*1024):.1f} MB)"
    return sizes

class QuietLogger:
    def debug(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): print(msg)

def strip_ansi(text):
    return re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])').sub('', text)

def progress_hook(d):
    raw_video_id = d.get('info_dict', {}).get('id')
    task_id = video_to_task.get(raw_video_id)
    if not task_id: return

    if d['status'] == 'downloading':
        try: percent = float(strip_ansi(d.get('_percent_str', '0%')).replace('%', '').strip())
        except: percent = 0.0
        tasks_progress[task_id] = {'status': 'downloading', 'progress': percent, 'speed': strip_ansi(d.get('_speed_str', 'N/A')).strip()}
    elif d['status'] == 'finished':
        tasks_progress[task_id] = {'status': 'processing', 'progress': 100, 'speed': 'Merging...'}

def get_base_ydl_opts():
    opts = {
        'quiet': True,
        'no_warnings': True,
        'source_address': '0.0.0.0', # Force IPv4
        'extractor_args': {
            'youtube': {
                'player_client': ['default', 'ios', 'android', 'web'] 
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        },
        'sleep_requests': 1,
    }
    
    # UPGRADE: Inject proxy if it exists
    if PROXY_URL:
        opts['proxy'] = PROXY_URL
        
    if os.path.exists(COOKIE_FILE):
        opts['cookiefile'] = COOKIE_FILE
    return opts

# --- 4. API ENDPOINTS ---
@app.post("/api/info")
def get_video_info(req: VideoRequest):
    ydl_opts = get_base_ydl_opts()
    ydl_opts['extract_flat'] = False

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
            return {
                "title": info.get('title', 'Unknown Title'),
                "thumbnail": info.get('thumbnail', ''),
                "duration": info.get('duration_string', 'Unknown'),
                "uploader": info.get('uploader', 'Unknown'),
                "platform": info.get('extractor', 'Unknown'),
                "sizes": calculate_sizes(info)
            }
    except Exception as e:
        error_msg = str(e)
        if "Sign in to confirm" in error_msg or "403" in error_msg or "Video unavailable" in error_msg: 
            raise HTTPException(status_code=403, detail="BOT_BLOCKED")
        raise HTTPException(status_code=400, detail=error_msg)

@app.post("/api/download")
def download_video(req: DownloadRequest, background_tasks: BackgroundTasks):
    
    init_opts = get_base_ydl_opts()
    try:
        with yt_dlp.YoutubeDL(init_opts) as ydl:
            info = ydl.extract_info(req.url, download=False)
            raw_video_id = info.get('id', 'task')
            task_id = f"{raw_video_id}_{int(time.time())}" 
            video_to_task[raw_video_id] = task_id
            tasks_progress[task_id] = {'status': 'starting', 'progress': 0, 'speed': 'Initializing...'}
    except Exception as e:
        error_msg = str(e)
        if "Sign in to confirm" in error_msg or "403" in error_msg or "Video unavailable" in error_msg: 
            raise HTTPException(status_code=403, detail="BOT_BLOCKED")
        raise HTTPException(status_code=400, detail=error_msg)

    format_str = 'bestvideo+bestaudio/best'
    postprocessors = []

    if req.quality == "1080p": format_str = 'bestvideo[height<=1080]+bestaudio/best'
    elif req.quality == "720p": format_str = 'bestvideo[height<=720]+bestaudio/best'
    elif req.quality == "480p": format_str = 'bestvideo[height<=480]+bestaudio/best'
    elif req.quality == "audio":
        format_str = 'bestaudio/best'
        postprocessors = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]

    ydl_opts = get_base_ydl_opts()
    ydl_opts.update({
        'format': format_str,
        'merge_output_format': 'mp4' if req.quality != "audio" else None,
        'outtmpl': os.path.join(TEMP_DIR, f'{task_id}.%(ext)s'),
        'logger': QuietLogger(),
        'progress_hooks': [progress_hook],
        'restrictfilenames': True,
    })
    if postprocessors: ydl_opts['postprocessors'] = postprocessors

    def run_download():
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(req.url, download=True)
                final_filename = ydl.prepare_filename(info)
                if req.quality == "audio": final_filename = final_filename.rsplit('.', 1)[0] + '.mp3'
                task_files[task_id] = final_filename
                tasks_progress[task_id] = {'status': 'finished', 'progress': 100, 'speed': 'Complete'}
        except Exception as e:
            tasks_progress[task_id] = {'status': 'error', 'progress': 0, 'speed': 'Error'}
            print(f"Background download error: {e}")

    background_tasks.add_task(run_download)
    return {"status": "started", "task_id": task_id}

@app.get("/api/progress/{task_id}")
def get_progress(task_id: str):
    return tasks_progress.get(task_id, {"status": "unknown"})

@app.get("/api/retrieve/{task_id}")
def retrieve_file(task_id: str, background_tasks: BackgroundTasks):
    filepath = task_files.get(task_id)
    if filepath and os.path.exists(filepath):
        background_tasks.add_task(cleanup_task_data, task_id, filepath)
        return FileResponse(path=filepath, filename=f"Extracted_{os.path.basename(filepath)}", media_type='application/octet-stream')
    raise HTTPException(status_code=404, detail="File not found.")

@app.post("/api/ai-kit")
def generate_ai_kit(req: AIRequest):
    if not GEMINI_API_KEY: raise HTTPException(status_code=500, detail="Server missing AI API Key.")
    prompt = f"Analyze this video metadata: Title: '{req.title}', Platform: {req.platform}, Uploader: {req.uploader}. Generate a short engaging summary, 5 relevant hashtags, and a viral social media caption to reshare this video."
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json", "responseSchema": {"type": "OBJECT", "properties": {"summary": {"type": "STRING"}, "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}}, "viralCaption": {"type": "STRING"}}}} }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        candidates = response.json().get("candidates")
        if not candidates: raise ValueError("No content generated")
        return candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Generation Failed: {str(e)}")

if __name__ == "__main__":
    uvicorn.run("backend:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=False)
