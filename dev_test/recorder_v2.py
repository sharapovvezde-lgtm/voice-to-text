"""
Meeting Recorder v2 — Запись экрана со звуком
- Видео: захват выбранной области экрана
- Аудио: микрофон + системный звук  
- Выход: MP4 файл со звуком + отдельные WAV для транскрибации
"""
import os
import sys
import time
import threading
import tempfile
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import cv2
import mss
import sounddevice as sd

# ===== Виджет выбора области экрана =====
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt, QRect, QPoint
from PyQt6.QtGui import QPainter, QColor, QFont, QPen


class ScreenRegionSelector(QWidget):
    """Полноэкранный виджет для выбора области записи"""
    
    def __init__(self, callback=None):
        super().__init__()
        self.callback = callback
        self.selection = None
        self._drawing = False
        
        # Глобальные координаты начала и конца выделения
        self._start_global = QPoint()
        self._end_global = QPoint()
        
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._setup_geometry()
    
    def _setup_geometry(self):
        screens = QApplication.screens()
        if not screens:
            return
        min_x = min(s.geometry().x() for s in screens)
        min_y = min(s.geometry().y() for s in screens)
        max_x = max(s.geometry().x() + s.geometry().width() for s in screens)
        max_y = max(s.geometry().y() + s.geometry().height() for s in screens)
        self._virtual_x = min_x
        self._virtual_y = min_y
        self.setGeometry(min_x, min_y, max_x - min_x, max_y - min_y)
    
    def showFullScreen(self):
        self._setup_geometry()
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
    
    def _global_to_local(self, global_point):
        """Конвертировать глобальные координаты экрана в локальные координаты виджета"""
        return QPoint(global_point.x() - self._virtual_x, global_point.y() - self._virtual_y)
    
    def _get_selection_rect_local(self):
        """Получить прямоугольник выделения в локальных координатах для отрисовки"""
        start_local = self._global_to_local(self._start_global)
        end_local = self._global_to_local(self._end_global)
        return QRect(start_local, end_local).normalized()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 150))
        
        if self._drawing:
            local_rect = self._get_selection_rect_local()
            if local_rect.width() > 5 and local_rect.height() > 5:
                # Очищаем область выделения (делаем прозрачной)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                painter.fillRect(local_rect, Qt.GlobalColor.transparent)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                
                # Зелёная рамка
                pen = QPen(QColor(0, 255, 0), 3)
                painter.setPen(pen)
                painter.drawRect(local_rect)
                
                # Размер и координаты
                size_text = f"{local_rect.width()} × {local_rect.height()}  📍({self._start_global.x()}, {self._start_global.y()})"
                painter.setFont(QFont("Arial", 16, QFont.Weight.Bold))
                painter.setPen(QColor(255, 255, 0))
                text_y = local_rect.y() - 10
                if text_y < 25:
                    text_y = local_rect.bottom() + 25
                painter.drawText(local_rect.x() + 5, text_y, size_text)
        
        # Инструкция
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("Arial", 20, QFont.Weight.Bold))
        painter.drawText(self.rect().adjusted(0, 50, 0, 0), 
                        Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
                        "🎯 ЗАЖМИТЕ левую кнопку мыши и выделите область")
        painter.setFont(QFont("Arial", 14))
        painter.drawText(self.rect().adjusted(0, 90, 0, 0),
                        Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
                        "ESC = отмена")
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Используем глобальные координаты!
            global_pos = event.globalPosition().toPoint()
            self._start_global = global_pos
            self._end_global = global_pos
            self._drawing = True
            self.update()
    
    def mouseMoveEvent(self, event):
        if self._drawing:
            self._end_global = event.globalPosition().toPoint()
            self.update()
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drawing:
            self._drawing = False
            self._end_global = event.globalPosition().toPoint()
            
            # Вычисляем прямоугольник в ГЛОБАЛЬНЫХ координатах экрана
            x1 = min(self._start_global.x(), self._end_global.x())
            y1 = min(self._start_global.y(), self._end_global.y())
            x2 = max(self._start_global.x(), self._end_global.x())
            y2 = max(self._start_global.y(), self._end_global.y())
            
            width = x2 - x1
            height = y2 - y1
            
            if width >= 50 and height >= 50:
                global_rect = {
                    "left": x1,
                    "top": y1,
                    "width": width,
                    "height": height
                }
                print(f"🎯 Выбрана область: left={x1}, top={y1}, width={width}, height={height}")
                self.selection = global_rect
                self.hide()
                if self.callback:
                    self.callback(global_rect)
            else:
                # Слишком маленькая область
                self._start_global = QPoint()
                self._end_global = QPoint()
                self.update()
    
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.selection = None
            self.hide()
            if self.callback:
                self.callback(None)


class MeetingRecorder:
    """Запись встреч: экран + микрофон (+ системный звук)"""
    
    def __init__(self, output_dir: str = None):
        self.output_dir = Path(output_dir) if output_dir else Path("./records")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Настройки
        self.fps = 15
        self.audio_rate = 44100
        self.monitor = None
        self.mic_device = None
        
        # Состояние
        self.is_recording = False
        self._stop_event = threading.Event()
        
        # Буферы
        self._video_frames = []
        self._audio_data = []  # Общий буфер аудио
        
        # Потоки
        self._video_thread = None
        self._audio_thread = None
    
    def get_monitors(self) -> list:
        with mss.mss() as sct:
            monitors = []
            for i, mon in enumerate(sct.monitors):
                if i == 0:
                    continue
                monitors.append({
                    "id": i, "name": f"Монитор {i}",
                    "width": mon["width"], "height": mon["height"],
                    "left": mon["left"], "top": mon["top"]
                })
            return monitors
    
    def get_microphones(self) -> list:
        devices = sd.query_devices()
        mics = []
        for i, dev in enumerate(devices):
            if dev['max_input_channels'] > 0:
                mics.append({
                    "id": i, "name": dev['name'],
                    "channels": dev['max_input_channels'],
                    "is_default": i == sd.default.device[0]
                })
        return mics
    
    def get_loopback_device(self):
        """Проверка наличия системного аудио захвата"""
        try:
            import pyaudiowpatch as pa
            p = pa.PyAudio()
            for i in range(p.get_device_count()):
                dev = p.get_device_info_by_index(i)
                if dev.get('isLoopbackDevice', False):
                    p.terminate()
                    return dev
            p.terminate()
        except:
            pass
        return None
    
    def _record_video(self):
        """Поток записи видео"""
        print(f"📹 Видео: старт (область: {self.monitor})")
        first_frame = True
        with mss.mss() as sct:
            frame_time = 1.0 / self.fps
            while not self._stop_event.is_set():
                start = time.time()
                try:
                    img = sct.grab(self.monitor)
                    frame = np.array(img)
                    if first_frame:
                        print(f"   Первый кадр: {frame.shape[1]}x{frame.shape[0]} px")
                        first_frame = False
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                    self._video_frames.append(frame)
                except Exception as e:
                    print(f"Video err: {e}")
                elapsed = time.time() - start
                if elapsed < frame_time:
                    time.sleep(frame_time - elapsed)
        print(f"📹 Видео: {len(self._video_frames)} кадров")
    
    def _record_audio(self):
        """Поток записи аудио (микрофон)"""
        print(f"🎤 Аудио: старт (устройство {self.mic_device})")
        
        try:
            chunk_samples = int(self.audio_rate * 0.1)  # 100ms
            
            stream = sd.InputStream(
                device=self.mic_device,
                samplerate=self.audio_rate,
                channels=1,
                dtype='int16',
                blocksize=chunk_samples
            )
            stream.start()
            
            while not self._stop_event.is_set():
                try:
                    data, _ = stream.read(chunk_samples)
                    self._audio_data.append(data.copy())
                except Exception as e:
                    print(f"Audio read err: {e}")
                    time.sleep(0.05)
            
            stream.stop()
            stream.close()
            print(f"🎤 Аудио: {len(self._audio_data)} чанков")
            
        except Exception as e:
            print(f"❌ Аудио ошибка: {e}")
            import traceback
            traceback.print_exc()
    
    def start(self, region: dict = None, mic_device: int = None, record_system: bool = True):
        """Начать запись"""
        if self.is_recording:
            return False
        
        if not region:
            print("❌ Область не выбрана!")
            return False
        
        # Очистка
        self._video_frames = []
        self._audio_data = []
        self._stop_event.clear()
        
        self.monitor = region
        self.mic_device = mic_device
        
        print(f"▶️ Запись области: left={region['left']}, top={region['top']}, "
              f"width={region['width']}, height={region['height']}")
        
        self.is_recording = True
        
        # Запуск потоков
        self._video_thread = threading.Thread(target=self._record_video, daemon=True)
        self._audio_thread = threading.Thread(target=self._record_audio, daemon=True)
        
        self._video_thread.start()
        self._audio_thread.start()
        
        return True
    
    def stop(self) -> dict:
        """Остановить и сохранить"""
        if not self.is_recording:
            return None
        
        print("⏹️ Остановка...")
        self._stop_event.set()
        self.is_recording = False
        
        if self._video_thread:
            self._video_thread.join(timeout=3)
        if self._audio_thread:
            self._audio_thread.join(timeout=3)
        
        return self._save_recording()
    
    def _save_recording(self) -> dict:
        """Сохранить видео со звуком"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"Meeting_{timestamp}"
        
        temp_video = str(self.output_dir / f"{base_name}_temp.avi")
        temp_audio = str(self.output_dir / f"{base_name}_mic.wav")
        final_video = str(self.output_dir / f"{base_name}.mp4")
        
        result = {"video": None, "mic_audio": None, "sys_audio": None, "base_name": base_name}
        
        # 1. Сохраняем видео (без звука)
        if self._video_frames:
            print(f"💾 Видео: {len(self._video_frames)} кадров...")
            h, w = self._video_frames[0].shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*'XVID')
            out = cv2.VideoWriter(temp_video, fourcc, self.fps, (w, h))
            for frame in self._video_frames:
                out.write(frame)
            out.release()
            print(f"   ✓ Временное видео: {temp_video}")
        else:
            print("⚠️ Нет видеокадров!")
            return result
        
        # 2. Сохраняем аудио
        if self._audio_data:
            print(f"💾 Аудио: {len(self._audio_data)} чанков...")
            audio_array = np.concatenate(self._audio_data)
            
            # Сохраняем WAV для транскрибации
            with wave.open(temp_audio, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(self.audio_rate)
                wf.writeframes(audio_array.tobytes())
            
            result["mic_audio"] = temp_audio
            print(f"   ✓ Аудио: {temp_audio}")
        else:
            print("⚠️ Нет аудио!")
        
        # 3. Объединяем видео + аудио через FFmpeg
        try:
            print("🎬 Объединяю видео и аудио...")
            
            # Получаем путь к ffmpeg через imageio
            import imageio_ffmpeg
            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
            
            import subprocess
            
            if result["mic_audio"] and os.path.exists(temp_audio):
                # FFmpeg: объединить видео + аудио
                cmd = [
                    ffmpeg_path, '-y',
                    '-i', temp_video,
                    '-i', temp_audio,
                    '-c:v', 'libx264',
                    '-c:a', 'aac',
                    '-b:a', '192k',
                    '-shortest',
                    final_video
                ]
                
                print(f"   Выполняю: ffmpeg ...")
                proc = subprocess.run(cmd, capture_output=True, text=True)
                
                if proc.returncode == 0 and os.path.exists(final_video):
                    result["video"] = final_video
                    print(f"   ✅ Видео со звуком: {final_video}")
                    # Удаляем временный файл
                    if os.path.exists(temp_video):
                        os.remove(temp_video)
                else:
                    print(f"   ⚠️ FFmpeg ошибка: {proc.stderr[:200] if proc.stderr else 'unknown'}")
                    # Оставляем AVI
                    final_avi = str(self.output_dir / f"{base_name}.avi")
                    import shutil
                    shutil.move(temp_video, final_avi)
                    result["video"] = final_avi
            else:
                # Без аудио - просто переименовываем
                final_avi = str(self.output_dir / f"{base_name}.avi")
                import shutil
                shutil.move(temp_video, final_avi)
                result["video"] = final_avi
                print(f"   ✅ Видео (без звука): {final_avi}")
                
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            import traceback
            traceback.print_exc()
            # Fallback - оставляем как есть
            result["video"] = temp_video
        
        return result
