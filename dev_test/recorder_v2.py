"""
Meeting Recorder v2 — Универсальный захват экрана + 2 аудиоканала
- Видео: захват экрана/окна через mss (высокий FPS)
- Аудио 1: Микрофон (голос пользователя = "Я")
- Аудио 2: Системный звук WASAPI Loopback (голос собеседника)
- Выход: .avi + отдельные WAV файлы для транскрибации
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


# ===== Виджет выбора области экрана =====
from PyQt6.QtWidgets import QWidget, QApplication, QRubberBand
from PyQt6.QtCore import Qt, QRect, QPoint
from PyQt6.QtGui import QPainter, QColor, QScreen


class ScreenRegionSelector(QWidget):
    """
    Полноэкранный виджет для выбора области записи мышкой
    """
    
    def __init__(self, callback=None):
        super().__init__()
        self.callback = callback
        self.selection = None
        self.origin = QPoint()
        
        # Полноэкранный полупрозрачный оверлей
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
        
        # Рамка выделения
        self.rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
    
    def paintEvent(self, event):
        painter = QPainter(self)
        # Полупрозрачный тёмный фон
        painter.fillRect(self.rect(), QColor(0, 0, 0, 100))
        
        # Инструкция
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(painter.font())
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
            "\n\n🎯 Выделите область для записи мышкой\nНажмите ESC для отмены"
        )
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.pos()
            self.rubber_band.setGeometry(QRect(self.origin, self.origin))
            self.rubber_band.show()
    
    def mouseMoveEvent(self, event):
        if self.rubber_band.isVisible():
            self.rubber_band.setGeometry(QRect(self.origin, event.pos()).normalized())
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.rubber_band.hide()
            rect = QRect(self.origin, event.pos()).normalized()
            
            # Минимальный размер 100x100
            if rect.width() >= 100 and rect.height() >= 100:
                # Получаем глобальные координаты
                global_rect = {
                    "left": self.geometry().x() + rect.x(),
                    "top": self.geometry().y() + rect.y(),
                    "width": rect.width(),
                    "height": rect.height()
                }
                self.selection = global_rect
                
                if self.callback:
                    self.callback(global_rect)
            
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
    
    def start(self, monitor_id: int = None, region: dict = None, mic_device: int = None, record_system: bool = True):
        """
        Начать запись
        
        Args:
            monitor_id: номер монитора (если region не указан)
            region: {"left": x, "top": y, "width": w, "height": h} - область записи
            mic_device: ID микрофона
            record_system: записывать ли системный звук
        """
        if self.is_recording:
            print("⚠️ Запись уже идёт")
            return False
        
        # Очистка
        self._video_frames = []
        self._mic_audio = []
        self._sys_audio = []
        self._stop_event.clear()
        
        # Настройки области
        if region:
            self.monitor = region
            print(f"▶️ Начинаю запись области: {region['width']}x{region['height']}")
        elif monitor_id:
            self.set_monitor(monitor_id)
            print(f"▶️ Начинаю запись монитора {monitor_id}")
        else:
            self.set_monitor(1)
            print("▶️ Начинаю запись монитора 1")
        
        self.mic_device = mic_device
        self._record_system = record_system
        
        print(f"   Область: {self.monitor}")
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
        """
        Остановить запись и сохранить файлы
        
        Returns:
            dict: {"video": path, "mic_audio": path, "sys_audio": path, "base_name": name}
        """
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
    
    def _save_recording(self) -> dict:
        """
        Сохранить видео и аудио в отдельные файлы
        
        Returns:
            dict: {"video": path, "mic_audio": path, "sys_audio": path}
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"Meeting_{timestamp}"
        
        # Пути к файлам (в папке записей, НЕ временные)
        video_path = str(self.output_dir / f"{base_name}.avi")
        mic_path = str(self.output_dir / f"{base_name}_mic.wav")
        sys_path = str(self.output_dir / f"{base_name}_sys.wav")
        
        result = {"video": None, "mic_audio": None, "sys_audio": None, "base_name": base_name}
        
        # === Сохраняем видео ===
        if self._video_frames:
            print(f"💾 Сохраняю видео ({len(self._video_frames)} кадров)...")
            h, w = self._video_frames[0].shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*'XVID')
            out = cv2.VideoWriter(video_path, fourcc, self.fps, (w, h))
            for frame in self._video_frames:
                out.write(frame)
            out.release()
            result["video"] = video_path
            print(f"   ✅ Видео: {video_path}")
        else:
            print("⚠️ Нет видеокадров")
        
        # === Сохраняем аудио микрофона ===
        if self._mic_audio:
            print(f"💾 Сохраняю аудио микрофона...")
            mic_data = np.concatenate(self._mic_audio)
            # Flatten если нужно
            if mic_data.ndim > 1:
                mic_data = mic_data.flatten()
            # Нормализация
            max_val = np.max(np.abs(mic_data))
            if max_val > 0:
                mic_data = mic_data / max_val
            mic_int16 = (mic_data * 32767).astype(np.int16)
            wavfile.write(mic_path, self.mic_samplerate, mic_int16)
            result["mic_audio"] = mic_path
            print(f"   ✅ Микрофон: {mic_path}")
        else:
            print("⚠️ Нет аудио микрофона")
        
        # === Сохраняем системный звук ===
        if self._sys_audio:
            print(f"💾 Сохраняю системный звук...")
            sys_data = np.concatenate(self._sys_audio)
            if sys_data.ndim > 1:
                sys_data = sys_data.flatten()
            max_val = np.max(np.abs(sys_data))
            if max_val > 0:
                sys_data = sys_data / max_val
            sys_int16 = (sys_data * 32767).astype(np.int16)
            wavfile.write(sys_path, self.sys_samplerate, sys_int16)
            result["sys_audio"] = sys_path
            print(f"   ✅ Системный звук: {sys_path}")
        else:
            print("⚠️ Нет системного звука")
        
        return result
    
    def select_region(self) -> dict:
        """
        Показать диалог выбора области экрана
        
        Returns:
            dict: {"left": x, "top": y, "width": w, "height": h} или None
        """
        selected_region = [None]  # Используем список для замыкания
        
        def on_selected(region):
            selected_region[0] = region
        
        selector = ScreenRegionSelector(callback=on_selected)
        selector.show()
        
        # Ждём пока пользователь выберет область
        while selector.isVisible():
            QApplication.processEvents()
            time.sleep(0.01)
        
        return selected_region[0]


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
