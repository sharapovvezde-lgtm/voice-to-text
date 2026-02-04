"""
Meeting Recorder v2 — Запись экрана со звуком
- Видео: захват ТОЧНОЙ выбранной области экрана
- Аудио: только микрофон (безопасно, без системного захвата)
- Выход: MP4 со звуком
"""
import os
import sys
import time
import threading
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
        return QPoint(global_point.x() - self._virtual_x, global_point.y() - self._virtual_y)
    
    def _get_selection_rect_local(self):
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
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                painter.fillRect(local_rect, Qt.GlobalColor.transparent)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
                
                pen = QPen(QColor(0, 255, 0), 3)
                painter.setPen(pen)
                painter.drawRect(local_rect)
                
                size_text = f"{local_rect.width()} × {local_rect.height()}"
                painter.setFont(QFont("Arial", 16, QFont.Weight.Bold))
                painter.setPen(QColor(255, 255, 0))
                text_y = local_rect.y() - 10
                if text_y < 25:
                    text_y = local_rect.bottom() + 25
                painter.drawText(local_rect.x() + 5, text_y, size_text)
        
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("Arial", 20, QFont.Weight.Bold))
        painter.drawText(self.rect().adjusted(0, 50, 0, 0), 
                        Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
                        "🎯 ЗАЖМИТЕ мышь и выделите область")
        painter.setFont(QFont("Arial", 14))
        painter.drawText(self.rect().adjusted(0, 90, 0, 0),
                        Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter,
                        "ESC = отмена")
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
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
            
            x1 = min(self._start_global.x(), self._end_global.x())
            y1 = min(self._start_global.y(), self._end_global.y())
            x2 = max(self._start_global.x(), self._end_global.x())
            y2 = max(self._start_global.y(), self._end_global.y())
            
            width = x2 - x1
            height = y2 - y1
            
            if width >= 50 and height >= 50:
                # Возвращаем координаты как есть
                global_rect = {
                    "left": x1,
                    "top": y1,
                    "width": width,
                    "height": height
                }
                print(f"🎯 Выбрана область: x={x1}, y={y1}, w={width}, h={height}")
                self.selection = global_rect
                self.hide()
                if self.callback:
                    self.callback(global_rect)
            else:
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
    """Запись встреч: экран + микрофон"""
    
    def __init__(self, output_dir: str = None):
        self.output_dir = Path(output_dir) if output_dir else Path("./records")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.fps = 15
        self.audio_rate = 44100
        self.region = None
        self.mic_device = None
        
        self.is_recording = False
        self._stop_event = threading.Event()
        
        self._video_frames = []
        self._audio_data = []
        
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
        """Отключено - возвращает None"""
        return None
    
    def _record_video(self):
        """Поток записи видео"""
        print(f"📹 Видео: старт")
        
        # Используем сохранённые координаты
        left = self.region["left"]
        top = self.region["top"]
        width = self.region["width"]
        height = self.region["height"]
        
        print(f"   Координаты для mss: left={left}, top={top}, width={width}, height={height}")
        
        first_frame = True
        with mss.mss() as sct:
            frame_time = 1.0 / self.fps
            
            while not self._stop_event.is_set():
                start = time.time()
                try:
                    # Захватываем область напрямую через словарь
                    monitor = {"left": left, "top": top, "width": width, "height": height}
                    img = sct.grab(monitor)
                    frame = np.array(img)
                    
                    if first_frame:
                        actual_h, actual_w = frame.shape[:2]
                        print(f"   Размер кадра: {actual_w}x{actual_h}")
                        if actual_w != width or actual_h != height:
                            print(f"   ⚠️ Размер не совпадает с запросом!")
                        first_frame = False
                    
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                    self._video_frames.append(frame)
                except Exception as e:
                    print(f"   Ошибка: {e}")
                
                elapsed = time.time() - start
                if elapsed < frame_time:
                    time.sleep(frame_time - elapsed)
        
        print(f"📹 Видео: {len(self._video_frames)} кадров")
    
    def _record_audio(self):
        """Поток записи микрофона"""
        print(f"🎤 Микрофон: старт")
        
        try:
            chunk = int(self.audio_rate * 0.1)
            
            stream = sd.InputStream(
                device=self.mic_device,
                samplerate=self.audio_rate,
                channels=1,
                dtype='int16',
                blocksize=chunk
            )
            stream.start()
            
            while not self._stop_event.is_set():
                try:
                    data, _ = stream.read(chunk)
                    self._audio_data.append(data.flatten().copy())
                except:
                    time.sleep(0.05)
            
            stream.stop()
            stream.close()
            print(f"🎤 Микрофон: {len(self._audio_data)} чанков")
            
        except Exception as e:
            print(f"❌ Микрофон ошибка: {e}")
    
    def start(self, region: dict = None, mic_device: int = None, record_system: bool = False):
        """Начать запись"""
        if self.is_recording:
            return False
        
        if not region:
            print("❌ Область не выбрана!")
            return False
        
        self._video_frames = []
        self._audio_data = []
        self._stop_event.clear()
        
        # Сохраняем координаты
        self.region = {
            "left": int(region["left"]),
            "top": int(region["top"]),
            "width": int(region["width"]),
            "height": int(region["height"])
        }
        self.mic_device = mic_device
        
        print(f"▶️ ЗАПИСЬ: x={self.region['left']}, y={self.region['top']}, "
              f"w={self.region['width']}, h={self.region['height']}")
        
        self.is_recording = True
        
        self._video_thread = threading.Thread(target=self._record_video, daemon=True)
        self._audio_thread = threading.Thread(target=self._record_audio, daemon=True)
        
        self._video_thread.start()
        self._audio_thread.start()
        
        return True
    
    def stop(self) -> dict:
        """Остановить"""
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
        """Сохранить"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"Meeting_{timestamp}"
        
        temp_video = str(self.output_dir / f"{base_name}_temp.avi")
        audio_path = str(self.output_dir / f"{base_name}_mic.wav")
        final_video = str(self.output_dir / f"{base_name}.mp4")
        
        result = {"video": None, "mic_audio": None, "sys_audio": None, "base_name": base_name}
        
        # 1. Видео
        if self._video_frames:
            print(f"💾 Видео: {len(self._video_frames)} кадров...")
            h, w = self._video_frames[0].shape[:2]
            print(f"   Размер: {w}x{h}")
            fourcc = cv2.VideoWriter_fourcc(*'XVID')
            out = cv2.VideoWriter(temp_video, fourcc, self.fps, (w, h))
            for frame in self._video_frames:
                out.write(frame)
            out.release()
            print(f"   ✓ {temp_video}")
        else:
            print("⚠️ Нет кадров!")
            return result
        
        # 2. Аудио
        if self._audio_data:
            print(f"💾 Аудио: {len(self._audio_data)} чанков...")
            audio = np.concatenate(self._audio_data)
            
            with wave.open(audio_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.audio_rate)
                wf.writeframes(audio.tobytes())
            
            result["mic_audio"] = audio_path
            print(f"   ✓ {audio_path}")
        
        # 3. Объединяем
        try:
            print("🎬 Объединяю...")
            
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            import subprocess
            
            if result["mic_audio"]:
                cmd = [
                    ffmpeg, '-y',
                    '-i', temp_video,
                    '-i', audio_path,
                    '-c:v', 'libx264',
                    '-c:a', 'aac',
                    '-shortest',
                    final_video
                ]
                
                proc = subprocess.run(cmd, capture_output=True)
                
                if proc.returncode == 0 and os.path.exists(final_video):
                    result["video"] = final_video
                    print(f"   ✅ {final_video}")
                    os.remove(temp_video)
                else:
                    import shutil
                    final_avi = str(self.output_dir / f"{base_name}.avi")
                    shutil.move(temp_video, final_avi)
                    result["video"] = final_avi
            else:
                import shutil
                final_avi = str(self.output_dir / f"{base_name}.avi")
                shutil.move(temp_video, final_avi)
                result["video"] = final_avi
                
        except Exception as e:
            print(f"❌ {e}")
            result["video"] = temp_video
        
        return result
