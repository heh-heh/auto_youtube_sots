import os
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware\nfrom fastapi import Header

app=FastAPI(title="AI YouTube Shorts API")
origins=[x.strip() for x in os.getenv("ALLOWED_ORIGINS","*").split(",") if x.strip()]
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=["*"],allow_headers=["*"])

@app.get("/health")
def health():
    return {"ok":True,"service":"youtube-shorts","oauth_configured":bool(os.getenv("YOUTUBE_CLIENT_ID"))}

@app.get("/api/youtube/status")
def status():
    return {"connected":False,"message":"YouTube OAuth credentials are required."}

@app.post("/api/youtube/upload")
async def upload(video:UploadFile=File(...),title:str=Form(...),description:str=Form(""),privacy:str=Form("private"),publish_at:str=Form("")):
    if privacy not in {"private","unlisted","public"}:
        raise HTTPException(400,"invalid privacy")
    if not video.filename:
        raise HTTPException(400,"video file is required")
    return {"ok":False,"status":"oauth_required","filename":video.filename,"message":"Connect YouTube OAuth before upload."}
