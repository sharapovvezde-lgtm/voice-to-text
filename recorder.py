# recorder.py - Логика захвата звука с микрофона

import numpy as np
import sounddevice as sd
from scipy.io import wavfile
import threading
import io
from typing import Optional, List, Tuple


class AudioRecorder:
    """Класс для записи аудио с микрофона"""
    
    SAMPLE_RATE = 16000  # 16kHz для Whisper
    CHANNELS = 1  # Mono
    DTYPE = np.float32
    
    def __init__(self, device_id: Optional[int] = None):
        self.device_id = device_id
        self.is_recording = False
        self.audio_buffer: List[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream: Optional[sd.InputStream] = None
    
    def _audio_callback(self, indata: np.ndarray, frames: int, 
                        time_info, status) -> None:
        """Callback для записи аудио в буфер"""
        if status:
            print(f"Audio status: {status}")
        
        if self.is_recording:
            with self._lock:
                self.audio_buffer.append(indata.copy())
    
    def start_recording(self) -> bool:
        """Начинает запись аудио"""
        if self.is_recording:
            return False
        
        try:
            self.audio_buffer = []
            self.is_recording = True
            
            self._stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype=self.DTYPE,
                device=self.device_id,
                callback=self._audio_callback,
                blocksize=1024
            )
            self._stream.start()
            return True
            
        except Exception as e:
            print(f"Ошибка начала записи: {e}")
            self.is_recording = False
            return False
    
    def stop_recording(self) -> Optional[np.ndarray]:
        """
        Останавливает запись и возвращает записанное аудио.
        Возвращает numpy array с аудио данными или None при ошибке.
        """
        if not self.is_recording:
            return None
        
        self.is_recording = False
        
        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            
            with self._lock:
                if not self.audio_buffer:
                    return None
                
                # Объединяем все буферы в один массив
                audio_data = np.concatenate(self.audio_buffer, axis=0)
                self.audio_buffer = []
                
                # Убираем лишнее измерение если есть
                if audio_data.ndim > 1:
                    audio_data = audio_data.flatten()
                
                return audio_data
                
        except Exception as e:
            print(f"Ошибка остановки записи: {e}")
            return None
    
    def get_audio_duration(self, audio_data: np.ndarray) -> float:
        """Возвращает длительность аудио в секундах"""
        return len(audio_data) / self.SAMPLE_RATE
    
    @staticmethod
    def get_microphones() -> List[Tuple[int, str]]:
        """
        Возвращает список доступных микрофонов.
        Формат: [(device_id, device_name), ...]
        """
        devices = []
        try:
            device_list = sd.query_devices()
            for i, device in enumerate(device_list):
                # Фильтруем только устройства ввода
                if device['max_input_channels'] > 0:
                    devices.append((i, device['name']))
        except Exception as e:
            print(f"Ошибка получения списка микрофонов: {e}")
        
        return devices
    
    @staticmethod
    def get_default_microphone() -> Optional[int]:
        """Возвращает ID микрофона по умолчанию"""
        try:
            device_info = sd.query_devices(kind='input')
            return sd.default.device[0]
        except Exception:
            return None
    
    def set_device(self, device_id: Optional[int]) -> None:
        """Устанавливает устройство записи"""
        self.device_id = device_id


# Пример использования
if __name__ == "__main__":
    print("Доступные микрофоны:")
    for device_id, name in AudioRecorder.get_microphones():
        print(f"  [{device_id}] {name}")
    
    print("\nТест записи (3 секунды)...")
    recorder = AudioRecorder()
    
    import time
    recorder.start_recording()
    time.sleep(3)
    audio = recorder.stop_recording()
    
    if audio is not None:
        duration = recorder.get_audio_duration(audio)
        print(f"Записано: {duration:.2f} секунд, {len(audio)} сэмплов")
    else:
        print("Ошибка записи")
