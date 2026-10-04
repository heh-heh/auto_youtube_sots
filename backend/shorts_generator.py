import json
import os
import secrets
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

OUTPUT_DIR = Path(os.getenv("SHORTS_OUTPUT_DIR", "/home/ssm-user/youtube-shorts-data/generated"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SHORTS_DEMO_MODE = os.getenv("SHORTS_DEMO_MODE", "false").lower() in ("1", "true", "yes", "on")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "alloy")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
SHORTS_DEV_MODE = os.getenv("SHORTS_DEV_MODE", "true").lower() in ("1", "true", "yes", "on")
SCENE_COUNT = max(4, min(8, int(os.getenv("SHORTS_SCENE_COUNT", "6"))))
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "/usr/bin/ffmpeg")
FFPROBE_BIN = os.getenv("FFPROBE_BIN", "/usr/bin/ffprobe")


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
        [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio)],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode:
        raise RuntimeError("ffprobe 실패")
    return float(r.stdout.strip())


def demo_script(keyword):
    title = f"{keyword} 30초 핵심 정리"
    script = (
        f"오늘은 {keyword}에 대해 핵심만 빠르게 알아보겠습니다. "
        "이 영상은 비용 없이 전체 영상 생성 파이프라인을 테스트하는 데모 쇼츠입니다. "
        "실제 서비스에서는 최신 자료를 바탕으로 대본과 장면 구성을 자동으로 생성할 수 있습니다. "
        "지금은 외부 AI API 없이 영상 생성과 MP4 렌더링이 정상 동작하는지 확인합니다."
    )
    hashtags = f"#shorts #{keyword.replace(' ', '')} #데모"
    scenes = [
        {"video_query": keyword, "narration_hint": "주제 소개"},
        {"video_query": "technology abstract", "narration_hint": "핵심 설명"},
        {"video_query": "business technology", "narration_hint": "서비스 동작 설명"},
        {"video_query": "mobile phone vertical", "narration_hint": "자동화 과정"},
        {"video_query": "computer programming", "narration_hint": "파이프라인 테스트"},
        {"video_query": "success celebration", "narration_hint": "마무리"},
    ][:SCENE_COUNT]
    while len(scenes) < SCENE_COUNT:
        scenes.append({"video_query": "technology abstract", "narration_hint": "추가 장면"})
    return title, script, hashtags, scenes


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
                    "scenes는 정확히 장면 수만큼의 배열이며 각 항목은 video_query와 narration_hint를 가진다. "
                    "video_query는 Pexels에서 검색하기 좋은 짧은 영어 검색어다. "
                    "사람, 장소, 사물, 행동처럼 실제 촬영 영상으로 찾기 쉬운 표현을 사용한다."
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


def dev_video(job, index, duration=10):
    """Create a moving MP4 locally when external video APIs are unavailable."""
    path = OUTPUT_DIR / f"{job}_dev_scene_{index}.mp4"
    hue = (index * 37) % 360
    vf = (
        "drawbox=x='(w-420)/2+180*sin(2*PI*t/3)':"
        "y='(h-420)/2+180*cos(2*PI*t/3)':w=420:h=420:"
        "color=white@0.16:t=fill,format=yuv420p"
    )
    cmd = [
        FFMPEG_BIN, "-y", "-f", "lavfi",
        "-i", "color=c=0x10182f:s=1080x1920:r=30",
        "-vf", vf, "-t", str(duration), "-an",
        "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "28", "-pix_fmt", "yuv420p", str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise RuntimeError("개발용 영상 생성 실패: " + r.stderr[-2500:])
    return path


def pexels_video(query, job, index):
    if not PEXELS_API_KEY:
        raise RuntimeError("PEXELS_API_KEY가 서버에 설정되지 않았습니다.")

    params = urllib.parse.urlencode({
        "query": query,
        "orientation": "portrait",
        "size": "medium",
        "locale": "en-US",
        "per_page": 15,
    })
    request = urllib.request.Request(
        "https://api.pexels.com/v1/videos/search?" + params,
        headers={"Authorization": PEXELS_API_KEY, "User-Agent": "AI-YouTube-Shorts/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))

    videos = data.get("videos", [])
    if not videos:
        # A portrait search can be too restrictive for niche topics.
        params = urllib.parse.urlencode({"query": query, "size": "medium", "per_page": 15})
        request = urllib.request.Request(
            "https://api.pexels.com/v1/videos/search?" + params,
            headers={"Authorization": PEXELS_API_KEY, "User-Agent": "AI-YouTube-Shorts/1.0"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            videos = json.loads(response.read().decode("utf-8")).get("videos", [])

    if not videos:
        raise RuntimeError(f"Pexels 영상 검색 결과가 없습니다: {query}")

    # Prefer the largest portrait-ish MP4 we can download without excessive size.
    candidates = []
    for video in videos:
        for vf in video.get("video_files", []):
            if vf.get("file_type") != "video/mp4" or not vf.get("link"):
                continue
            w, h = int(vf.get("width") or 0), int(vf.get("height") or 0)
            portrait_score = 1 if h >= w else 0
            candidates.append((portrait_score, min(w, 1080), vf["link"]))
    if not candidates:
        raise RuntimeError(f"Pexels에서 MP4 파일을 찾지 못했습니다: {query}")

    candidates.sort(reverse=True)
    link = candidates[0][2]
    path = OUTPUT_DIR / f"{job}_scene_{index}.mp4"
    urllib.request.urlretrieve(link, path)
    return path


def render(clips, audio, srt, output, duration):
    segment = duration / len(clips)
    concat = OUTPUT_DIR / f"{output.stem}_concat.txt"
    normalized = []

    # Normalize every stock clip into a 1080x1920 vertical segment.
    for index, clip in enumerate(clips, 1):
        normalized_path = OUTPUT_DIR / f"{output.stem}_norm_{index}.mp4"
        target = segment
        cmd = [
            FFMPEG_BIN, "-y", "-stream_loop", "-1", "-i", str(clip),
            "-t", f"{target:.3f}",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
            "-r", "30", "-an", "-c:v", "libx264", "-preset", os.getenv("FFMPEG_PRESET", "veryfast"),
            "-crf", "25", "-pix_fmt", "yuv420p", str(normalized_path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode:
            raise RuntimeError(r.stderr[-3000:])
        normalized.append(normalized_path)

    lines = []
    for item in normalized:
        lines.append(f"file '{item.as_posix()}'")
    concat.write_text("\n".join(lines), encoding="utf-8")

    subtitle = srt.as_posix().replace("\\", "/").replace(":", "\\:")
    style = "FontName=Noto Sans CJK KR,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=3,Alignment=2,MarginV=180"
    vf = "subtitles=" + subtitle + ":force_style='" + style + "'"

    cmd = [
        FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
        "-i", str(audio), "-vf", vf,
        "-c:v", "libx264", "-preset", os.getenv("FFMPEG_PRESET", "veryfast"),
        "-crf", "25", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        "-movflags", "+faststart", str(output),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    concat.unlink(missing_ok=True)
    for item in normalized:
        item.unlink(missing_ok=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-5000:])


def generate(keyword):
    job = secrets.token_hex(8)
    audio = OUTPUT_DIR / f"{job}.mp3"
    srt = OUTPUT_DIR / f"{job}.srt"
    video = OUTPUT_DIR / f"{job}.mp4"
    clips = []

    try:
        if SHORTS_DEMO_MODE:
            title, script, hashtags, scenes = demo_script(keyword)
            duration = 30.0
            make_srt(title, script, duration, srt)
            r = subprocess.run([
                FFMPEG_BIN, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", str(duration), "-c:a", "libmp3lame", "-b:a", "96k", str(audio)
            ], capture_output=True, text=True, timeout=60)
            if r.returncode:
                raise RuntimeError("데모 오디오 생성 실패: " + r.stderr[-2000:])
            for index in range(1, SCENE_COUNT + 1):
                clips.append(dev_video(job, index, duration / SCENE_COUNT))
            render(clips, audio, srt, video, duration)
            return {
                "job_id": job, "title": title, "script": script, "hashtags": hashtags,
                "duration": duration, "scene_count": len(clips), "filename": video.name,
                "media_source": "demo", "attribution_url": None,
            }

        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY가 서버에 설정되지 않았습니다.")
        if not PEXELS_API_KEY:
            if SHORTS_DEV_MODE:
                title = f"{keyword} 테스트 영상"
                script = "외부 영상 API 없이 생성 파이프라인을 테스트하는 영상입니다."
                hashtags = "#shorts #test"
                duration = 10.0
                make_srt(title, script, duration, srt)
                r = subprocess.run([
                    FFMPEG_BIN, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", str(duration), "-c:a", "aac", "-b:a", "96k", str(audio)
                ], capture_output=True, text=True, timeout=60)
                if r.returncode:
                    raise RuntimeError("개발용 오디오 생성 실패: " + r.stderr[-2000:])
                clips.append(dev_video(job, 1, duration))
                render(clips, audio, srt, video, duration)
                return {
                    "job_id": job, "title": title, "script": script, "hashtags": hashtags,
                    "duration": duration, "scene_count": 1, "filename": video.name,
                    "media_source": "dev", "attribution_url": None
                }
            raise RuntimeError("PEXELS_API_KEY가 서버에 설정되지 않았습니다.")

        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
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

        for index, scene in enumerate(scenes, 1):
            query = str(scene.get("video_query", keyword)).strip()
            clips.append(pexels_video(query, job, index))

        render(clips, audio, srt, video, duration)

        return {
            "job_id": job,
            "title": title,
            "script": script,
            "hashtags": hashtags,
            "duration": round(duration, 1),
            "scene_count": len(clips),
            "filename": video.name,
            "media_source": "Pexels",
            "attribution_url": "https://www.pexels.com/",
        }
    finally:
        audio.unlink(missing_ok=True)
        srt.unlink(missing_ok=True)
        for clip in clips:
            clip.unlink(missing_ok=True)

