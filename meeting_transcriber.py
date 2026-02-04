"""
Расшифровка записи встречи из видео.
"""
import os
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
import wave
import numpy as np

CREATE_NO_WINDOW = 0x08000000

try:
    import whisper
    WHISPER_OK = True
except Exception:
    WHISPER_OK = False


def get_ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


class MeetingTranscriber:
    def __init__(self, model_name="medium"):
        self.model_name = model_name
        self.model = None
    
    def load_model(self):
        if not WHISPER_OK:
            raise RuntimeError("whisper not installed")
        if not self.model:
            self.model = whisper.load_model(self.model_name)
        return self.model
    
    def _extract_audio(self, video_path):
        ffmpeg = get_ffmpeg()
        tmp_wav = os.path.join(tempfile.gettempdir(), "lv_audio.wav")
        cmd = [ffmpeg, '-y', '-i', video_path, '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', tmp_wav]
        try:
            subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=120)
            if os.path.exists(tmp_wav):
                return tmp_wav
        except Exception:
            pass
        return None
    
    def _load_wav(self, path):
        if not path or not os.path.exists(path):
            return None
        try:
            with wave.open(path, 'rb') as wf:
                data = wf.readframes(wf.getnframes())
            return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception:
            return None
    
    def transcribe_meeting(self, video_path=None, audio_path=None, language="ru"):
        if video_path and os.path.exists(video_path):
            audio_path = self._extract_audio(video_path)
        
        if not audio_path:
            return {"segments": [], "full_text": "(Нет аудио)"}
        
        audio = self._load_wav(audio_path)
        if audio_path.startswith(tempfile.gettempdir()):
            try:
                os.remove(audio_path)
            except Exception:
                pass
        
        if audio is None or len(audio) < 1000:
            return {"segments": [], "full_text": "(Аудио пустое)"}
        
        self.load_model()
        result = self.model.transcribe(audio, language=language, verbose=False, temperature=0.0, best_of=5, beam_size=5)
        
        segments = [{"start": s["start"], "end": s["end"], "text": s["text"].strip()}
                   for s in result.get("segments", []) if s["text"].strip()]
        
        lines = []
        prev_end = 0
        for s in segments:
            text = s["text"].strip()
            if not text:
                continue
            pause = s["start"] - prev_end
            if pause > 1.5 and prev_end > 0:
                lines.append("")
            lines.append(text)
            prev_end = s["end"]
        
        return {"segments": segments, "full_text": "\n".join(lines)}
    
    def save_report(self, transcript, output_path=None, video_path=None, **kwargs):
        if not output_path:
            output_path = str(Path(video_path).with_suffix('.txt')) if video_path else f"Meeting_{datetime.now():%Y%m%d_%H%M%S}.txt"
        
        report = f"{'='*50}\nЗАПИСЬ — {datetime.now():%d.%m.%Y %H:%M}\n{'='*50}\n\n{transcript['full_text']}\n\n{'='*50}\n"
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report)
        return output_path
