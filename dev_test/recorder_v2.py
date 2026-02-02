"""
Meeting Recorder v2 — Универсальный захват экрана + 2 аудиоканала
- Видео: захват выбранной области экрана через mss
- Аудио 1: Микрофон (голос пользователя = "Я")
- Аудио 2: Системный звук WASAPI Loopback (голос собеседника)
- Выход: .avi + отдельные WAV файлы для транскрибации
"""
import os
import sys
import time
import threading
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import cv2
import mss
import sounddevice as sd
from scipy.io import wavfile

# PyAudio для WASAPI Loopback
try:
    import pyaudiowpatch as pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    try:
        import pyaudio
        PYAUDIO_AVAILABLE = True
    except ImportError:
        PYAUDIO_AVAILABLE = False
        print("⚠️ pyaudio/pyaudiowpatch не установлен")


# ===== Виджет выбора области экрана =====
from PyQt6.QtWidgets import QWidget, QApplication, QRubberBand, QLabel
from PyQt6.QtCore import Qt, QRect, QPoint, QTimer
from PyQt6.QtGui import QPainter, QColor, QFont


class ScreenRegionSelector(QWidget):
    """
    Полноэкранный виджет для выбора области записи мышкой
    """
    
    def __init__(self, callback=None):
        super().__init__()
        self.callback = callback
        self.selection = None
        self.origin = QPoint()
        self.current_rect = QRect()
        
        # Полноэкранный оверлей
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        
        # Получаем размер всех мониторов
        screen = QApplication.primaryScreen()
        geometry = screen.virtualGeometry()
        self.setGeometry(geometry)
        
        self._drawing = False
    
    def paintEvent(self, event):
        painter = QPainter(self)
        
        # Полупрозрачный тёмный фон
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))
        
        # Если выделяем область - рисуем её
        if self._drawing and not self.current_rect.isNull():
            # Очищаем выделенную область (делаем её прозрачной)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(self.current_rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            
            # Рамка вокруг выделения
            painter.setPen(QColor(0, 200, 0, 255))
            painter.drawRect(self.current_rect)
            
            # Размер области
            size_text = f"{self.current_rect.width()} x {self.current_rect.height()}"
            painter.setFont(QFont("Arial", 14, QFont.Weight.Bold))
            painter.setPen(QColor(255, 255, 255))
            text_x = self.current_rect.x() + 5
            text_y = self.current_rect.y() - 10 if self.current_rect.y() > 30 else self.current_rect.bottom() + 20
            painter.drawText(text_x, text_y, size_text)
        
        # Инструкция вверху
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("Arial", 16))
        instruction = "🎯 Зажмите ЛКМ и выделите область для записи  |  ESC = отмена"
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter, 
                        f"\n\n{instruction}")
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.pos()
            self.current_rect = QRect(self.origin, self.origin)
            self._drawing = True
            self.update()
    
    def mouseMoveEvent(self, event):
        if self._drawing:
            self.current_rect = QRect(self.origin, event.pos()).normalized()
            self.update()
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drawing:
            self._drawing = False
            rect = QRect(self.origin, event.pos()).normalized()
            
            # Минимальный размер 100x100
            if rect.width() >= 100 and rect.height() >= 100:
                global_rect = {
                    "left": self.geometry().x() + rect.x(),
                    "top": self.geometry().y() + rect.y(),
                    "width": rect.width(),
                    "height": rect.height()
                }
                self.selection = global_rect
                
                if self.callback:
                    self.callback(global_rect)
            else:
                if self.callback:
                    self.callback(None)
            
            self.close()
    
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.selection = None
            if self.callback:
                self.callback(None)
            self.close()


class MeetingRecorder:
    """
    Класс для записи встреч: экран + микрофон + системный звук
    """
    
    def __init__(self, output_dir: str = None):
        self.output_dir = Path(output_dir) if output_dir else Path("./records")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Настройки видео
        self.fps = 15
        self.monitor = None
        
        # Настройки аудио
        self.mic_samplerate = 16000
        self.sys_samplerate = 44100
        self.mic_device = None
        
        # Состояние
        self.is_recording = False
        self._stop_event = threading.Event()
        self._record_system = True
        
        # Буферы
        self._video_frames = []
        self._mic_audio = []
        self._sys_audio = []
        
        # Потоки
        self._video_thread = None
        self._mic_thread = None
        self._sys_thread = None
        
        # PyAudio для системного звука
        self._pyaudio = None
        self._loopback_device = None
    
    def get_monitors(self) -> list:
        """Список доступных мониторов"""
        with mss.mss() as sct:
            monitors = []
            for i, mon in enumerate(sct.monitors):
                if i == 0:
                    continue
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
        """Найти устройство WASAPI Loopback для захвата системного звука"""
        if not PYAUDIO_AVAILABLE:
            return None
        
        try:
            p = pyaudio.PyAudio()
            
            # Ищем WASAPI loopback устройство
            wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            
            # Ищем loopback устройство (обычно содержит "loopback" в названии)
            for i in range(p.get_device_count()):
                dev = p.get_device_info_by_index(i)
                
                # Проверяем что это WASAPI и loopback
                if dev.get('hostApi') == wasapi_info['index']:
                    # Ищем устройство с isLoopbackDevice или с "loopback" в имени
                    if dev.get('isLoopbackDevice', False) or 'loopback' in dev['name'].lower():
                        self._loopback_device = dev
                        p.terminate()
                        return dev
                    
                    # Или это устройство вывода по умолчанию
                    if dev['maxInputChannels'] > 0 and dev['maxOutputChannels'] == 0:
                        # Может быть loopback
                        pass
            
            # Если не нашли явный loopback, берём default output device
            default_output = p.get_default_output_device_info()
            self._loopback_device = default_output
            p.terminate()
            return default_output
            
        except Exception as e:
            print(f"⚠️ Ошибка поиска loopback: {e}")
            return None
    
    def set_monitor(self, monitor_id: int = 1):
        """Установить монитор для записи"""
        with mss.mss() as sct:
            if monitor_id < len(sct.monitors):
                self.monitor = sct.monitors[monitor_id]
            else:
                self.monitor = sct.monitors[1]
    
    def _record_video(self):
        """Поток записи видео"""
        with mss.mss() as sct:
            frame_time = 1.0 / self.fps
            
            while not self._stop_event.is_set():
                start = time.time()
                
                try:
                    img = sct.grab(self.monitor)
                    frame = np.array(img)
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                    self._video_frames.append(frame)
                except Exception as e:
                    print(f"Video error: {e}")
                
                elapsed = time.time() - start
                if elapsed < frame_time:
                    time.sleep(frame_time - elapsed)
    
    def _record_microphone(self):
        """Поток записи микрофона"""
        chunk_samples = int(self.mic_samplerate * 0.1)
        
        def callback(indata, frames, time_info, status):
            if status:
                print(f"Mic: {status}")
            self._mic_audio.append(indata.copy())
        
        try:
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
        except Exception as e:
            print(f"❌ Ошибка записи микрофона: {e}")
    
    def _record_system_audio(self):
        """Поток записи системного звука через PyAudio WASAPI"""
        if not PYAUDIO_AVAILABLE:
            print("⚠️ PyAudio недоступен")
            return
        
        try:
            p = pyaudio.PyAudio()
            
            # Получаем WASAPI host API
            wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_speakers = p.get_device_info_by_index(wasapi_info['defaultOutputDevice'])
            
            # Проверяем поддержку loopback
            if not default_speakers.get('isLoopbackDevice', False):
                # Ищем loopback версию этого устройства
                for i in range(p.get_device_count()):
                    dev = p.get_device_info_by_index(i)
                    if dev.get('name', '').startswith(default_speakers['name'].split(' (')[0]):
                        if dev.get('isLoopbackDevice', False):
                            default_speakers = dev
                            break
            
            channels = int(default_speakers['maxInputChannels'])
            if channels < 1:
                channels = 2
            
            rate = int(default_speakers['defaultSampleRate'])
            self.sys_samplerate = rate
            
            chunk = int(rate * 0.1)  # 100ms
            
            stream = p.open(
                format=pyaudio.paFloat32,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=default_speakers['index'],
                frames_per_buffer=chunk
            )
            
            print(f"🔊 Системный звук: {default_speakers['name']}")
            
            while not self._stop_event.is_set():
                try:
                    data = stream.read(chunk, exception_on_overflow=False)
                    audio_data = np.frombuffer(data, dtype=np.float32)
                    
                    # Stereo -> Mono
                    if channels > 1:
                        audio_data = audio_data.reshape(-1, channels)
                        audio_data = np.mean(audio_data, axis=1)
                    
                    self._sys_audio.append(audio_data)
                except Exception as e:
                    print(f"Sys audio read error: {e}")
                    time.sleep(0.1)
            
            stream.stop_stream()
            stream.close()
            p.terminate()
            
        except Exception as e:
            print(f"❌ Ошибка записи системного звука: {e}")
            import traceback
            traceback.print_exc()
    
    def start(self, region: dict = None, mic_device: int = None, record_system: bool = True):
        """
        Начать запись
        
        Args:
            region: {"left": x, "top": y, "width": w, "height": h} - ОБЯЗАТЕЛЬНО!
            mic_device: ID микрофона
            record_system: записывать ли системный звук
        """
        if self.is_recording:
            print("⚠️ Запись уже идёт")
            return False
        
        if not region:
            print("❌ Область записи не выбрана!")
            return False
        
        # Очистка
        self._video_frames = []
        self._mic_audio = []
        self._sys_audio = []
        self._stop_event.clear()
        
        # Настройки области
        self.monitor = region
        self.mic_device = mic_device
        self._record_system = record_system
        
        print(f"▶️ Начинаю запись области: {region['width']}x{region['height']}")
        print(f"   Позиция: ({region['left']}, {region['top']})")
        print(f"   Микрофон: {mic_device or 'default'}")
        print(f"   Системный звук: {'Да' if record_system else 'Нет'}")
        
        self.is_recording = True
        
        # Запуск потоков
        self._video_thread = threading.Thread(target=self._record_video, daemon=True)
        self._mic_thread = threading.Thread(target=self._record_microphone, daemon=True)
        
        self._video_thread.start()
        self._mic_thread.start()
        
        if record_system:
            self._sys_thread = threading.Thread(target=self._record_system_audio, daemon=True)
            self._sys_thread.start()
        
        return True
    
    def stop(self) -> dict:
        """Остановить запись и сохранить файлы"""
        if not self.is_recording:
            print("⚠️ Запись не запущена")
            return None
        
        print("⏹️ Останавливаю запись...")
        self._stop_event.set()
        self.is_recording = False
        
        # Ждём завершения потоков
        if self._video_thread:
            self._video_thread.join(timeout=3)
        if self._mic_thread:
            self._mic_thread.join(timeout=3)
        if self._sys_thread:
            self._sys_thread.join(timeout=3)
        
        return self._save_recording()
    
    def _save_recording(self) -> dict:
        """Сохранить видео и аудио в отдельные файлы"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"Meeting_{timestamp}"
        
        video_path = str(self.output_dir / f"{base_name}.avi")
        mic_path = str(self.output_dir / f"{base_name}_mic.wav")
        sys_path = str(self.output_dir / f"{base_name}_sys.wav")
        
        result = {"video": None, "mic_audio": None, "sys_audio": None, "base_name": base_name}
        
        # === Видео ===
        if self._video_frames:
            print(f"💾 Сохраняю видео ({len(self._video_frames)} кадров)...")
            h, w = self._video_frames[0].shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*'XVID')
            out = cv2.VideoWriter(video_path, fourcc, self.fps, (w, h))
            for frame in self._video_frames:
                out.write(frame)
            out.release()
            result["video"] = video_path
            print(f"   ✅ {video_path}")
        
        # === Микрофон ===
        if self._mic_audio:
            print(f"💾 Сохраняю микрофон...")
            mic_data = np.concatenate(self._mic_audio)
            if mic_data.ndim > 1:
                mic_data = mic_data.flatten()
            max_val = np.max(np.abs(mic_data))
            if max_val > 0:
                mic_data = mic_data / max_val * 0.9
            mic_int16 = (mic_data * 32767).astype(np.int16)
            wavfile.write(mic_path, self.mic_samplerate, mic_int16)
            result["mic_audio"] = mic_path
            print(f"   ✅ {mic_path}")
        
        # === Системный звук ===
        if self._sys_audio:
            print(f"💾 Сохраняю системный звук...")
            sys_data = np.concatenate(self._sys_audio)
            if sys_data.ndim > 1:
                sys_data = sys_data.flatten()
            max_val = np.max(np.abs(sys_data))
            if max_val > 0:
                sys_data = sys_data / max_val * 0.9
            sys_int16 = (sys_data * 32767).astype(np.int16)
            wavfile.write(sys_path, self.sys_samplerate, sys_int16)
            result["sys_audio"] = sys_path
            print(f"   ✅ {sys_path}")
        else:
            print("   ⚠️ Системный звук не записан")
        
        return result
