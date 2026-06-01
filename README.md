🚀 Universal Video Extractor & AI Content SynthesizerA high-performance, full-stack web application that bypasses platform limits to extract high-quality video and audio from thousands of websites (YouTube, Instagram, X/Twitter, TikTok). It features a modern cyberpunk UI, real-time download tracking, and an integrated Google Gemini AI engine that instantly generates ready-to-post social media kits from extracted video metadata.✨ Key FeaturesUniversal Support: Securely extract media from thousands of online platforms.Dynamic Processing: Select between 1080p, 720p, 480p, or Audio-Only (MP3). Calculates true file sizes dynamically before downloading.Turbo-Threaded Engine: Bypasses platform download throttling by multiplexing concurrent fragment downloads (acts like aria2c but keeps progress tracking intact).Real-Time Telemetry: Live progress bar, exact percentage tracking, and download speed visualization using RESTful polling.AI Social Media Kit: Integrates the Gemini 2.5 Flash API to analyze video metadata and instantly generate engaging summaries, viral captions, and intelligent hashtags.Cyberpunk UI: Custom fluid dot-matrix background, responsive glassmorphism bento-box layouts, and elite typography.Production-Hardened Backend: Includes strict URL payload validation, background disk cleanup to prevent storage DoS, and automated RAM flushing to prevent memory leaks.🛠️ Tech StackFrontend (Client)React 18 (Vite Build Engine)Tailwind CSS (Styling)RESTful API PollingBackend (Server)Python 3.10+FastAPI & Uvicorn (Async Web Framework)yt-dlp (Extraction Engine)FFmpeg (Stream Merging Engine)💻 Local Development SetupTo run this application on your local machine, you will need to run the Backend and Frontend simultaneously.PrerequisitesPython 3.10+ installed.Node.js (npm) installed.FFmpeg installed and added to your System PATH (Crucial for merging 1080p video with audio).Step 1: Start the Backend (Terminal 1)# 1. Clone the repository and navigate to the backend folder
cd backend-folder-name

# 2. Create and activate a virtual environment
python -m venv venv
# Windows: venv\Scripts\activate
# Mac/Linux: source venv/bin/activate

# 3. Install Python dependencies
pip install fastapi uvicorn pydantic yt-dlp requests

# 4. Set your Google Gemini API Key (Windows CMD example)
set GEMINI_API_KEY=your_actual_api_key_here

# 5. Run the server
python backend.py
The API will start running on http://127.0.0.1:8000Step 2: Start the Frontend (Terminal 2)# 1. Navigate to the frontend UI folder
cd universal-extractor-ui

# 2. Install Node dependencies
npm install

# 3. Start the Vite development server
npm run dev
The UI will load at http://localhost:5173🌐 Production DeploymentThis application is architected to be deployed on modern cloud infrastructure.Backend (Render.com): Use the provided Dockerfile to deploy the backend as a Web Service. Ensure you add GEMINI_API_KEY to the Render Environment Variables.Frontend (Vercel.com): Update the API_BASE variable in App.jsx to point to your new Render backend URL. Deploy the Vite frontend seamlessly via Vercel's GitHub integration.CORS Configuration: Once your Vercel frontend is live, update the allow_origins array in your backend.py file to include your Vercel domain to secure the connection.⚖️ DisclaimerThis project is built strictly for educational computer science purposes to demonstrate API architecture, background task processing, and modern UI design.Users are responsible for ensuring they have the legal right to download the content they process. Do not use this tool to bypass DRM, redistribute copyrighted material, or violate the Terms of Service of content platforms.Crafted by Sarthak Dey
