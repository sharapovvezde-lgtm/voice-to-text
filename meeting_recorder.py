"""
Запись экрана + микрофон (один поток).
"""
import os
import subprocess
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except:
    pass

import numpy as np
import cv2
import mss
import sounddevice as sd

CREATE_NO_WINDOW = 0x08000000


def get_ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except:
        return "ffmpeg"


def get_screen_size():
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


class MeetingRecorder:
    def __init__(self, output_dir=None):
        # Папка records в корне проекта (создаётся автоматически)
        self.output_dir = Path(output_dir) if output_dir else Path("records")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.fps = 15
        self.rate = 44100
        self.is_recording = False
        self._stop_event = threading.Event()
        
        self._frames = []
        self._audio = []
        self._base_name = None
    
    def get_microphones(self):
        mics = []
        for i, d in enumerate(sd.query_devices()):
            if d['max_input_channels'] > 0:
                mics.append({"id": i, "name": d['name'], "is_default": i == sd.default.device[0]})
        return mics
    
    def _record_screen(self):
        w, h = get_screen_size()
        region = {"left": 0, "top": 0, "width": w, "height": h}
        
        with mss.mss() as sct:
            while not self._stop_event.is_set():
                t0 = time.time()
                img = sct.grab(region)
                frame = cv2.cvtColor(np.array(img), cv2.COLOR_BGRA2BGR)
                self._frames.append(frame)
                elapsed = time.time() - t0
                time.sleep(max(0, 1.0/self.fps - elapsed))
    
    def _record_audio(self, device):
        chunk = int(self.rate * 0.05)
        try:
            stream = sd.InputStream(
                device=device, samplerate=self.rate, channels=1,
                dtype='int16', blocksize=chunk
            )
            stream.start()
            while not self._stop_event.is_set():
                data, _ = stream.read(chunk)
                self._audio.append(data.flatten().copy())
            stream.stop()
            stream.close()
        except Exception:
            pass
    
    def start(self, region=None, mic_device=None, record_system=False):
        if self.is_recording:
            return False
        
        self._frames = []
        self._audio = []
        self._stop_event.clear()
        
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._base_name = f"Meeting_{ts}"
        
        threading.Thread(target=self._record_screen, daemon=True).start()
        threading.Thread(target=self._record_audio, args=(mic_device,), daemon=True).start()
        
        self.is_recording = True
        return True
    
    def stop(self):
        if not self.is_recording:
            return None
        
        self._stop_event.set()
        self.is_recording = False
        time.sleep(0.3)
        return self._save()
    
    def _save(self):
        if not self._frames:
            return {"video": None, "base_name": None}
        
        tmp_video = self.output_dir / f"{self._base_name}_tmp.avi"
        tmp_audio = self.output_dir / f"{self._base_name}_tmp.wav"
        final_video = self.output_dir / f"{self._base_name}.mp4"
        
        h, w = self._frames[0].shape[:2]
        out = cv2.VideoWriter(str(tmp_video), cv2.VideoWriter_fourcc(*'XVID'), self.fps, (w, h))
        try:
            for f in self._frames:
                out.write(f)
        finally:
            out.release()
            del out
        
        if self._audio:
            arr = np.concatenate(self._audio)
            arr = np.clip(arr.astype(np.int32) * 2, -32768, 32767).astype(np.int16)
            with wave.open(str(tmp_audio), 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.rate)
                wf.writeframes(arr.tobytes())
        
        ffmpeg = get_ffmpeg()
        # -pix_fmt yuv420p — совместимость с плеерами Windows; -movflags +faststart, -flush_packets 1
        if tmp_audio.exists():
            cmd = [
                ffmpeg, '-y', '-i', str(tmp_video), '-i', str(tmp_audio),
                '-filter_complex', '[1:a]volume=2[a]', '-map', '0:v', '-map', '[a]',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-b:a', '192k',
                '-movflags', '+faststart', '-flush_packets', '1',
                '-shortest', str(final_video)
            ]
        else:
            cmd = [
                ffmpeg, '-y', '-i', str(tmp_video),
                '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                '-movflags', '+faststart', '-flush_packets', '1',
                str(final_video)
            ]
        
        try:
            proc = subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=180)
            if proc.returncode != 0:
                proc = None
        except Exception:
            proc = None
        
        # Освобождение памяти: удаляем временные файлы и очищаем буферы
        if final_video.exists():
            try:
                tmp_video.unlink(missing_ok=True)
                tmp_audio.unlink(missing_ok=True)
            except Exception:
                pass
        
        # Полная очистка буферов после сохранения
        self._frames.clear()
        self._audio.clear()
        
        if final_video.exists():
            return {"video": str(final_video), "base_name": self._base_name}
        return {"video": str(tmp_video), "base_name": self._base_name}
