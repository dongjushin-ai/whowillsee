"""발표 영상: 장면을 일정 간격으로 뽑아 '발표 모습' 지표를 잰다.

- 정면 얼굴 비율: 정면 얼굴 검출기가 얼굴을 찾은 장면의 비율. 카메라(청중)를 보고 말하는지에 대한 대용 지표다.
  시선 추적이 아니므로 '대략적인 추정'이다.
- 얼굴이 화면 안에 있는 비율, 얼굴 크기(너무 멀리 있는지), 밝기(조명), 좌우 움직임.
- 대표 장면 몇 장을 JPEG 로 저장해 AI 평가와 화면 표시에 쓴다.
- 영상 속 음성은 ffmpeg 로 뽑아 발표 녹음 분석(recording)으로 넘긴다.
"""
from __future__ import annotations

import shutil
import statistics
import subprocess
import tempfile
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}
MAX_VIDEO_BYTES = 300 * 1024 * 1024
SAMPLE_EVERY = 2.0      # 초
MAX_SAMPLES = 300
KEYFRAMES = 8
KEYFRAME_EDGE = 768


class VideoError(Exception):
    pass


def available() -> bool:
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


class FaceDetector:
    """OpenCV 에 들어 있는 Haar 얼굴 검출기 (정면 + 옆얼굴)."""

    def __init__(self):
        import cv2

        base = cv2.data.haarcascades
        self.cv2 = cv2
        self.frontal = cv2.CascadeClassifier(base + "haarcascade_frontalface_default.xml")
        self.profile = cv2.CascadeClassifier(base + "haarcascade_profileface.xml")
        if self.frontal.empty():
            raise VideoError("얼굴 검출 모델을 불러올 수 없습니다 (opencv-python-headless 4.x 가 필요합니다).")

    def __call__(self, gray) -> dict:
        h, w = gray.shape[:2]
        min_size = (max(24, w // 25), max(24, h // 25))
        faces = self.frontal.detectMultiScale(gray, 1.1, 5, minSize=min_size)
        frontal = len(faces) > 0
        if not frontal:
            faces = self.profile.detectMultiScale(gray, 1.1, 5, minSize=min_size)
            if len(faces) == 0:  # 반대쪽 옆얼굴
                flipped = self.cv2.flip(gray, 1)
                faces = [(w - x - fw, y, fw, fh) for (x, y, fw, fh) in self.profile.detectMultiScale(flipped, 1.1, 5, minSize=min_size)]
        if len(faces) == 0:
            return {"frontal": False, "visible": False, "box": None}
        x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])  # 가장 큰 얼굴 = 발표자
        # numpy 숫자는 JSON 으로 저장되지 않으므로 파이썬 float 로 바꾼다
        return {"frontal": bool(frontal), "visible": True,
                "box": {"x": float(x) / w, "y": float(y) / h, "w": float(fw) / w, "h": float(fh) / h}}


def analyze_file(path: Path, out_dir: Path, detector=None) -> dict:
    """영상을 분석해 지표와 대표 장면 목록을 돌려준다. 대표 장면은 out_dir 에 저장한다."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise VideoError("영상을 열 수 없습니다. mp4(H.264) 형식으로 저장해 다시 올려주세요.")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        duration = frames / fps if fps > 0 else 0
        if duration <= 0:
            raise VideoError("영상 길이를 알 수 없습니다.")
        step = max(SAMPLE_EVERY, duration / MAX_SAMPLES)
        detector = detector or FaceDetector()
        samples, kept = [], {}
        key_times = [duration * (i + 0.5) / KEYFRAMES for i in range(KEYFRAMES)]
        t = 0.0
        while t < duration:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            found = detector(gray)
            samples.append({"t": round(t, 1), "brightness": round(float(gray.mean()) / 255, 3), **found})
            # 대표 장면: 각 구간 가운데에 가장 가까운 장면
            for k, kt in enumerate(key_times):
                if k not in kept or abs(t - kt) < abs(kept[k][0] - kt):
                    kept[k] = (t, frame)
            t += step
    finally:
        cap.release()
    if not samples:
        raise VideoError("영상에서 장면을 읽지 못했습니다.")

    out_dir.mkdir(parents=True, exist_ok=True)
    keyframes = []
    for k in sorted(kept):
        kt, frame = kept[k]
        h, w = frame.shape[:2]
        scale = KEYFRAME_EDGE / max(h, w)
        if scale < 1:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
        name = f"frame-{k + 1:02d}.jpg"
        cv2.imwrite(str(out_dir / name), frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if not any(f["t"] == round(kt, 1) for f in keyframes):
            keyframes.append({"t": round(kt, 1), "image": name})
    return {"metrics": metrics(samples, duration, step), "keyframes": keyframes}


def metrics(samples: list[dict], duration: float, step: float) -> dict:
    n = len(samples)
    visible = [s for s in samples if s["visible"]]
    frontal = [s for s in samples if s["frontal"]]
    longest, run, longest_at, run_start = 0, 0, None, None
    for s in samples:
        if s["frontal"]:
            run = 0
            continue
        if run == 0:
            run_start = s["t"]
        run += 1
        if run > longest:
            longest, longest_at = run, run_start
    sizes = [s["box"]["h"] for s in visible]
    centers = [s["box"]["x"] + s["box"]["w"] / 2 for s in visible]
    return {
        "duration_seconds": round(duration, 1),
        "samples": n,
        "sample_every": round(step, 1),
        "frontal_ratio": round(len(frontal) / n, 2),
        "visible_ratio": round(len(visible) / n, 2),
        "longest_away_seconds": round(longest * step, 1),
        "longest_away_at": longest_at,
        "first_away_at": next((s["t"] for s in samples if not s["visible"]), None),
        "face_size": round(float(statistics.median(sizes)), 3) if sizes else None,
        "brightness": round(float(statistics.mean(s["brightness"] for s in samples)), 2),
        "movement": round(float(statistics.pstdev(centers)), 3) if len(centers) > 1 else 0.0,
        # 화면 표시용 타임라인: frontal(정면) / side(옆·아래) / away(화면 밖)
        "timeline": [{"t": s["t"], "state": "frontal" if s["frontal"] else "side" if s["visible"] else "away"}
                     for s in samples],
    }


def extract_audio(path: Path) -> bytes | None:
    """영상의 음성을 받아쓰기용 작은 mp3 로 뽑는다. ffmpeg 가 없거나 음성이 없으면 None."""
    exe = ffmpeg_path()
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "audio.mp3"
        cmd = [exe, "-v", "error", "-y", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", str(out)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        return out.read_bytes() if out.exists() and out.stat().st_size > 1000 else None


def summary(video: dict | None) -> dict | None:
    """분석 결과에 넣을 요약 (타임라인 제외)."""
    if not video:
        return None
    m = video["metrics"]
    return {k: m.get(k) for k in ("duration_seconds", "frontal_ratio", "visible_ratio", "longest_away_seconds",
                                  "longest_away_at", "first_away_at", "face_size", "brightness", "movement")} | {
        "filename": video.get("filename"), "keyframes": video.get("keyframes", [])}
