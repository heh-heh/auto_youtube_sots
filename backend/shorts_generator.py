import base64
import json
import os
import secrets
import subprocess
from pathlib import Path

from openai import OpenAI

OUTPUT_DIR = Path(os.getenv("SHORTS_OUTPUT_DIR", "/home/ssm-user/youtube-shorts-data/generated"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")
SCENE_COUNT = max(4, min(8, int(os.getenv("SHORTS_SCENE_COUNT", "6"))))


def ts(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def make_srt(title, script, duration, path):
    chunks = [x.strip() for x in script.replace("!", "!\n").replace("?", "?\n").replace(".", ".\n").replace("。", "。\n").splitlines() if x.strip()]
    chunks = chunks or [script]
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
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio)],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode:
        raise RuntimeError("ffprobe 실패")
    return float(r.stdout.strip())


def make_script(client, keyword):
    r = client.responses.create(
        model=OPENAI_MODEL,
        input=[
            {
                "role": "system",
                "content": (
                    "한국어 YouTube Shorts 전문 작가 겸 영상 콘티 작가다. "
                    "키워드로 50~60초 정보형 쇼츠를 만든다. 과장이나 근거 없는 숫자를 만들지 않는다. "
                    f"영상은 {SCENE_COUNT}개 장면으로 구성한다. JSON만 반환한다. "
                    "필드는 title, script, hashtags, scenes다. "
                    "script는 230~320자 자연스러운 한국어 내레이션이며 괄호, 이모지, 장면 지시문을 넣지 않는다. "
                    "scenes는 정확히 장면 수만큼의 배열이며 각 항목은 image_prompt와 narration_hint를 가진다. "
                    "image_prompt는 영어로 작성하고, 세로형 YouTube Shorts에 적합한 시네마틱 실사 또는 고품질 일러스트 장면을 설명한다. "
                    "이미지 안에는 글자, 로고, 워터마크를 넣지 않는다. "
                    "각 장면은 앞 장면과 시각적으로 겹치지 않도록 핵심 피사체와 구도를 바꾼다."
                ),
            },
            {"role": "user", "content": f"키워드: {keyword}"},
        ],
    )
    raw = r.output_text.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    data = json.loads(raw)
    title = str(data.get("title", keyword)).strip()
    script = str(data.get("script", "")).strip()
    hashtags = str(data.get("hashtags", "#shorts")).strip()
    scenes = data.get("scenes", [])
    if not script:
        raise RuntimeError("AI가 대본을 생성하지 못했습니다.")
    if not isinstance(scenes, list) or len(scenes) != SCENE_COUNT:
        raise RuntimeError(f"AI 콘티 장면 수가 {SCENE_COUNT}개가 아닙니다.")
    return title, script, hashtags, scenes


def generate_images(client, scenes, job):
    paths = []
    for index, scene in enumerate(scenes, 1):
        prompt = (
            "Create a vertical 9:16 visual for a Korean YouTube Shorts video. "
            "No text, no subtitles, no logos, no watermark. "
            "Strong central subject, clear composition, high contrast, visually engaging on a phone screen. "
            + str(scene.get("image_prompt", "cinematic visual"))
        )
        response = client.images.generate(
            model=OPENAI_IMAGE_MODEL,
            prompt=prompt,
            size="1024x1536",
            quality="low",
        )
        item = response.data[0]
        encoded = getattr(item, "b64_json", None)
        if not encoded:
            raise RuntimeError("이미지 생성 결과를 받지 못했습니다.")
        path = OUTPUT_DIR / f"{job}_scene_{index}.png"
        path.write_bytes(base64.b64decode(encoded))
        paths.append(path)
    return paths


def render(images, audio, srt, output, duration):
    segment = duration / len(images)
    concat = OUTPUT_DIR / f"{output.stem}_concat.txt"
    lines = []
    for image in images:
        lines.append(f"file '{image.as_posix()}'")
        lines.append(f"duration {segment:.4f}")
    lines.append(f"file '{images[-1].as_posix()}'")
    concat.write_text("\n".join(lines), encoding="utf-8")

    subtitle = srt.as_posix().replace("\\", "/").replace(":", "\\:")
    style = "FontName=Noto Sans CJK KR,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=3,Alignment=2,MarginV=180"
    vf = "subtitles=" + subtitle + ":force_style='" + style + "'"
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-i", str(audio),
        "-vf", vf,
        "-c:v", "libx264", "-preset", os.getenv("FFMPEG_PRESET", "veryfast"),
        "-crf", "25", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        "-movflags", "+faststart", str(output),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    concat.unlink(missing_ok=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-5000:])


def generate(keyword):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY가 서버에 설정되지 않았습니다.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    job = secrets.token_hex(8)
    audio = OUTPUT_DIR / f"{job}.mp3"
    srt = OUTPUT_DIR / f"{job}.srt"
    video = OUTPUT_DIR / f"{job}.mp4"
    images = []

    try:
        title, script, hashtags, scenes = make_script(client, keyword)

        speech = client.audio.speech.create(
            model=OPENAI_TTS_MODEL,
            voice=OPENAI_TTS_VOICE,
            input=script,
            response_format="mp3",
            instructions="한국어 쇼츠 내레이션처럼 또렷하고 자연스럽고 약간 빠르게 읽어줘.",
            speed=1.05,
        )
        speech.write_to_file(audio)

        duration = duration_of(audio)
        if duration > 65:
            raise RuntimeError(f"생성 음성이 65초를 초과했습니다: {duration:.1f}초")

        make_srt(title, script, duration, srt)
        images = generate_images(client, scenes, job)
        render(images, audio, srt, video, duration)

        return {
            "job_id": job,
            "title": title,
            "script": script,
            "hashtags": hashtags,
            "duration": round(duration, 1),
            "scene_count": len(images),
            "filename": video.name,
        }
    finally:
        audio.unlink(missing_ok=True)
        srt.unlink(missing_ok=True)
        for image in images:
            image.unlink(missing_ok=True)
