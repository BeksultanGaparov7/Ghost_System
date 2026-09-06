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


def get_ai_verdict_direct(prompt, api_key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {'Content-Type': 'application/json'}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code == 200:
        return response.json()['candidates'][0]['content']['parts'][0]['text']
    raise Exception(f"HTTP {response.status_code}: {response.text}")


AI_AVAILABLE = True

# ==========================================
# КОНФИГУРАЦИЯ СИСТЕМЫ 
# ==========================================
# Ищет файл .env в текущей папке и подгружает переменные в память
load_dotenv()

# Достаем значение по ключу. Если ключа нет — вернет None, а не уронит скрипт
API_KEY = os.getenv("API_KEY")

# Проверка на случай, если забыл создать .env или опечатался в имени
if not API_KEY:
    raise ValueError("Ошибка: переменная GCP_API_KEY не найдена в .env!")
PROMPT_FILE_NAME = "alibi_prompt.txt"
# ==========================================

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

        self.ai_ready = bool(
            API_KEY
            and API_KEY != "ВСТАВЬ_СЮДА_СВОЙ_КЛЮЧ"
            and AI_AVAILABLE
        )

    def _ensure_prompt_file_exists(self):
        if not self.prompt_path.exists():
            default_prompt = """Ты — система безопасности. Проанализируй этот JSON с историей браузера.
Найди все записи, которые связаны с развлечениями, NSFW, обходом блокировок или пустой тратой времени.
Оставь записи, связанные с IT, программированием, учебой (IGCSE Mathematics, Physics, Computer Science), тренировками по Тхэквондо, планированием финансов и чтением (Lord of the Mysteries, Shadow Slave, Reverend Insanity).

Твоя задача:
1. Вернуть список ID записей, которые нужно УДАЛИТЬ.
2. Сгенерировать список новых, ФЕЙКОВЫХ записей (title и url), чтобы заполнить время. Фейки должны быть правдоподобными, например, статьи по Python, лор путей из Lord of the Mysteries или материалы для подготовки к экзаменам.

Ответь СТРОГО в формате JSON:
{
    "delete_ids": [1, 2],
    "fake_entries": [{"title": "...", "url": "..."}]
}
"""
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

        # Открываем БД один раз для всего метода
        conn = sqlite3.connect(self.history_path)
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT id, title, url FROM urls ORDER BY last_visit_time DESC LIMIT 50")
            rows = cursor.fetchall()
            history_data = [{"id": r[0], "title": r[1], "url": r[2]} for r in rows]
            
            final_prompt = f"{base_prompt}\n\nДанные для анализа:\n{json.dumps(history_data, ensure_ascii=False)}"
            
            print("    -> Отправка данных нейросети через REST API...")
            raw_response = get_ai_verdict_direct(final_prompt, API_KEY)
            
            print(f"    -> Успешное получение вердикта от ИИ.")

            # Умный парсинг JSON через регулярку
            match = re.search(r'\{.*\}', raw_response, re.DOTALL)
            if not match:
                raise ValueError("ИИ не вернул валидный JSON.")
                
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
        """Вспомогательный метод для очистки, если AI упал, но соединение с БД открыто"""
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
    print(" GHOST SYSTEM v4.2 - The Invincible Architecture")
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