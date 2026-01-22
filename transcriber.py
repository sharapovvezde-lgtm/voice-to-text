# transcriber.py - Инициализация Whisper и обработка аудио
# Использует openai-whisper (работает на Python 3.14)

import numpy as np
from typing import Optional, Tuple
import threading
import torch


class WhisperTranscriber:
    """Класс для транскрибации аудио с помощью OpenAI Whisper"""
    
    def __init__(self):
        self.model = None
        self.model_name: Optional[str] = None
        self._lock = threading.Lock()
        self._loading = False
        
        # Определяем устройство
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"Whisper будет использовать: {self.device}")
    
    def load_model(self, model_size: str = "base") -> bool:
        """
        Загружает модель Whisper.
        
        Args:
            model_size: Размер модели (tiny, base, small, medium, large)
        
        Returns:
            True при успешной загрузке
        """
        if self._loading:
            return False
        
        self._loading = True
        
        try:
            import whisper
            
            print(f"Загрузка модели '{model_size}' на {self.device}...")
            
            with self._lock:
                self.model = whisper.load_model(model_size, device=self.device)
                self.model_name = model_size
            
            print("Модель загружена успешно!")
            return True
            
        except Exception as e:
            print(f"Ошибка загрузки модели: {e}")
            return False
        
        finally:
            self._loading = False
    
    def transcribe(self, audio_data: np.ndarray, 
                   language: Optional[str] = None) -> Tuple[str, float]:
        """
        Транскрибирует аудио в текст.
        """
        if self.model is None:
            print("Модель не загружена!")
            return "", 0.0
        
        try:
            with self._lock:
                # Нормализуем аудио
                if audio_data.dtype != np.float32:
                    audio_data = audio_data.astype(np.float32)
                
                # Информация об аудио
                max_val = np.abs(audio_data).max()
                print(f"Аудио: длина={len(audio_data)}, max={max_val:.4f}")
                
                # Нормализуем
                if max_val > 0:
                    audio_data = audio_data / max(max_val, 0.001)
                
                # Транскрибация (без указания языка для автоопределения)
                result = self.model.transcribe(
                    audio_data,
                    fp16=False,
                    verbose=True
                )
                
                text = result.get("text", "").strip()
                print(f"Результат: '{text}'")
                
                return text, 1.0
                
        except Exception as e:
            print(f"Ошибка транскрибации: {e}")
            import traceback
            traceback.print_exc()
            return "", 0.0
    
    def is_model_loaded(self) -> bool:
        """Проверяет, загружена ли модель"""
        return self.model is not None
    
    def get_model_name(self) -> Optional[str]:
        """Возвращает имя текущей модели"""
        return self.model_name
    
    def unload_model(self) -> None:
        """Выгружает модель из памяти"""
        with self._lock:
            if self.model is not None:
                del self.model
                self.model = None
            self.model_name = None
            
            # Очищаем кэш CUDA если используется
            if torch.cuda.is_available():
                torch.cuda.empty_cache()


# Singleton instance для глобального доступа
_transcriber_instance: Optional[WhisperTranscriber] = None


def get_transcriber() -> WhisperTranscriber:
    """Возвращает глобальный экземпляр транскрайбера"""
    global _transcriber_instance
    if _transcriber_instance is None:
        _transcriber_instance = WhisperTranscriber()
    return _transcriber_instance


# Пример использования
if __name__ == "__main__":
    import time
    
    transcriber = get_transcriber()
    
    print("Загрузка модели 'base'...")
    start = time.time()
    success = transcriber.load_model("base")
    print(f"Время загрузки: {time.time() - start:.2f}с")
    
    if success:
        # Тест с тишиной
        test_audio = np.zeros(16000 * 2, dtype=np.float32)  # 2 секунды тишины
        text, prob = transcriber.transcribe(test_audio)
        print(f"Результат теста: '{text}' (вероятность: {prob:.2f})")
