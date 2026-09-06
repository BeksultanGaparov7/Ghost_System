import os
import sys
import shutil
import sqlite3
import json
import time
import platform
import argparse
import subprocess
import re
import requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Импортируем официальный клиент Groq
from groq import Groq

# ==========================================
# КОНФИГУРАЦИЯ СИСТЕМЫ 
# ==========================================
load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not GROQ_API_KEY:
    raise ValueError("Ошибка: переменная GROQ_API_KEY не найдена в .env!")

PROMPT_FILE_NAME = "alibi_prompt.txt"
AI_AVAILABLE = True
# ==========================================

def get_best_groq_model(api_key):
    """
    Эвристический анализатор моделей Groq.
    Динамически оценивает доступные модели по их параметрам и тегам,
    всегда выбирая самую мощную текстовую нейросеть из актуального пула.
    """
    url = "https://api.groq.com/openai/v1/models"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # 1. Стоп-слова для фильтрации мультимодальных и специализированных моделей
    forbidden_keywords = ['whisper', 'vision', 'audio', 'llava', 'embed']
    
    def calculate_model_score(model_id):
        score = 0
        model_name = model_id.lower()
        
        # Извлекаем количество параметров (например, '120' из 'gpt-oss-120b')
        # Чем больше параметров, тем умнее модель и лучше она парсит JSON
        params_match = re.search(r'(\d+)b', model_name)
        if params_match:
            score += int(params_match.group(1)) * 10  # 120b даст 1200 очков, 8b даст 80
            
        # Бонусы за архитектуру и стабильность
        if 'gpt-oss' in model_name:
            score += 500  # Приоритет флагманам OpenAI OSS
        if 'versatile' in model_name:
            score += 100  # Приоритет стабильным универсальным сборкам
        if 'llama' in model_name and '3.3' in model_name:
            score += 200  # Бонус за свежие архитектуры Llama
            
        # Штрафы за тестовые или урезанные версии
        if 'preview' in model_name:
            score -= 300
        if 'instant' in model_name:
            score -= 50
            
        return score

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        models_data = response.json().get("data", [])
        
        # 2. Фильтруем откровенный мусор (аудио, картинки)
        text_models = [
            m["id"] for m in models_data 
            if not any(bad_word in m["id"].lower() for bad_word in forbidden_keywords)
        ]
        
        if not text_models:
            raise ValueError("API не вернул ни одной текстовой модели.")
            
        # 3. Применяем эвристический скоринг
        best_model = max(text_models, key=calculate_model_score)
        
        # Логирование для отладки (в консоль выведет, почему она выбрана)
        # print(f"[DEBUG] Доступные модели: {text_models}")
        # print(f"[DEBUG] Победитель скоринга: {best_model} (Score: {calculate_model_score(best_model)})")
        
        return best_model
        
    except requests.exceptions.RequestException as e:
        print(f"[-] Сетевая ошибка при опросе моделей: {e}.")
        # Жесткий фоллбэк на гарантированно существующую Production-модель
        return "llama-3.3-70b-versatile" 
    except Exception as e:
        print(f"[-] Ошибка логики роутера моделей: {e}.")
        return "llama-3.3-70b-versatile"

def get_ai_verdict_groq(prompt, api_key, model_name):
    """
    Использует клиент Groq для получения ответа. Настроено на строгий JSON.
    """
    client = Groq(api_key=api_key)
    
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": "You are a strict data processing system. You must output ONLY valid JSON. No explanations, no markdown formatting, no conversational text."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.1, # Максимальная детерминированность, нам не нужна креативность в парсинге
        stream=False     # Нам нужен сразу весь JSON для парсинга, стриминг тут усложнит сборку
    )
    
    return response.choices[0].message.content


class ChromeGhostSystem:
    def __init__(self, profile_name=None):
        self.os_name = platform.system()
        self.profile_name = profile_name
        
        self.base_path = self._get_base_path()
        self.profile_path = self._resolve_profile_path()
        self.history_path = self.profile_path / "History"
        self.sessions_path = self.profile_path / "Sessions"
        
        self.vault_dir = Path.cwd() / "Ghost_Vault"
        self.vault_dir.mkdir(exist_ok=True)
        self.chrome_epoch_offset = 11644473600000000

        self.prompt_path = Path.cwd() / PROMPT_FILE_NAME
        self._ensure_prompt_file_exists()

        self.ai_ready = bool(GROQ_API_KEY and AI_AVAILABLE)

    def _ensure_prompt_file_exists(self):
        if not self.prompt_path.exists():
            default_prompt = """Ты — система безопасности. Проанализируй этот JSON с историей браузера.
Найди все записи, которые связаны с развлечениями, NSFW, обходом блокировок или пустой тратой времени.
Оставь записи, связанные с IT, программированием, учебой (IGCSE Mathematics, Physics, Computer Science), тренировками по Тхэквондо, планированием финансов и чтением (Lord of the Mysteries, Shadow Slave, Reverend Insanity).

Твоя задача:
1. Вернуть список ID записей, которые нужно УДАЛИТЬ.
2. Сгенерировать список новых, ФЕЙКОВЫХ записей (title и url), чтобы заполнить время. Фейки должны быть правдоподобными, например, статьи по Python, лор путей из Lord of the Mysteries или материалы для подготовки к экзаменам.

Ответь СТРОГО в формате JSON без markdown-блоков:
{
    "delete_ids": [1, 2],
    "fake_entries": [{"title": "...", "url": "..."}]
}"""
            with open(self.prompt_path, 'w', encoding='utf-8') as f:
                f.write(default_prompt)
            print(f"[*] Сгенерирован внешний файл настроек промпта: {PROMPT_FILE_NAME}")

    def _get_base_path(self) -> Path:
        if self.os_name == "Windows":
            return Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
        elif self.os_name == "Darwin":
            return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
        elif self.os_name == "Linux":
            return Path.home() / ".config" / "google-chrome"
        raise OSError("Unsupported OS")

    def _resolve_profile_path(self) -> Path:
        if not self.base_path.exists():
            print(f"[-] Ошибка: Базовая директория не найдена: {self.base_path}")
            sys.exit(1)

        if self.profile_name:
            target_path = self.base_path / self.profile_name
            if target_path.exists():
                return target_path
            print(f"[!] Профиль {self.profile_name} не найден. Ищу активный...")

        possible_profiles = []
        for directory in self.base_path.iterdir():
            if directory.is_dir() and (directory.name == "Default" or directory.name.startswith("Profile")):
                if (directory / "History").exists():
                    possible_profiles.append(directory)

        if not possible_profiles:
            print("[-] Профили с историей не найдены.")
            sys.exit(1)

        return max(possible_profiles, key=lambda p: os.path.getmtime(p / "History"))

    def execute_order_66(self):
        print("[*] Выполняю перехват процессов Chrome...")
        if self.os_name == "Windows":
            subprocess.run(["taskkill", "/F", "/IM", "chrome.exe", "/T"], capture_output=True)
        else:
            subprocess.run(["pkill", "-f", "chrome"], capture_output=True)
        
        time.sleep(3) 
        print("[+] Chrome ликвидирован. Локи базы данных сняты.")

    def _convert_chrome_time(self, microseconds: int) -> str:
        if not microseconds: return "Unknown"
        try:
            unix_time = (microseconds - self.chrome_epoch_offset) / 1_000_000
            return datetime.fromtimestamp(max(0, unix_time)).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return "Invalid"

    def create_readable_vault(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        raw_backup = self.vault_dir / f"History_Raw_{timestamp}.sqlite"
        shutil.copy2(self.history_path, raw_backup)

        conn = sqlite3.connect(self.history_path)
        cursor = conn.cursor()
        cursor.execute("SELECT u.title, u.url, u.last_visit_time FROM urls u ORDER BY u.last_visit_time DESC LIMIT 1000")
        
        html_path = self.vault_dir / f"Readable_History_{timestamp}.html"
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write("<html><head><style>body{font-family: Arial; background:#1e1e1e; color:#fff;} ")
            f.write("table{width:100%; border-collapse: collapse;} th, td{border:1px solid #444; padding:8px;} ")
            f.write("a{color:#4da6ff; text-decoration:none;}</style></head><body>")
            f.write("<h2>Ghost Vault - История браузера</h2><table><tr><th>Время</th><th>Сайт</th><th>URL</th></tr>")
            
            for title, url, visit_time in cursor.fetchall():
                v_time = self._convert_chrome_time(visit_time)
                safe_title = str(title).replace("<", "&lt;").replace(">", "&gt;") if title else "Без названия"
                f.write(f"<tr><td>{v_time}</td><td>{safe_title}</td><td><a href='{url}'>{url[:80]}...</a></td></tr>")
                
            f.write("</table></body></html>")
            
        conn.close()
        print(f"[+] Читаемый бэкап создан: {html_path.name}")

    def annihilate_sessions(self):
        if self.sessions_path.exists():
            try:
                for file in self.sessions_path.iterdir():
                    if file.is_file():
                        file.unlink()
                print("[+] Меню 'Недавние вкладки' полностью уничтожено.")
            except Exception as e:
                print(f"[-] Ошибка при очистке сессий: {e}")

    def ai_alibi_protocol(self):
        if not self.ai_ready:
            print("[-] Подсистема ИИ не готова. Выполняю обычную зачистку...")
            return self.wipe_and_vacuum()

        print("[*] Активирован протокол AI Alibi. Сборка данных...")
        
        with open(self.prompt_path, 'r', encoding='utf-8') as f:
            base_prompt = f.read()

        conn = sqlite3.connect(self.history_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT id, title, url FROM urls ORDER BY last_visit_time DESC LIMIT 50")
            rows = cursor.fetchall()
            history_data = [{"id": r[0], "title": r[1], "url": r[2]} for r in rows]
            
            final_prompt = f"{base_prompt}\n\nДанные для анализа:\n{json.dumps(history_data, ensure_ascii=False)}"
            
            # 1. Автономно выбираем лучшую модель
            print("    -> Опрос Groq для выбора оптимальной модели...")
            selected_model = get_best_groq_model(GROQ_API_KEY)
            print(f"    -> Выбрана модель: {selected_model}")
            
            # 2. Отправляем запрос через официальный SDK
            print("    -> Отправка данных нейросети...")
            raw_response = get_ai_verdict_groq(final_prompt, GROQ_API_KEY, selected_model)
            print(f"    -> Успешное получение вердикта от ИИ.")

            # Умный парсинг JSON через регулярку (спасает, если модель добавит markdown)
            match = re.search(r'\{.*\}', raw_response, re.DOTALL)
            if not match:
                raise ValueError(f"ИИ не вернул валидный JSON. Ответ:\n{raw_response}")
                
            ai_verdict = json.loads(match.group(0))
            ids_to_delete = ai_verdict.get("delete_ids", [])
            fake_entries = ai_verdict.get("fake_entries", [])
            
            if ids_to_delete:
                placeholders = ','.join('?' for _ in ids_to_delete)
                cursor.execute(f"DELETE FROM urls WHERE id IN ({placeholders})", ids_to_delete)
                cursor.execute(f"DELETE FROM visits WHERE url IN ({placeholders})", ids_to_delete)
                print(f"    -> Уничтожено {len(ids_to_delete)} компрометирующих записей.")
                
            if fake_entries:
                current_chrome_time = int(time.time() * 1000000) + self.chrome_epoch_offset
                for fake in fake_entries:
                    cursor.execute(
                        "INSERT OR IGNORE INTO urls (url, title, visit_count, last_visit_time) VALUES (?, ?, 1, ?)",
                        (fake['url'], fake['title'], current_chrome_time)
                    )
                    cursor.execute("SELECT id FROM urls WHERE url = ?", (fake['url'],))
                    url_id_row = cursor.fetchone()
                    if url_id_row:
                        url_id = url_id_row[0]
                        cursor.execute(
                            "INSERT INTO visits (url, visit_time, transition) VALUES (?, ?, 805306368)",
                            (url_id, current_chrome_time)
                        )
                    current_chrome_time -= 120000000 
                print(f"    -> Внедрено {len(fake_entries)} записей Алиби.")
            
            conn.commit()
            cursor.execute("VACUUM;")
            print("[+] База перестроена. Реконструкция завершена.")
            
        except Exception as e:
            print(f"[-] Критическая ошибка AI протокола: {e}")
            print("[*] Выполняю обычную зачистку как резервный план...")
            self._execute_fallback_wipe(conn, cursor)
            
        finally:
            conn.close()

    def _execute_fallback_wipe(self, conn, cursor):
        tables = ["urls", "visits", "visit_source", "keyword_search_terms", "segments", "segment_usage"]
        try:
            for table in tables:
                cursor.execute(f"DELETE FROM {table};")
            conn.commit()
            cursor.execute("VACUUM;")
            print("[+] Резервная очистка завершена. База пуста.")
        except Exception as e:
            print(f"[-] Ошибка резервной очистки: {e}")

    def wipe_and_vacuum(self):
        print("[*] Выполняю тотальное удаление истории...")
        conn = sqlite3.connect(self.history_path)
        cursor = conn.cursor()
        self._execute_fallback_wipe(conn, cursor)
        conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ghost System - Автономная манипуляция историей Chrome.")
    parser.add_argument("--profile", type=str, help="Имя папки профиля (например, 'Profile 5').")
    parser.add_argument("--wipe-all", action="store_true", help="Принудительно удалить всё, игнорируя ИИ.")
    
    args = parser.parse_args()

    print("="*60)
    print(" GHOST SYSTEM v4.3 - Groq Autonomous Integration")
    print("="*60)
    
    ghost = ChromeGhostSystem(profile_name=args.profile)
    ghost.execute_order_66()
    ghost.create_readable_vault()
    ghost.annihilate_sessions()
    
    if args.wipe_all:
        ghost.wipe_and_vacuum()
    else:
        ghost.ai_alibi_protocol()
        
    print("="*60)
    print("[+] Операция завершена.")