import os
import uuid
import subprocess
import httpx
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI()

# Allow your Cloudflare Pages frontend to talk to this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # We will lock this down later
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs = {}

# These will be set later
VOICELY_API_KEY = os.getenv("VOICELY_API_KEY")
KAGGLE_MUSETALK_URL = os.getenv("KAGGLE_MUSETALK_URL")

def process_dub_job(job_id: str, video_path: str, target_lang: str):
    try:
        jobs[job_id]["status"] = "extracting_audio"
        audio_path = f"/tmp/{job_id}.wav"
        
        # 1. Extract Audio using FFmpeg
        subprocess.run(["ffmpeg", "-i", video_path, "-vn", "-acodec", "pcm_s16le", "-ar", "16000", audio_path], check=True)

        # (In the future, transcribe and translate here)
        jobs[job_id]["status"] = "transcribing"
        transcribed_text = "This is a sample transcription."
        
        jobs[job_id]["status"] = "translating"
        translated_text = "यह एक नमूना है।" if target_lang == "hindi" else "یہ ایک نمونہ ہے۔"

        jobs[job_id]["status"] = "generating_speech"
        tts_audio_path = f"/tmp/{job_id}_tts.mp3"
        
        # 2. Call Voicely for Text-to-Speech
        headers = {"xi-api-key": VOICELY_API_KEY, "Content-Type": "application/json"}
        payload = {
            "text": translated_text,
            "model_id": "voicely-flash-v1",
            "language_code": "hi" if target_lang == "hindi" else "ur"
        }
        
        # Note: If Voicely fails, we create a dummy audio file just so the pipeline doesn't break during testing
        try:
            with httpx.Client() as client:
                response = client.post("https://api.tryvoicely.com/v1/text-to-speech", json=payload, headers=headers)
                response.raise_for_status()
                with open(tts_audio_path, "wb") as f:
                    f.write(response.content)
        except Exception:
            subprocess.run(["ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "3", tts_audio_path], check=True)

        jobs[job_id]["status"] = "lip_syncing"
        output_video_path = f"/tmp/{job_id}_dubbed.mp4"
        
        # 3. Send to Kaggle for Lip-Sync
        # (For now, we just copy the video and swap the audio locally so we can test the server)
        subprocess.run(["ffmpeg", "-i", video_path, "-i", tts_audio_path, "-c:v", "copy", "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0", output_video_path], check=True)

        jobs[job_id]["status"] = "completed"
        jobs[job_id]["output"] = output_video_path

    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)

@app.post("/dub")
async def dub_video(background_tasks: BackgroundTasks, file: UploadFile = File(...), target_lang: str = Form(...)):
    job_id = str(uuid.uuid4())
    video_path = f"/tmp/{job_id}_{file.filename}"
    with open(video_path, "wb") as f:
        f.write(await file.read())
    
    jobs[job_id] = {"status": "queued"}
    background_tasks.add_task(process_dub_job, job_id, video_path, target_lang)
    return {"job_id": job_id}

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    return jobs.get(job_id, {"status": "not_found"})

@app.get("/download/{job_id}")
async def download(job_id: str):
    job = jobs.get(job_id)
    if job and job.get("status") == "completed":
        return FileResponse(job["output"], media_type="video/mp4", filename="dubbed_video.mp4")
    return {"error": "File not ready"}