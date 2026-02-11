"""
Lesnov Voice — Transcriber
- Извлекает аудио из видео
- Разделяет по паузам
- Формат в столбик
"""
import os
import sys
import subprocess
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from datetime import datetime
import wave
import numpy as np

CREATE_NO_WINDOW = 0x08000000

try:
    import whisper
    WHISPER_OK = True
except:
    WHISPER_OK = False


def get_ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except:
        return "ffmpeg"


class MeetingTranscriber:
    def __init__(self, model_name="medium"):
        self.model_name = model_name
        self.model = None
    
    def load_model(self):
        if not WHISPER_OK:
            raise RuntimeError("whisper not installed")
        if not self.model:
            print(f"Loading Whisper '{self.model_name}'...")
            self.model = whisper.load_model(self.model_name)
            print("Model loaded")
        return self.model
    
    def _extract_audio(self, video_path):
        """Извлечь аудио из видео"""
        ffmpeg = get_ffmpeg()
        tmp_wav = os.path.join(tempfile.gettempdir(), "lv_audio.wav")
        
        cmd = [
            ffmpeg, '-y', '-i', video_path,
            '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
            tmp_wav
        ]
        
        try:
            subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=120)
            if os.path.exists(tmp_wav):
                return tmp_wav
        except Exception as e:
            print(f"Extract error: {e}")
        return None
    
    def _load_wav(self, path):
        if not path or not os.path.exists(path):
            return None
        try:
            with wave.open(path, 'rb') as wf:
                data = wf.readframes(wf.getnframes())
            return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        except:
            return None
    
    def transcribe_meeting(self, video_path=None, audio_path=None, mic_path=None, 
                          sys_path=None, language="ru"):
        """Транскрибация видео"""
        
        # Извлекаем аудио из видео
        if video_path and os.path.exists(video_path):
            print(f"Extracting audio from {Path(video_path).name}...")
            audio_path = self._extract_audio(video_path)
        
        if not audio_path:
            return {"segments": [], "full_text": "(Нет аудио)"}
        
        audio = self._load_wav(audio_path)
        
        # Удаляем временный файл
        if audio_path.startswith(tempfile.gettempdir()):
            try:
                os.remove(audio_path)
            except:
                pass
        
        if audio is None or len(audio) < 1000:
            return {"segments": [], "full_text": "(Аудио пустое)"}
        
        print(f"Transcribing ({len(audio)/16000:.1f}s)...")
        self.load_model()
        
        result = self.model.transcribe(
            audio, 
            language=language, 
            verbose=False,
            temperature=0.0,
            best_of=5,
            beam_size=5
        )
        
        segments = [{"start": s["start"], "end": s["end"], "text": s["text"].strip()} 
                   for s in result.get("segments", []) if s["text"].strip()]
        
        print(f"Found {len(segments)} segments")
        
        if not segments:
            return {"segments": [], "full_text": "(Речь не распознана)"}
        
        # Формат в столбик - разделение по паузам
        lines = []
        prev_end = 0
        
        for s in segments:
            text = s["text"].strip()
            if not text:
                continue
            
            pause = s["start"] - prev_end
            
            # Пустая строка при паузе > 1.5 сек
            if pause > 1.5 and prev_end > 0:
                lines.append("")
            
            lines.append(text)
            prev_end = s["end"]
        
        return {"segments": segments, "full_text": "\n".join(lines)}
    
    def save_report(self, transcript, output_path=None, video_path=None, **kwargs):
        
        if not output_path:
            if video_path:
                output_path = str(Path(video_path).with_suffix('.txt'))
            else:
                output_path = f"Meeting_{datetime.now():%Y%m%d_%H%M%S}.txt"
        
        report = f"""{'='*50}
ЗАПИСЬ — {datetime.now():%d.%m.%Y %H:%M}
{'='*50}

{transcript['full_text']}

{'='*50}
"""
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report)
        
        print(f"Saved: {output_path}")
        return output_path
