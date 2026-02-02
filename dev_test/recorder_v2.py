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
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QRect, QPoint
from PyQt6.QtGui import QPainter, QColor, QFont, QPen


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
        self._drawing = False
        
        # Флаги окна - важно для правильного отображения поверх всего
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setCursor(Qt.CursorShape.CrossCursor)
        
        # Размер на все мониторы
        self._setup_geometry()
    
    def _setup_geometry(self):
        """Установить размер на весь виртуальный экран (все мониторы)"""
        screens = QApplication.screens()
        if not screens:
            return
        
        # Находим общий прямоугольник всех мониторов
        min_x = min(s.geometry().x() for s in screens)
        min_y = min(s.geometry().y() for s in screens)
        max_x = max(s.geometry().x() + s.geometry().width() for s in screens)
        max_y = max(s.geometry().y() + s.geometry().height() for s in screens)
        
        self._screen_offset_x = min_x
        self._screen_offset_y = min_y
        
        self.setGeometry(min_x, min_y, max_x - min_x, max_y - min_y)
    
    def showFullScreen(self):
        """Показать на весь экран"""
        self._setup_geometry()
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Полупрозрачный тёмный фон
        painter.fillRect(self.rect(), QColor(0, 0, 0, 150))
        
        # Если выделяем область
        if self._drawing and not self.current_rect.isNull() and self.current_rect.width() > 5:
            # Очищаем выделенную область
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(self.current_rect, Qt.GlobalColor.transparent)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            
            # Зелёная рамка
            pen = QPen(QColor(0, 255, 0), 3)
            painter.setPen(pen)
            painter.drawRect(self.current_rect)
            
            # Размер
            size_text = f"{self.current_rect.width()} × {self.current_rect.height()}"
            painter.setFont(QFont("Arial", 16, QFont.Weight.Bold))
            painter.setPen(QColor(255, 255, 0))
            
            text_y = self.current_rect.y() - 10
            if text_y < 25:
                text_y = self.current_rect.bottom() + 25
            painter.drawText(self.current_rect.x() + 5, text_y, size_text)
        
        # Инструкция
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("Arial", 20, QFont.Weight.Bold))
        text = "🎯 ЗАЖМИТЕ левую кнопку мыши и выделите область"
        painter.drawText(self.rect().adjusted(0, 50, 0, 0), 
                        Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter, text)
        
        painter.setFont(QFont("Arial", 14))
        painter.drawText(self.rect().adjusted(0, 90, 0, 0),
                        Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
                        "Нажмите ESC для отмены")
    
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
            
            # Минимальный размер 50x50
            if rect.width() >= 50 and rect.height() >= 50:
                # Глобальные координаты с учётом смещения мониторов
                global_rect = {
                    "left": self._screen_offset_x + rect.x(),
                    "top": self._screen_offset_y + rect.y(),
                    "width": rect.width(),
                    "height": rect.height()
                }
                self.selection = global_rect
                self.hide()
                
                if self.callback:
                    self.callback(global_rect)
            else:
                # Слишком маленькая область - показываем подсказку и продолжаем
                self.current_rect = QRect()
                self.update()
    
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.selection = None
            self.hide()
            if self.callback:
                self.callback(None)


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
            print("⚠️ PyAudio не установлен")
            return None
        
        try:
            p = pyaudio.PyAudio()
            
            # Способ 1: Ищем устройство с isLoopbackDevice (pyaudiowpatch)
            for i in range(p.get_device_count()):
                try:
                    dev = p.get_device_info_by_index(i)
                    if dev.get('isLoopbackDevice', False):
                        print(f"🔊 Найден loopback: {dev['name']}")
                        self._loopback_device = dev
                        p.terminate()
                        return dev
                except:
                    continue
            
            # Способ 2: Ищем WASAPI default speakers и делаем из него loopback
            try:
                wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
                default_speakers_idx = wasapi_info.get('defaultOutputDevice', -1)
                
                if default_speakers_idx >= 0:
                    speakers = p.get_device_info_by_index(default_speakers_idx)
                    print(f"🔊 Default speakers: {speakers['name']}")
                    
                    # Для pyaudiowpatch - ищем loopback версию
                    for i in range(p.get_device_count()):
                        dev = p.get_device_info_by_index(i)
                        # Ищем loopback устройство связанное с динамиками
                        if (dev.get('isLoopbackDevice', False) or 
                            ('loopback' in dev['name'].lower() and 
                             speakers['name'].split()[0] in dev['name'])):
                            print(f"🔊 Loopback для speakers: {dev['name']}")
                            self._loopback_device = dev
                            p.terminate()
                            return dev
                    
                    # Если не нашли loopback, используем speakers напрямую
                    # (работает только с pyaudiowpatch)
                    self._loopback_device = speakers
                    p.terminate()
                    return speakers
            except Exception as e:
                print(f"⚠️ WASAPI: {e}")
            
            p.terminate()
            return None
            
        except Exception as e:
            print(f"⚠️ Ошибка поиска loopback: {e}")
            import traceback
            traceback.print_exc()
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
        """Поток записи микрофона через sounddevice"""
        try:
            print(f"🎤 Запись микрофона: устройство {self.mic_device}, {self.mic_samplerate}Hz")
            
            # Записываем блоками пока не остановят
            chunk_duration = 0.1  # 100ms
            chunk_samples = int(self.mic_samplerate * chunk_duration)
            
            stream = sd.InputStream(
                device=self.mic_device,
                samplerate=self.mic_samplerate,
                channels=1,
                dtype='float32',
                blocksize=chunk_samples
            )
            stream.start()
            
            while not self._stop_event.is_set():
                try:
                    data, overflowed = stream.read(chunk_samples)
                    if overflowed:
                        print("⚠️ Mic overflow")
                    self._mic_audio.append(data.copy())
                except Exception as e:
                    print(f"Mic read: {e}")
                    time.sleep(0.1)
            
            stream.stop()
            stream.close()
            print(f"🎤 Микрофон: записано {len(self._mic_audio)} чанков")
            
        except Exception as e:
            print(f"❌ Ошибка записи микрофона: {e}")
            import traceback
            traceback.print_exc()
    
    def _record_system_audio(self):
        """Поток записи системного звука через PyAudio WASAPI Loopback"""
        if not PYAUDIO_AVAILABLE:
            print("⚠️ PyAudio недоступен для системного звука")
            return
        
        try:
            p = pyaudio.PyAudio()
            
            # Ищем loopback устройство
            loopback_dev = None
            
            # Метод 1: Ищем устройство с isLoopbackDevice
            for i in range(p.get_device_count()):
                try:
                    dev = p.get_device_info_by_index(i)
                    if dev.get('isLoopbackDevice', False):
                        loopback_dev = dev
                        print(f"🔊 Найден loopback: {dev['name']}")
                        break
                except:
                    continue
            
            # Метод 2: Используем default output с loopback=True (pyaudiowpatch)
            if loopback_dev is None:
                try:
                    wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
                    default_idx = wasapi_info.get('defaultOutputDevice', -1)
                    if default_idx >= 0:
                        loopback_dev = p.get_device_info_by_index(default_idx)
                        print(f"🔊 Используем default output: {loopback_dev['name']}")
                except Exception as e:
                    print(f"⚠️ Не удалось получить default output: {e}")
            
            if loopback_dev is None:
                print("❌ Loopback устройство не найдено")
                p.terminate()
                return
            
            # Настройки записи
            channels = max(1, int(loopback_dev.get('maxInputChannels', 2)))
            if channels == 0:
                channels = 2  # Для loopback обычно стерео
            
            rate = int(loopback_dev.get('defaultSampleRate', 44100))
            self.sys_samplerate = rate
            chunk = int(rate * 0.1)  # 100ms
            
            print(f"🔊 Системный звук: {channels}ch, {rate}Hz")
            
            # Открываем поток
            # Для pyaudiowpatch используем специальные параметры
            try:
                stream = p.open(
                    format=pyaudio.paFloat32,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=loopback_dev['index'],
                    frames_per_buffer=chunk,
                    as_loopback=True  # Специальный параметр pyaudiowpatch!
                )
            except TypeError:
                # Если as_loopback не поддерживается (старый pyaudio)
                stream = p.open(
                    format=pyaudio.paFloat32,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=loopback_dev['index'],
                    frames_per_buffer=chunk
                )
            
            print(f"🔊 Поток открыт, записываю...")
            
            while not self._stop_event.is_set():
                try:
                    data = stream.read(chunk, exception_on_overflow=False)
                    audio_data = np.frombuffer(data, dtype=np.float32)
                    
                    # Stereo -> Mono
                    if channels > 1:
                        try:
                            audio_data = audio_data.reshape(-1, channels)
                            audio_data = np.mean(audio_data, axis=1)
                        except:
                            pass
                    
                    self._sys_audio.append(audio_data.astype(np.float32))
                except Exception as e:
                    if not self._stop_event.is_set():
                        print(f"⚠️ Sys read: {e}")
                    time.sleep(0.05)
            
            stream.stop_stream()
            stream.close()
            p.terminate()
            
            print(f"🔊 Системный звук: записано {len(self._sys_audio)} чанков")
            
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
