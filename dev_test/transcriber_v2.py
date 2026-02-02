"""
Meeting Transcriber v2 — Транскрибация с разделением спикеров
- Аудио 1 (микрофон) → "Я"
- Аудио 2 (системный) → "Собеседник"
- Выход: текстовый отчёт с таймкодами
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from datetime import datetime
import tempfile

import numpy as np
from scipy.io import wavfile

# Whisper
try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    print("⚠️ openai-whisper не установлен")


def format_timestamp(seconds: float) -> str:
    """Форматирование времени: MM:SS или HH:MM:SS"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class MeetingTranscriber:
    """
    Транскрибатор встреч с разделением спикеров
    """
    
    def __init__(self, model_name: str = "base"):
        self.model_name = model_name
        self.model = None
        
    def load_model(self):
        """Загрузить модель Whisper"""
        if not WHISPER_AVAILABLE:
            raise RuntimeError("openai-whisper не установлен!")
        
        if self.model is None:
            print(f"📥 Загружаю модель Whisper '{self.model_name}'...")
            self.model = whisper.load_model(self.model_name)
            print("✅ Модель загружена")
        
        return self.model
    
    def transcribe_audio(self, audio_path: str, language: str = "ru") -> list:
        """
        Транскрибировать аудиофайл с таймкодами
        
        Returns:
            list of dict: [{"start": 0.0, "end": 2.5, "text": "Привет"}]
        """
        self.load_model()
        
        print(f"🔄 Транскрибирую: {audio_path}")
        
        result = self.model.transcribe(
            audio_path,
            language=language,
            task="transcribe",
            verbose=False
        )
        
        segments = []
        for seg in result.get("segments", []):
            segments.append({
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"].strip()
            })
        
        return segments
    
    def transcribe_meeting(
        self,
        mic_audio_path: str = None,
        sys_audio_path: str = None,
        language: str = "ru"
    ) -> dict:
        """
        Транскрибировать встречу с разделением спикеров
        
        Args:
            mic_audio_path: путь к WAV микрофона ("Я")
            sys_audio_path: путь к WAV системного звука ("Собеседник")
            language: язык для распознавания
        
        Returns:
            dict: {"segments": [...], "full_text": "..."}
        """
        all_segments = []
        
        # Транскрибируем микрофон ("Я")
        if mic_audio_path and os.path.exists(mic_audio_path):
            print("\n🎤 Транскрибирую микрофон (Я)...")
            try:
                mic_segments = self.transcribe_audio(mic_audio_path, language)
                for seg in mic_segments:
                    seg["speaker"] = "Я"
                all_segments.extend(mic_segments)
                print(f"   ✅ Найдено {len(mic_segments)} сегментов")
            except Exception as e:
                print(f"   ❌ Ошибка: {e}")
        else:
            print(f"⚠️ Файл микрофона не найден: {mic_audio_path}")
        
        # Транскрибируем системный звук ("Собеседник")
        if sys_audio_path and os.path.exists(sys_audio_path):
            print("\n🔊 Транскрибирую системный звук (Собеседник)...")
            try:
                sys_segments = self.transcribe_audio(sys_audio_path, language)
                for seg in sys_segments:
                    seg["speaker"] = "Собеседник"
                all_segments.extend(sys_segments)
                print(f"   ✅ Найдено {len(sys_segments)} сегментов")
            except Exception as e:
                print(f"   ❌ Ошибка: {e}")
        else:
            if sys_audio_path:
                print(f"⚠️ Файл системного звука не найден: {sys_audio_path}")
        
        if not all_segments:
            print("⚠️ Не найдено ни одного сегмента для транскрибации")
            return {
                "segments": [],
                "full_text": "(Пусто - речь не распознана)"
            }
        
        # Сортируем по времени
        all_segments.sort(key=lambda x: x["start"])
        
        # Объединяем близкие сегменты одного спикера
        merged_segments = self._merge_segments(all_segments)
        
        # Формируем полный текст
        full_text = self._format_transcript(merged_segments)
        
        return {
            "segments": merged_segments,
            "full_text": full_text
        }
    
    def _merge_segments(self, segments: list, gap_threshold: float = 1.0) -> list:
        """
        Объединить близкие сегменты одного спикера
        """
        if not segments:
            return []
        
        merged = []
        current = segments[0].copy()
        
        for seg in segments[1:]:
            # Если тот же спикер и маленький промежуток - объединяем
            if (seg["speaker"] == current["speaker"] and 
                seg["start"] - current["end"] < gap_threshold):
                current["end"] = seg["end"]
                current["text"] += " " + seg["text"]
            else:
                merged.append(current)
                current = seg.copy()
        
        merged.append(current)
        return merged
    
    def _format_transcript(self, segments: list) -> str:
        """Форматировать транскрипт в читаемый текст"""
        lines = []
        for seg in segments:
            ts = format_timestamp(seg["start"])
            speaker = seg["speaker"]
            text = seg["text"]
            lines.append(f"[{ts}] {speaker}: {text}")
        
        return "\n".join(lines)
    
    def save_report(
        self,
        transcript: dict,
        output_path: str = None,
        video_path: str = None
    ) -> str:
        """
        Сохранить отчёт о встрече
        
        Args:
            transcript: результат transcribe_meeting()
            output_path: путь для сохранения (опционально)
            video_path: путь к видео (для имени файла)
        
        Returns:
            путь к сохранённому файлу
        """
        if not output_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if video_path:
                base = Path(video_path).stem
                output_path = str(Path(video_path).parent / f"{base}_transcript.txt")
            else:
                output_path = f"Meeting_Report_{timestamp}.txt"
        
        # Формируем отчёт
        report_lines = [
            "=" * 60,
            "📋 ОТЧЁТ О ВСТРЕЧЕ",
            f"📅 Дата: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "=" * 60,
            "",
            "📝 ТРАНСКРИПЦИЯ:",
            "-" * 40,
            "",
            transcript["full_text"],
            "",
            "-" * 40,
            f"📊 Всего сегментов: {len(transcript['segments'])}",
            "=" * 60
        ]
        
        report_text = "\n".join(report_lines)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report_text)
        
        print(f"📄 Отчёт сохранён: {output_path}")
        return output_path


# ===== Тестовый запуск =====
if __name__ == "__main__":
    print("\n" + "="*60)
    print("🔬 ТЕСТ MeetingTranscriber")
    print("="*60)
    
    transcriber = MeetingTranscriber(model_name="base")
    
    # Пример использования
    print("""
Примеры использования:

1. Транскрибация видео с двумя аудиодорожками:
   result = transcriber.transcribe_meeting(video_path="meeting.mp4")
   transcriber.save_report(result, "report.txt")

2. Транскрибация отдельных аудиофайлов:
   result = transcriber.transcribe_meeting(
       mic_audio_path="mic.wav",
       sys_audio_path="system.wav"
   )

3. Ожидаемый формат вывода:
   [00:00] Я: Всем привет
   [00:03] Собеседник: Здравствуйте
   [00:08] Я: Начнём встречу
""")
    
    # Тест загрузки модели
    if WHISPER_AVAILABLE:
        print("\nЗагружаю модель для теста...")
        try:
            transcriber.load_model()
            print("✅ Модель загружена успешно!")
        except Exception as e:
            print(f"❌ Ошибка: {e}")
    else:
        print("❌ Whisper не установлен")
    
    input("\nНажмите Enter для выхода...")
