"""
Meeting Recorder v2 — Универсальный захват экрана + 2 аудиоканала
- Видео: захват экрана/окна через mss (высокий FPS)
- Аудио 1: Микрофон (голос пользователя = "Я")
- Аудио 2: Системный звук WASAPI Loopback (голос собеседника)
- Выход: .mp4 с двумя аудиодорожками
"""
import os
import sys
import time
import threading
import queue
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import cv2
import mss
import sounddevice as sd
from scipy.io import wavfile

# Опционально: soundcard для WASAPI Loopback
try:
    import soundcard as sc
    SOUNDCARD_AVAILABLE = True
except ImportError:
    SOUNDCARD_AVAILABLE = False
    print("⚠️ soundcard не установлен. Loopback недоступен.")


class MeetingRecorder:
    """
    Класс для записи встреч: экран + микрофон + системный звук
    """
    
    def __init__(self, output_dir: str = None):
        self.output_dir = Path(output_dir) if output_dir else Path("./records")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Настройки видео
        self.fps = 15
        self.monitor = None  # Номер монитора или регион {left, top, width, height}
        
        # Настройки аудио
        self.mic_samplerate = 16000
        self.sys_samplerate = 48000
        self.mic_device = None  # None = default
        
        # Состояние
        self.is_recording = False
        self._stop_event = threading.Event()
        
        # Буферы
        self._video_frames = []
        self._mic_audio = []
        self._sys_audio = []
        
        # Потоки
        self._video_thread = None
        self._mic_thread = None
        self._sys_thread = None
        
        # Временные файлы
        self._temp_video = None
        self._temp_mic = None
        self._temp_sys = None
    
    def get_monitors(self) -> list:
        """Список доступных мониторов"""
        with mss.mss() as sct:
            monitors = []
            for i, mon in enumerate(sct.monitors):
                if i == 0:
                    continue  # Пропускаем "все мониторы"
                monitors.append({
                    "id": i,
                    "name": f"Монитор {i}",
                    "width": mon["width"],
                    "height": mon["height"],
                    "left": mon["left"],
                    "top": mon["top"]
                })
            return monitors
    
    def get_microphones(self) -> list:
        """Список доступных микрофонов"""
        devices = sd.query_devices()
        mics = []
        for i, dev in enumerate(devices):
            if dev['max_input_channels'] > 0:
                mics.append({
                    "id": i,
                    "name": dev['name'],
                    "channels": dev['max_input_channels'],
                    "is_default": i == sd.default.device[0]
                })
        return mics
    
    def get_loopback_device(self):
        """Найти устройство для захвата системного звука"""
        if not SOUNDCARD_AVAILABLE:
            return None
        
        mics = sc.all_microphones(include_loopback=True)
        for mic in mics:
            if mic.isloopback:
                return mic
        return None
    
    def set_monitor(self, monitor_id: int = 1):
        """Установить монитор для записи"""
        with mss.mss() as sct:
            if monitor_id < len(sct.monitors):
                self.monitor = sct.monitors[monitor_id]
            else:
                self.monitor = sct.monitors[1]  # Первый реальный монитор
    
    def set_region(self, left: int, top: int, width: int, height: int):
        """Установить регион экрана для записи"""
        self.monitor = {"left": left, "top": top, "width": width, "height": height}
    
    def _record_video(self):
        """Поток записи видео"""
        with mss.mss() as sct:
            frame_time = 1.0 / self.fps
            
            while not self._stop_event.is_set():
                start = time.time()
                
                # Захват кадра
                img = sct.grab(self.monitor)
                frame = np.array(img)
                # BGRA -> BGR
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                self._video_frames.append(frame)
                
                # Поддержание FPS
                elapsed = time.time() - start
                if elapsed < frame_time:
                    time.sleep(frame_time - elapsed)
    
    def _record_microphone(self):
        """Поток записи микрофона"""
        chunk_duration = 0.1  # 100ms chunks
        chunk_samples = int(self.mic_samplerate * chunk_duration)
        
        def callback(indata, frames, time_info, status):
            if status:
                print(f"Mic status: {status}")
            self._mic_audio.append(indata.copy())
        
        with sd.InputStream(
            device=self.mic_device,
            samplerate=self.mic_samplerate,
            channels=1,
            dtype='float32',
            blocksize=chunk_samples,
            callback=callback
        ):
            while not self._stop_event.is_set():
                time.sleep(0.05)
    
    def _record_system_audio(self):
        """Поток записи системного звука (WASAPI Loopback)"""
        if not SOUNDCARD_AVAILABLE:
            return
        
        loopback = self.get_loopback_device()
        if not loopback:
            print("⚠️ Loopback устройство не найдено")
            return
        
        chunk_samples = int(self.sys_samplerate * 0.1)  # 100ms
        
        try:
            with loopback.recorder(samplerate=self.sys_samplerate, channels=2) as rec:
                while not self._stop_event.is_set():
                    data = rec.record(numframes=chunk_samples)
                    # Stereo -> Mono
                    mono = np.mean(data, axis=1)
                    self._sys_audio.append(mono.astype('float32'))
        except Exception as e:
            print(f"❌ Ошибка записи системного звука: {e}")
    
    def start(self, monitor_id: int = 1, mic_device: int = None):
        """Начать запись"""
        if self.is_recording:
            print("⚠️ Запись уже идёт")
            return False
        
        # Очистка
        self._video_frames = []
        self._mic_audio = []
        self._sys_audio = []
        self._stop_event.clear()
        
        # Настройки
        self.set_monitor(monitor_id)
        self.mic_device = mic_device
        
        print(f"▶️ Начинаю запись...")
        print(f"   Монитор: {self.monitor}")
        print(f"   Микрофон: {mic_device or 'default'}")
        
        self.is_recording = True
        
        # Запуск потоков
        self._video_thread = threading.Thread(target=self._record_video, daemon=True)
        self._mic_thread = threading.Thread(target=self._record_microphone, daemon=True)
        self._sys_thread = threading.Thread(target=self._record_system_audio, daemon=True)
        
        self._video_thread.start()
        self._mic_thread.start()
        self._sys_thread.start()
        
        return True
    
    def stop(self) -> str:
        """Остановить запись и сохранить файл"""
        if not self.is_recording:
            print("⚠️ Запись не запущена")
            return None
        
        print("⏹️ Останавливаю запись...")
        self._stop_event.set()
        self.is_recording = False
        
        # Ждём завершения потоков
        if self._video_thread:
            self._video_thread.join(timeout=2)
        if self._mic_thread:
            self._mic_thread.join(timeout=2)
        if self._sys_thread:
            self._sys_thread.join(timeout=2)
        
        # Сохранение
        return self._save_recording()
    
    def _save_recording(self) -> str:
        """Сохранить видео и аудио в файл"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Создаём временные файлы
        temp_dir = tempfile.gettempdir()
        video_path = os.path.join(temp_dir, f"video_{timestamp}.avi")
        mic_path = os.path.join(temp_dir, f"mic_{timestamp}.wav")
        sys_path = os.path.join(temp_dir, f"sys_{timestamp}.wav")
        output_path = str(self.output_dir / f"Meeting_{timestamp}.mp4")
        
        # === Сохраняем видео ===
        if self._video_frames:
            print(f"💾 Сохраняю видео ({len(self._video_frames)} кадров)...")
            h, w = self._video_frames[0].shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*'XVID')
            out = cv2.VideoWriter(video_path, fourcc, self.fps, (w, h))
            for frame in self._video_frames:
                out.write(frame)
            out.release()
        else:
            print("⚠️ Нет видеокадров")
            return None
        
        # === Сохраняем аудио микрофона ===
        if self._mic_audio:
            print(f"💾 Сохраняю аудио микрофона...")
            mic_data = np.concatenate(self._mic_audio)
            # Нормализация
            mic_data = mic_data / (np.max(np.abs(mic_data)) + 1e-8)
            mic_int16 = (mic_data * 32767).astype(np.int16)
            wavfile.write(mic_path, self.mic_samplerate, mic_int16)
        
        # === Сохраняем системный звук ===
        if self._sys_audio:
            print(f"💾 Сохраняю системный звук...")
            sys_data = np.concatenate(self._sys_audio)
            sys_data = sys_data / (np.max(np.abs(sys_data)) + 1e-8)
            sys_int16 = (sys_data * 32767).astype(np.int16)
            wavfile.write(sys_path, self.sys_samplerate, sys_int16)
        
        # === Объединяем через FFmpeg ===
        print(f"🎬 Объединяю в MP4...")
        output_path = self._merge_with_ffmpeg(
            video_path, mic_path, sys_path, output_path
        )
        
        # Очистка временных файлов
        for f in [video_path, mic_path, sys_path]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except:
                    pass
        
        return output_path
    
    def _merge_with_ffmpeg(self, video_path, mic_path, sys_path, output_path) -> str:
        """Объединить видео и аудио через FFmpeg"""
        import subprocess
        
        # Проверяем наличие ffmpeg
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        except:
            print("⚠️ FFmpeg не найден! Сохраняю только видео без звука.")
            # Копируем видео как есть
            import shutil
            output_avi = output_path.replace('.mp4', '.avi')
            shutil.copy(video_path, output_avi)
            return output_avi
        
        # FFmpeg команда: видео + 2 аудиодорожки
        cmd = ['ffmpeg', '-y']
        
        # Входные файлы
        cmd.extend(['-i', video_path])
        if os.path.exists(mic_path):
            cmd.extend(['-i', mic_path])
        if os.path.exists(sys_path):
            cmd.extend(['-i', sys_path])
        
        # Маппинг потоков
        cmd.extend(['-map', '0:v'])  # Видео
        if os.path.exists(mic_path):
            cmd.extend(['-map', '1:a'])  # Аудио микрофона
        if os.path.exists(sys_path):
            idx = 2 if os.path.exists(mic_path) else 1
            cmd.extend(['-map', f'{idx}:a'])  # Системный звук
        
        # Кодеки
        cmd.extend([
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-crf', '23',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-shortest',
            output_path
        ])
        
        print(f"   FFmpeg: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"❌ FFmpeg ошибка: {result.stderr}")
            return video_path
        
        print(f"✅ Сохранено: {output_path}")
        return output_path
    
    def get_audio_paths(self) -> tuple:
        """Вернуть пути к аудиофайлам для транскрибации"""
        return self._temp_mic, self._temp_sys


# ===== Тестовый запуск =====
if __name__ == "__main__":
    print("\n" + "="*60)
    print("🎬 ТЕСТ MeetingRecorder")
    print("="*60)
    
    recorder = MeetingRecorder(output_dir="./dev_test/temp_records")
    
    # Показать мониторы
    print("\n📺 Доступные мониторы:")
    for mon in recorder.get_monitors():
        print(f"   {mon['id']}: {mon['name']} ({mon['width']}x{mon['height']})")
    
    # Показать микрофоны
    print("\n🎤 Доступные микрофоны:")
    for mic in recorder.get_microphones():
        default = "✓" if mic['is_default'] else " "
        print(f"   [{default}] {mic['id']}: {mic['name']}")
    
    # Loopback
    loopback = recorder.get_loopback_device()
    if loopback:
        print(f"\n🔁 Loopback: {loopback.name}")
    else:
        print("\n⚠️ Loopback не найден")
    
    # Тестовая запись
    input("\nНажмите Enter для начала 5-секундной тестовой записи...")
    
    recorder.start(monitor_id=1)
    
    for i in range(5, 0, -1):
        print(f"   Осталось: {i} сек...")
        time.sleep(1)
    
    output = recorder.stop()
    
    if output:
        print(f"\n✅ Файл сохранён: {output}")
    else:
        print("\n❌ Ошибка записи")
    
    input("\nНажмите Enter для выхода...")
