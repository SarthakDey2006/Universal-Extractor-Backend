from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
import os
import uuid
import json
import logging
import asyncio
import urllib.request
from fastapi.responses import FileResponse
import google.generativeai as genai

# --- SECURITY & LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI()

# Configure CORS (Matches your Vercel URL exactly)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", 
        "http://127.0.0.1:5173",
        "https://universal-extractor-ui.vercel.app"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Gemini AI
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_KEY_HERE")
genai.configure(api_key=GEMINI_API_KEY)

# --- ENTERPRISE SECURITY GUARDS ---
MAX_DURATION_SECONDS = 14400  # 4 Hours maximum video length
MAX_CONCURRENT_DOWNLOADS = 3  # Prevent server crashing
active_downloads = 0

# --- STATE MANAGEMENT & QUEUE ---
download_progress = {}
video_to_task = {} 

TEMP_DIR = "temp_downloads"
os.makedirs(TEMP_DIR, exist_ok=True)

class VideoRequest(BaseModel):
    url: str

class AIRequest(BaseModel):
    video_details: dict

def is_youtube_url(url: str) -> bool:
    return 'youtube.com' in url.lower() or 'youtu.be' in url.lower()

def calculate_sizes(info):
    sizes = {}
    formats = info.get('formats', [])
    
    audio_formats = [f for f in formats if f.get('acodec') != 'none' and f.get('vcodec') == 'none']
    if audio_formats:
        best_audio = sorted(audio_formats, key=lambda x: x.get('abr', 0) or 0, reverse=True)[0]
        sizes['audio_only'] = best_audio.get('filesize') or best_audio.get('filesize_approx') or 0

    video_formats = [f for f in formats if f.get('vcodec') != 'none' and f.get('acodec') != 'none']
    if not video_formats:
        video_formats = [f for f in formats if f.get('vcodec') != 'none']

    if video_formats:
        best_video = sorted(video_formats, key=lambda x: (x.get('height', 0) or 0), reverse=True)[0]
        sizes['highest_video'] = best_video.get('filesize') or best_video.get('filesize_approx') or 0
        if best_video.get('acodec') == 'none' and sizes.get('audio_only'):
            sizes['highest_video'] += sizes['audio_only']

    for key in sizes:
        if sizes[key]:
            sizes[key] = f"{sizes[key] / (1024 * 1024):.2f} MB"
        else:
            sizes[key] = "Unknown"
            
    return sizes

@app.post("/api/info")
async def get_info(req: VideoRequest, request: Request):
    logger.info(f"Info request for {req.url} from IP: {request.client.host}")
    
    ydl_opts = {
        'quiet': True, 
        'no_warnings': True, 
        'extract_flat': False,
        'http_headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'}
    }
    
    # Use cookies if available to prevent generic blocks
    if os.path.exists('cookies.txt'):
        ydl_opts['cookiefile'] = 'cookies.txt'
    
    try:
        info = await asyncio.to_thread(lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(req.url, download=False))
        
        # GUARD: Max Duration Check
        duration = info.get('duration', 0)
        if duration > MAX_DURATION_SECONDS:
            raise HTTPException(status_code=400, detail=f"Video is too long. Max duration is {MAX_DURATION_SECONDS/3600} hours.")

        return {
            "title": info.get('title', 'Unknown Title'),
            "thumbnail": info.get('thumbnail', ''),
            "duration": info.get('duration_string', 'Unknown'),
            "uploader": info.get('uploader', 'Unknown'),
            "platform": info.get('extractor', 'Unknown'),
            "sizes": calculate_sizes(info)
        }
    except Exception as e:
        # COBALT FALLBACK: If yt-dlp gets IP banned by YouTube, provide dummy info to allow the download to proceed!
        if is_youtube_url(req.url):
            logger.warning("YouTube info extraction blocked. Using Cobalt API fallback.")
            return {
                "title": "YouTube Video (Cobalt Routing)",
                "thumbnail": "https://www.youtube.com/img/desktop/yt_1200.png",
                "duration": "Unknown",
                "uploader": "YouTube Creator",
                "platform": "youtube",
                "sizes": {"highest_video": "Ready to Download", "audio_only": "Ready"}
            }
        logger.error(f"Failed to extract info: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

def download_video_task(url: str, task_id: str):
    global active_downloads
    
    # 1. YOUTUBE FALLBACK ROUTE (Using Cobalt API)
    if is_youtube_url(url):
        logger.info(f"Using Cobalt API Bypass for YouTube: {url}")
        temp_filepath = os.path.join(TEMP_DIR, f"{task_id}.mp4")
        try:
            # Ask Cobalt for the direct video link
            cobalt_req = urllib.request.Request(
                'https://api.cobalt.tools/api/json',
                data=json.dumps({"url": url}).encode('utf-8'),
                headers={
                    'Accept': 'application/json', 
                    'Content-Type': 'application/json',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
                }
            )
            with urllib.request.urlopen(cobalt_req) as response:
                data = json.loads(response.read().decode('utf-8'))
                if data.get('status') == 'error':
                    raise Exception(data.get('text', 'Cobalt API Error'))
                download_url = data['url']
            
            # Stream the video from Cobalt to Render server safely
            download_progress[task_id] = {"status": "downloading", "percent": 0}
            dl_req = urllib.request.Request(download_url, headers={'User-Agent': 'Mozilla/5.0'})
            
            with urllib.request.urlopen(dl_req) as response:
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                with open(temp_filepath, 'wb') as f:
                    while True:
                        chunk = response.read(16384) # Download in 16KB chunks
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = (downloaded / total_size) * 100
                            download_progress[task_id] = {"status": "downloading", "percent": round(percent, 2)}
            
            download_progress[task_id] = {"status": "completed", "percent": 100, "file": temp_filepath}
            
        except Exception as e:
            logger.error(f"Cobalt download failed for {task_id}: {str(e)}")
            download_progress[task_id] = {"status": "error", "message": str(e)}
        finally:
            active_downloads -= 1
        return # Exit the function here for YouTube

    # 2. STANDARD YT-DLP ROUTE (For Instagram, TikTok, X)
    temp_filepath = os.path.join(TEMP_DIR, f"{task_id}.%(ext)s")
    
    def progress_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                percent = (downloaded / total) * 100
                download_progress[task_id] = {"status": "downloading", "percent": round(percent, 2)}
        elif d['status'] == 'finished':
            download_progress[task_id] = {"status": "processing", "percent": 100}

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': temp_filepath,
        'restrictfilenames': True,
        'merge_output_format': 'mp4',
        'progress_hooks': [progress_hook],
        'noplaylist': True,
        'http_headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'}
    }
    
    if os.path.exists('cookies.txt'):
        ydl_opts['cookiefile'] = 'cookies.txt'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        downloaded_files = [f for f in os.listdir(TEMP_DIR) if f.startswith(task_id)]
        if downloaded_files:
            final_file = downloaded_files[0]
            download_progress[task_id] = {
                "status": "completed", 
                "percent": 100, 
                "file": os.path.join(TEMP_DIR, final_file)
            }
        else:
            download_progress[task_id] = {"status": "error", "message": "File not found after processing."}
            
    except Exception as e:
        logger.error(f"Download failed for {task_id}: {str(e)}")
        download_progress[task_id] = {"status": "error", "message": str(e)}
    finally:
        active_downloads -= 1

@app.post("/api/download")
async def start_download(req: VideoRequest, background_tasks: BackgroundTasks):
    global active_downloads
    
    # GUARD: Concurrent Rate Limiter
    if active_downloads >= MAX_CONCURRENT_DOWNLOADS:
        raise HTTPException(status_code=429, detail="Server is currently at maximum capacity. Please try again in a few minutes.")
        
    active_downloads += 1
    task_id = str(uuid.uuid4())
    download_progress[task_id] = {"status": "starting", "percent": 0}
    
    # QUEUE: Run the heavy download in the background
    background_tasks.add_task(download_video_task, req.url, task_id)
    return {"task_id": task_id}

@app.get("/api/progress/{task_id}")
async def get_progress(task_id: str):
    progress = download_progress.get(task_id)
    if not progress:
        return {"status": "not_found"}
    return progress

@app.get("/api/file/{task_id}")
async def get_file(task_id: str):
    progress = download_progress.get(task_id)
    if not progress or progress["status"] != "completed":
        raise HTTPException(status_code=400, detail="File not ready")
        
    file_path = progress["file"]
    return FileResponse(
        file_path, 
        media_type='application/octet-stream', 
        filename=os.path.basename(file_path),
        background=BackgroundTasks([lambda: os.remove(file_path)]) # Auto-cleanup
    )

@app.post("/api/analyze")
async def analyze_content(req: AIRequest):
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"""
        Act as an expert social media manager. I am giving you the metadata of a video I just downloaded. 
        Generate a viral, highly-engaging caption and a list of 10 relevant hashtags for me to use when I repost this.

        Video Details:
        Title: {req.video_details.get('title')}
        Platform: {req.video_details.get('platform')}
        Uploader: {req.video_details.get('uploader')}

        Respond ONLY with a JSON object in this exact format, with no markdown formatting around it:
        {{
            "caption": "Your viral caption here",
            "hashtags": "#tag1 #tag2 #tag3"
        }}
        """
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        
        if raw_text.startswith('```json'):
            raw_text = raw_text[7:-3]
        elif raw_text.startswith('```'):
            raw_text = raw_text[3:-3]
            
        result = json.loads(raw_text)
        return result
    except Exception as e:
        logger.error(f"AI Analysis failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # Dynamic port setup for Render compatibility
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
