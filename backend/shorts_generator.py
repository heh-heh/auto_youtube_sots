import json
import os
import secrets
import subprocess
from pathlib import Path
from openai import OpenAI

OUTPUT_DIR = Path(os.getenv("SHORTS_OUTPUT_DIR", "/home/ssm-user/youtube-shorts-data/generated"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")

def ts(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def make_srt(title, script, duration, path):
    chunks = [x.strip() for x in script.replace("!", "!\n").replace("?", "?\n").replace(".", ".\n").replace("。", "。\n").splitlines() if x.strip()]
    if not chunks:
        chunks = [script]
    weights = [max(1, len(x.replace(" ", ""))) for x in chunks]
    total = sum(weights)
    rows = ["1", "00:00:00,000 --> 00:00:04,000", title, ""]
    current = 0.0
    for i, (chunk, weight) in enumerate(zip(chunks, weights), 2):
        start = current
        end = duration if i - 1 == len(chunks) else min(duration, current + duration * weight / total)
        rows.extend([str(i), f"{ts(start)} --> {ts(end)}", chunk, ""])
        current = end
    path.write_text("\n".join(rows), encoding="utf-8")

def duration_of(audio):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio)], capture_output=True, text=True, timeout=30)
    if r.returncode:
        raise RuntimeError("ffprobe 실패")
    return float(r.stdout.strip())

def render(audio, srt, output):
    style = "FontName=Noto Sans CJK KR,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=3,Alignment=2,MarginV=180"
    vf = "subtitles=" + str(srt) + ":force_style='" + style + "'"
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x10182f:s=1080x1920:r=30", "-i", str(audio), "-vf", vf, "-c:v", "libx264", "-preset", os.getenv("FFMPEG_PRESET", "veryfast"), "-crf", "25", "-tune", "stillimage", "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p", "-shortest", "-movflags", "+faststart", str(output)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise RuntimeError(r.stderr[-3500:])

def make_script(client, keyword):
    r = client.responses.create(
        model=OPENAI_MODEL,
        input=[
            {"role": "system", "content": "한국어 YouTube Shorts 작가다. 키워드로 50~60초 정보형 쇼츠를 만든다. 과장이나 근거 없는 숫자를 만들지 않는다. JSON만 반환한다. 필드는 title, script, hashtags. script는 230~320자 자연스러운 한국어 내레이션이며 괄호, 이모지, 장면 지시문을 넣지 않는다."},
            {"role": "user", "content": f"키워드: {keyword}"}
        ]
    )
    raw = r.output_text.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    data = json.loads(raw)
    return str(data.get("title", keyword)).strip(), str(data.get("script", "")).strip(), str(data.get("hashtags", "#shorts")).strip()

def generate(keyword):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY가 서버에 설정되지 않았습니다.")
    client = OpenAI(api_key=OPENAI_API_KEY)
    job = secrets.token_hex(8)
    audio = OUTPUT_DIR / f"{job}.mp3"
    srt = OUTPUT_DIR / f"{job}.srt"
    video = OUTPUT_DIR / f"{job}.mp4"
    try:
        title, script, hashtags = make_script(client, keyword)
        speech = client.audio.speech.create(model=OPENAI_TTS_MODEL, voice=OPENAI_TTS_VOICE, input=script, response_format="mp3", instructions="한국어 쇼츠 내레이션처럼 또렷하고 자연스럽고 약간 빠르게 읽어줘.", speed=1.05)
        speech.write_to_file(audio)
        duration = duration_of(audio)
        if duration > 65:
            raise RuntimeError(f"생성 음성이 65초를 초과했습니다: {duration:.1f}초")
        make_srt(title, script, duration, srt)
        render(audio, srt, video)
        return {"job_id": job, "title": title, "script": script, "hashtags": hashtags, "duration": round(duration, 1), "filename": video.name}
    finally:
        audio.unlink(missing_ok=True)
        srt.unlink(missing_ok=True)
