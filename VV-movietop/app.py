import streamlit as st
import streamlit.components.v1 as components
import json
import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import sqlite3
import io

# Настройки страницы для расширения границ интерфейса (Широкий экран)
st.set_page_config(
    page_title="VVMC Club",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------- РЕЗЕРВНОЕ КОПИРОВАНИЕ НА GOOGLE DRIVE -----------------
DB_FILE = "vvmc_club.db"
BG_DIR = "backgrounds"

def get_gdrive_service():
    """Авторизует и возвращает клиент Google Drive API."""
    if "gdrive" in st.secrets and "service_account" in st.secrets["gdrive"]:
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            
            creds_info = dict(st.secrets["gdrive"]["service_account"])
            if "private_key" in creds_info:
                creds_info["private_key"] = creds_info["private_key"].replace("\\n", "\n")
                
            creds = service_account.Credentials.from_service_account_info(
                creds_info, 
                scopes=["https://www.googleapis.com/auth/drive"]
            )
            return build('drive', 'v3', credentials=creds)
        except Exception as e:
            st.sidebar.error(f"Ошибка авторизации Google Drive: {e}")
    return None

def find_db_on_gdrive(service, folder_id):
    """Ищет файл базы данных в указанной папке Google Drive."""
    try:
        query = f"name = '{DB_FILE}' and '{folder_id}' in parents and trashed = false"
        results = service.files().list(
            q=query, 
            spaces='drive', 
            fields='files(id, name)'
        ).execute()
        files = results.get('files', [])
        if files:
            return files[0]['id']
    except Exception as e:
        st.sidebar.error(f"Ошибка поиска файла на Google Диске: {e}")
    return None

def download_db_from_cloud():
    """Загружает базу данных из Google Drive при старте приложения."""
    service = get_gdrive_service()
    if service and "gdrive" in st.secrets:
        folder_id = st.secrets["gdrive"].get("folder_id")
        if not folder_id:
            st.sidebar.warning("⚠️ Не указан folder_id в secrets[gdrive]!")
            return
            
        file_id = find_db_on_gdrive(service, folder_id)
        if file_id:
            try:
                from googleapiclient.http import MediaIoBaseDownload
                request = service.files().get_media(fileId=file_id)
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                
                with open(DB_FILE, "wb") as f:
                    f.write(fh.getvalue())
                st.sidebar.success("🔄 База успешно скачана с Google Диска!")
            except Exception as e:
                st.sidebar.error(f"Ошибка скачивания базы с Google Диска: {e}")
        else:
            st.sidebar.info("Файл базы данных не найден на Google Диске. Будет создан новый при изменениях.")

def upload_db_to_cloud():
    """Загружает (или обновляет) локальную базу данных на Google Drive."""
    service = get_gdrive_service()
    if service and "gdrive" in st.secrets and os.path.exists(DB_FILE):
        folder_id = st.secrets["gdrive"].get("folder_id")
        if not folder_id:
            return
            
        from googleapiclient.http import MediaFileUpload
        file_id = find_db_on_gdrive(service, folder_id)
        media = MediaFileUpload(DB_FILE, mimetype='application/x-sqlite3', resumable=True)
        
        try:
            if file_id:
                # Если файл уже существует, обновляем его содержимое
                service.files().update(
                    fileId=file_id,
                    media_body=media
                ).execute()
            else:
                # Если файла нет, создаем новый в указанной папке
                file_metadata = {
                    'name': DB_FILE,
                    'parents': [folder_id]
                }
                service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id'
                ).execute()
        except Exception as e:
            st.sidebar.error(f"Не удалось сохранить бэкап на Google Диск: {e}")

# ----------------- БАЗА ДАННЫХ SQLITE -----------------

def get_db_conn():
    """Создает и возвращает подключение к базе данных SQLite."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON") # Включаем поддержку каскадного удаления
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Инициализация таблиц базы данных."""
    # Пытаемся сначала скачать актуальную копию из Google Диска
    if "db_downloaded" not in st.session_state:
        download_db_from_cloud()
        st.session_state["db_downloaded"] = True

    with get_db_conn() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password TEXT,
                is_telegram INTEGER DEFAULT 0
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS movies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                poster TEXT,
                status TEXT,
                added_by TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS ratings (
                movie_id INTEGER,
                username TEXT,
                score INTEGER,
                quip TEXT,
                rating_date TEXT,
                PRIMARY KEY (movie_id, username),
                FOREIGN KEY(movie_id) REFERENCES movies(id) ON DELETE CASCADE,
                FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE
            )
        ''')
        conn.commit()

# --- Вспомогательные функции для работы с БД ---

def get_users_dict():
    with get_db_conn() as conn:
        return {row['username']: row['password'] for row in conn.execute('SELECT username, password FROM users')}

def add_user(username, password, is_telegram=0):
    with get_db_conn() as conn:
        conn.execute('INSERT OR REPLACE INTO users (username, password, is_telegram) VALUES (?, ?, ?)',
                     (username, password, is_telegram))
        conn.commit()
    upload_db_to_cloud() # Синхронизируем изменения с Google Диском

def get_movies():
    """Возвращает список фильмов со словарем оценок."""
    movies_list = []
    with get_db_conn() as conn:
        m_rows = conn.execute('SELECT * FROM movies').fetchall()
        for m in m_rows:
            movie = dict(m)
            movie['ratings'] = {}
            r_rows = conn.execute('SELECT username, score, quip, rating_date FROM ratings WHERE movie_id = ?', (m['id'],))
            for r in r_rows:
                movie['ratings'][r['username']] = {
                    "score": r['score'],
                    "quip": r['quip'],
                    "date": r['rating_date']
                }
            movies_list.append(movie)
    return movies_list

def add_movie(title, poster, status, added_by):
    with get_db_conn() as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO movies (title, poster, status, added_by) VALUES (?, ?, ?, ?)',
                       (title, poster, status, added_by))
        conn.commit()
        last_id = cursor.lastrowid
    upload_db_to_cloud() # Синхронизируем изменения с Google Диском
    return last_id

def update_movie_status(movie_id, status):
    with get_db_conn() as conn:
        conn.execute('UPDATE movies SET status = ? WHERE id = ?', (status, movie_id))
        conn.commit()
    upload_db_to_cloud() # Синхронизируем изменения с Google Диском

def delete_movie(movie_id):
    with get_db_conn() as conn:
        conn.execute('DELETE FROM movies WHERE id = ?', (movie_id,))
        conn.commit()
    upload_db_to_cloud() # Синхронизируем изменения с Google Диском

def set_rating(movie_id, username, score, quip, date):
    with get_db_conn() as conn:
        conn.execute('''
            INSERT INTO ratings (movie_id, username, score, quip, rating_date)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(movie_id, username) DO UPDATE SET
                score=excluded.score,
                quip=excluded.quip,
                rating_date=excluded.rating_date
        ''', (movie_id, username, score, quip, date))
        conn.commit()
    upload_db_to_cloud() # Синхронизируем изменения с Google Диском

# --- Миграция со старых JSON ---
def migrate_json_to_sqlite():
    """Одноразовый перенос данных из старых JSON-файлов в SQLite."""
    if not os.path.exists("migrated.flag"):
        if os.path.exists("users.json"):
            try:
                with open("users.json", "r", encoding="utf-8") as f:
                    users_data = json.load(f)
                    for u, p in users_data.items():
                        add_user(u, p, 1 if p == "telegram_auto_pass" else 0)
            except Exception: pass

        if os.path.exists("movies.json"):
            try:
                with open("movies.json", "r", encoding="utf-8") as f:
                    movies_data = json.load(f)
                    for m in movies_data:
                        m_id = add_movie(m.get("title", "Без названия"), m.get("poster"), m.get("status", "watched"), m.get("added_by", "admin"))
                        
                        for u, r_data in m.get("ratings", {}).items():
                            if isinstance(r_data, (int, float)):
                                score = int(r_data * 10) if r_data <= 10.0 else int(r_data)
                                set_rating(m_id, u, max(1, min(100, score)), "", datetime.now().strftime("%Y-%m-%d %H:%M"))
                            elif isinstance(r_data, dict):
                                set_rating(m_id, u, r_data.get("score", 50), r_data.get("quip", ""), r_data.get("date", datetime.now().strftime("%Y-%m-%d %H:%M")))
            except Exception: pass
            
        with open("migrated.flag", "w") as f:
            f.write("Данные перенесены в SQLite.")
        upload_db_to_cloud()

# Инициализация и миграция баз данных
init_db()
migrate_json_to_sqlite()

# Загружаем актуальные данные из SQLite
users_db = get_users_dict()
movies = get_movies()

# Определение фонов
if "bg_choice" not in st.session_state: 
    st.session_state["bg_choice"] = "Стандартный"
    
bg_files = ["Стандартный"]
if os.path.exists(BG_DIR):
    bg_files += [f for f in os.listdir(BG_DIR) if f.lower().endswith(('.png', '.jpg', '.jpeg'))]

# Кастомные стили (высокотехнологичный темный дизайн, бирюзовые акценты)
st.markdown("""
<style>
    .stApp { background-color: #0b0f19; color: #f1f5f9; }
    .block-container { padding-top: 1rem !important; padding-bottom: 2rem !important; max-width: 95% !important; }
    h1, h2, h3 { color: #f8fafc !important; font-family: 'Segoe UI', Roboto, Helvetica, sans-serif; font-weight: 700 !important; }
    .vvmc-header-link { text-decoration: none !important; display: inline-block; transition: opacity 0.2s, transform 0.2s; cursor: pointer; }
    .vvmc-header-link:hover { opacity: 0.8; transform: scale(1.02); }
    .vvmc-title { margin: 0; font-size: 2.8rem; font-weight: 800; background: -webkit-linear-gradient(45deg, #0d9488, #14b8a6); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .criticker-card { background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); border: 1px solid #334155; border-radius: 12px; padding: 18px; margin-bottom: 20px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); transition: transform 0.2s, border-color 0.2s; }
    .criticker-card:hover { transform: translateY(-2px); border-color: #0d9488; }
    .criticker-badge { display: inline-block; padding: 4px 10px; border-radius: 6px; font-weight: bold; font-size: 0.9em; text-align: center; min-width: 45px; }
    .badge-high { background-color: #0d9488; color: #ffffff; }
    .badge-mid { background-color: #d97706; color: #ffffff; }
    .badge-low { background-color: #dc2626; color: #ffffff; }
    .quip-box { background-color: #1e293b; border-left: 3px solid #0d9488; padding: 8px 12px; margin: 5px 0; border-radius: 0 8px 8px 0; font-style: italic; font-size: 0.9em; color: #cbd5e1; }
    section[data-testid="stSidebar"] { background-color: #080c14 !important; border-right: 1px solid #1e293b; }
    section[data-testid="stSidebar"] button { background-color: #1e293b !important; color: #f1f5f9 !important; border: 1px solid #334155 !important; border-radius: 8px !important; font-weight: 600 !important; padding: 6px 12px !important; transition: all 0.25s ease !important; }
    section[data-testid="stSidebar"] button:hover { background-color: #dc2626 !important; color: #ffffff !important; border-color: #ef4444 !important; box-shadow: 0 0 12px rgba(239, 68, 68, 0.4) !important; }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; background-color: #0f172a; padding: 8px 10px; border-radius: 14px; border: 1px solid #1e293b; box-shadow: inset 0 2px 4px rgba(0,0,0,0.3); display: flex; justify-content: space-between; width: 100% !important; }
    .stTabs [data-baseweb="tab"] { color: #94a3b8; border-radius: 10px; padding: 10px 14px; font-size: 0.95rem; font-weight: 600; background-color: transparent; transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); border: 1px solid transparent; flex-grow: 1; text-align: center; }
    .stTabs [data-baseweb="tab-list"] button[aria-label="Scroll left"], .stTabs [data-baseweb="tab-list"] button[aria-label="Scroll right"] { display: none !important; }
    .stTabs [data-baseweb="tab"]:hover { color: #f8fafc; background-color: #1e293b; border-color: #334155; }
    .stTabs [aria-selected="true"] { background: linear-gradient(135deg, #0d9488 0%, #14b8a6 100%) !important; color: #ffffff !important; box-shadow: 0 4px 12px rgba(13, 148, 136, 0.35); border-color: #14b8a6; }
</style>
""", unsafe_allow_html=True)

# Инициализация сессии пользователя
if "logged_in" not in st.session_state: 
    st.session_state["logged_in"] = False
if "current_user" not in st.session_state: 
    st.session_state["current_user"] = None
if "via_telegram" not in st.session_state:
    st.session_state["via_telegram"] = False

# Проверка Telegram-параметров в URL и немедленный автологин при их обнаружении
query_params = st.query_params
if not st.session_state["logged_in"] and "tg_user" in query_params:
    tg_user = query_params["tg_user"]
    
    # Свежий запрос к БД перед автологином, чтобы избежать рассинхронизации состояния
    current_users = get_users_dict()
    
    if tg_user not in current_users:
        add_user(tg_user, "telegram_auto_pass", is_telegram=1)
        users_db = get_users_dict() # Сразу обновляем локальный кэш
    
    st.session_state.update({
        "logged_in": True,
        "current_user": tg_user,
        "via_telegram": True
    })
    st.rerun()

# Экран логина (для обычных браузеров и ручного выбора)
if not st.session_state["logged_in"]:
    st.markdown("""
    <div style="text-align: center; margin-bottom: 25px;">
        <h1 class="vvmc-title">🎬 VVMC CLUB</h1>
        <p style='color: #94a3b8; margin-top: 5px;'>Приватный кино-топ и анализ вкусов для узкого круга друзей</p>
    </div>
    """, unsafe_allow_html=True)
    
    col_login, col_info_box = st.columns([2, 1])
    with col_login:
        with st.form("login_form"):
            st.subheader("Вход в киноклуб")
            u = st.text_input("Логин / Имя пользователя")
            p = st.text_input("Пароль", type="password")
            
            submit_btn = st.form_submit_button("Войти")
            register_btn = st.form_submit_button("Зарегистрировать новый аккаунт")
            
            if submit_btn:
                if users_db.get(u) == p:
                    st.session_state.update({"logged_in": True, "current_user": u, "via_telegram": False})
                    st.success(f"Добро пожаловать обратно, {u}!")
                    st.rerun()
                else:
                    st.error("❌ Неверный логин или пароль")
            
            if register_btn:
                if len(u) < 2 or len(p) < 4:
                    st.warning("⚠️ Логин должен быть от 2 символов, а пароль от 4 символов")
                elif u in users_db:
                    st.error("❌ Этот пользователь уже зарегистрирован")
                else:
                    add_user(u, p, is_telegram=0)
                    users_db = get_users_dict() # Обновляем локальный кэш пользователей
                    st.session_state.update({"logged_in": True, "current_user": u, "via_telegram": False})
                    st.success(f"🎉 Аккаунт '{u}' успешно создан!")
                    st.rerun()
                    
        # --- ИНТЕГРАЦИЯ С ТЕЛЕГРАМ-БОТОМ И WEBAPP ---
        st.write("<div style='text-align: center; margin: 15px 0; color: #64748b;'>или</div>", unsafe_allow_html=True)
        
        # Получаем имя бота из secrets для построения прямой ссылки
        bot_username = st.secrets.get("TELEGRAM_BOT_USERNAME", "vvmc_club_bot")
        
        # Кнопка перехода в бота
        st.markdown(f"""
        <div style="display: flex; flex-direction: column; align-items: center; gap: 12px; margin-bottom: 15px;">
            <a href="https://t.me/{bot_username}" target="_blank" style="text-decoration: none; width: 100%; max-width: 320px;">
                <div style="
                    background: linear-gradient(135deg, #24A1DE 0%, #1d82b2 100%); 
                    color: white; 
                    border-radius: 10px; 
                    padding: 12px; 
                    font-weight: bold; 
                    text-align: center;
                    font-family: sans-serif;
                    box-shadow: 0 4px 15px rgba(36, 161, 222, 0.3);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 10px;
                ">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                        <path d="m22 2-7 20-4-9-9-4Z"></path>
                        <path d="M22 2 11 13"></path>
                    </svg>
                    Открыть бота в Telegram
                </div>
            </a>
        </div>
        """, unsafe_allow_html=True)

        # Интерактивный iframe с отладчиком Telegram WebApp для автовхода
        components.html(
            """
            <div id="btn-container" style="
                display: flex; 
                flex-direction: column; 
                align-items: center; 
                justify-content: center; 
                background-color: #0f172a; 
                border: 1px solid #1e293b; 
                border-radius: 10px; 
                padding: 15px; 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            ">
                <div id="status" style="color: #94a3b8; font-size: 13px; font-weight: 500; text-align: center; margin-bottom: 8px;">
                    Ожидание Telegram-окружения...
                </div>
                <div id="debug-log" style="color: #64748b; font-size: 11px; text-align: center; word-break: break-all; max-height: 40px; overflow-y: auto;">
                    Инициализация...
                </div>
            </div>

            <!-- Подключаем скрипт Telegram непосредственно внутри iframe -->
            <script src="https://telegram.org/js/telegram-web-app.js"></script>
            <script>
                const statusDiv = document.getElementById('status');
                const logDiv = document.getElementById('debug-log');

                function log(message, isError = false) {
                    logDiv.innerHTML = message;
                    if (isError) {
                        logDiv.style.color = "#f87171";
                    } else {
                        logDiv.style.color = "#10b981";
                    }
                }

                function initTelegram() {
                    try {
                        log("Проверка скрипта Telegram WebApp...");
                        if (typeof window.Telegram === 'undefined') {
                            statusDiv.innerHTML = "❌ Ошибка: Скрипт Telegram не загрузился.";
                            log("window.Telegram не определен.", true);
                            return;
                        }

                        const tg = window.Telegram;
                        
                        if (tg && tg.WebApp && tg.WebApp.initDataUnsafe && tg.WebApp.initDataUnsafe.user) {
                            statusDiv.innerHTML = "⚡ Авторизация обнаружена!";
                            tg.WebApp.ready();
                            tg.WebApp.expand();
                            
                            const user = tg.WebApp.initDataUnsafe.user;
                            const username = user.username || user.first_name || "tg_user";
                            
                            log("Пользователь найден: " + username);
                            
                            // Получаем адрес родительского окна (Streamlit) через referrer в обход ограничений CORS
                            let parentUrlString = document.referrer;
                            if (!parentUrlString || parentUrlString === "") {
                                parentUrlString = window.location.ancestorOrigins ? window.location.ancestorOrigins[0] : window.location.href;
                            }
                            
                            if (parentUrlString) {
                                let url = new URL(parentUrlString);
                                url.searchParams.set("tg_user", username);
                                url.searchParams.set("tg_ref", "telegram");
                                
                                log("Перенаправление родителя на: " + url.pathname);
                                // Безопасное перенаправление родителя
                                window.top.location.href = url.href;
                            } else {
                                log("Не удалось определить URL родителя", true);
                            }
                        } else {
                            statusDiv.innerHTML = "📢 Режим браузера";
                            log("Вы не внутри Telegram клиента. Авто-вход сработает при открытии сайта внутри Telegram-бота.");
                        }
                    } catch (err) {
                        statusDiv.innerHTML = "⚠️ Ошибка инициализации";
                        log(err.message, true);
                    }
                }
                
                // Даем небольшую задержку на подгрузку DOM и API
                setTimeout(initTelegram, 400);
            </script>
            """,
            height=120,
        )

    with col_info_box:
        st.markdown("""
        <div style="background-color: #0f172a; border: 1px solid #1e293b; border-radius: 12px; padding: 15px;">
            <h4 style="margin-top:0; color:#14b8a6 !important;">💡 Как войти через Telegram?</h4>
            <ol style="color:#94a3b8; font-size:0.9em; padding-left:20px;">
                <li style="margin-bottom:8px;">Зайдите в Telegram-бота <strong>vvmc_club_bot</strong> (или в вашего бота) и запустите его.</li>
                <li style="margin-bottom:8px;">Нажмите кнопку запуска WebApp ("Открыть Клуб" / "Запустить").</li>
                <li style="margin-bottom:8px;">Сайт откроется внутри Telegram, виджет снизу мгновенно распознает ваш профиль и совершит бесшовный вход!</li>
            </ol>
        </div>
        """, unsafe_allow_html=True)
    st.stop()

# ----------------- ОСНОВНОЙ ИНТЕРФЕЙС ПРИЛОЖЕНИЯ -----------------

st.markdown("""
<div style="text-align: center; margin-bottom: 20px;">
    <a href="javascript:window.parent.location.href = window.parent.location.pathname;" class="vvmc-header-link" title="На главную (Лента критики)">
        <h1 class="vvmc-title">🎬 VVMC CLUB</h1>
    </a>
</div>
""", unsafe_allow_html=True)

def get_badge_class(score):
    if score >= 75: return "badge-high"
    if score >= 40: return "badge-mid"
    return "badge-low"

def calculate_tci(user1, user2, movies_list):
    scores_u1 = []
    scores_u2 = []
    for m in movies_list:
        if m.get("status") == "watched" and user1 in m["ratings"] and user2 in m["ratings"]:
            scores_u1.append(m["ratings"][user1]["score"])
            scores_u2.append(m["ratings"][user2]["score"])
            
    if len(scores_u1) < 3:
        return None, len(scores_u1)
        
    mae = np.mean(np.abs(np.array(scores_u1) - np.array(scores_u2)))
    tci_score = max(0, min(100, int(100 - mae)))
    return tci_score, len(scores_u1)

with st.sidebar:
    user_initial = st.session_state['current_user'][0].upper() if st.session_state['current_user'] else "?"
    status_badge = "TELEGRAM MEMBER" if st.session_state["via_telegram"] else "VVMC CLUB MEMBER"
    st.markdown(f"""
    <div style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); border: 1px solid #334155; border-radius: 14px; padding: 18px; margin-bottom: 20px; text-align: center; box-shadow: 0 4px 10px rgba(0,0,0,0.2);">
        <div style="width: 52px; height: 52px; background: linear-gradient(135deg, #0d9488 0%, #14b8a6 100%); border-radius: 50%; display: flex; align-items: center; justify-content: center; margin: 0 auto 12px auto; font-size: 1.6rem; font-weight: bold; color: white; box-shadow: 0 0 15px rgba(13,148,136,0.5);">
            {user_initial}
        </div>
        <div style="font-size: 1.15rem; font-weight: bold; color: #f8fafc; margin-bottom: 2px;">{st.session_state['current_user']}</div>
        <div style="font-size: 0.8rem; color: #14b8a6; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; margin-bottom: 12px;">{status_badge}</div>
    </div>
    """, unsafe_allow_html=True)
    
    if st.button("🚪 Выйти из аккаунта", use_container_width=True):
        st.session_state.update({"logged_in": False, "current_user": None, "via_telegram": False})
        st.query_params.clear() # Очищаем параметры URL
        st.rerun()
        
    st.markdown("<div style='margin-bottom: 15px;'></div>", unsafe_allow_html=True)
    
    # --- ДИАГНОСТИКА И СТАТУС GOOGLE DRIVE В СЕЙДБАРЕ ---
    st.markdown("<p style='font-size: 0.9em; font-weight: bold; color: #94a3b8; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px;'>☁️ Облако Google Drive</p>", unsafe_allow_html=True)
    
    gdrive_service = get_gdrive_service()
    if gdrive_service:
        st.markdown("<div style='color: #10b981; font-weight: bold; font-size: 0.9em; margin-bottom: 5px;'>● Подключено к Drive API</div>", unsafe_allow_html=True)
        
        # Получаем данные о файле
        try:
            db_size_kb = os.path.getsize(DB_FILE) / 1024 if os.path.exists(DB_FILE) else 0
            st.markdown(f"<div style='color: #cbd5e1; font-size: 0.85em;'>Локальная БД: <b>{db_size_kb:.1f} KB</b></div>", unsafe_allow_html=True)
            
            folder_id = st.secrets["gdrive"].get("folder_id")
            cloud_file_id = find_db_on_gdrive(gdrive_service, folder_id)
            if cloud_file_id:
                st.markdown(f"<div style='color: #10b981; font-size: 0.85em;'>Файл в облаке: <b>Найден ✓</b><br><span style='color: #64748b; font-size: 0.8em;'>ID: ...{cloud_file_id[-8:]}</span></div>", unsafe_allow_html=True)
            else:
                st.markdown("<div style='color: #fbbf24; font-size: 0.85em;'>Файл в облаке: <b>Не создан</b><br><span style='color: #64748b;'>Создастся при синхронизации</span></div>", unsafe_allow_html=True)
        except Exception:
            pass
            
        if st.button("🔄 Синхронизировать сейчас", use_container_width=True):
            with st.spinner("Загрузка базы в Google Drive..."):
                upload_db_to_cloud()
            st.success("База успешно отправлена!")
            st.rerun()
    else:
        st.markdown("<div style='color: #ef4444; font-weight: bold; font-size: 0.9em;'>○ Отключено (нет Secrets)</div>", unsafe_allow_html=True)
        st.caption("Настройте credentials в Streamlit Cloud -> Settings -> Secrets")
        
    st.markdown("<hr style='border: 1px solid #1e293b; margin: 15px 0;'/>", unsafe_allow_html=True)
    
    st.markdown("<p style='font-size: 0.9em; font-weight: bold; color: #94a3b8; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px;'>🎨 Настройка темы</p>", unsafe_allow_html=True)
    selected_bg = st.selectbox("Фон приложения", bg_files, label_visibility="collapsed")
    if selected_bg != st.session_state["bg_choice"]:
        st.session_state["bg_choice"] = selected_bg
        st.rerun()
        
    st.markdown("<hr style='border: 1px solid #1e293b; margin: 15px 0;'/>", unsafe_allow_html=True)
    
    st.markdown("""
    <div style="background-color: #0f172a; border-radius: 12px; border: 1px solid #1e293b; padding: 15px; box-shadow: inset 0 2px 4px rgba(0,0,0,0.2);">
        <div style="font-weight: 700; color: #f8fafc; margin-bottom: 12px; font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid #1e293b; padding-bottom: 6px; display: flex; align-items: center; gap: 6px;">
            <span>🎯</span> Шкала оценок
        </div>
        <div style="display: flex; flex-direction: column; gap: 8px;">
            <div style="display: flex; align-items: center; justify-content: space-between;">
                <span class="criticker-badge badge-high" style="padding: 1px 8px; font-size: 0.75rem;">90 - 100</span>
                <span style="font-size: 0.85em; color: #cbd5e1; font-weight: 500;">Шедевр</span>
            </div>
            <div style="display: flex; align-items: center; justify-content: space-between;">
                <span class="criticker-badge badge-high" style="background-color: #14b8a6; padding: 1px 8px; font-size: 0.75rem;">75 - 89</span>
                <span style="font-size: 0.85em; color: #cbd5e1; font-weight: 500;">Отличный</span>
            </div>
            <div style="display: flex; align-items: center; justify-content: space-between;">
                <span class="criticker-badge badge-mid" style="padding: 1px 8px; font-size: 0.75rem;">50 - 74</span>
                <span style="font-size: 0.85em; color: #cbd5e1; font-weight: 500;">Хороший</span>
            </div>
            <div style="display: flex; align-items: center; justify-content: space-between;">
                <span class="criticker-badge badge-mid" style="background-color: #f97316; padding: 1px 8px; font-size: 0.75rem;">30 - 49</span>
                <span style="font-size: 0.85em; color: #cbd5e1; font-weight: 500;">Проходной</span>
            </div>
            <div style="display: flex; align-items: center; justify-content: space-between;">
                <span class="criticker-badge badge-low" style="padding: 1px 8px; font-size: 0.75rem;">1 - 29</span>
                <span style="font-size: 0.85em; color: #cbd5e1; font-weight: 500;">Ужасно</span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

tab_feed, tab_list, tab_watchlist, tab_add, tab_rating = st.tabs([
    "💬 Лента Критики", 
    "🏆 Просмотренные и оценённые", 
    "🎬 Посмотреть позже", 
    "➕ Добавить фильм", 
    "📊 Анализ & Совместимость"
])

# 1. ВКЛАДКА: Лента Критики
with tab_feed:
    st.subheader("🔥 Последние квипы и оценки друзей")
    
    events = []
    for m in movies:
        for user, r_data in m.get("ratings", {}).items():
            events.append({
                "movie": m["title"],
                "poster": m.get("poster"),
                "user": user,
                "score": r_data["score"],
                "quip": r_data["quip"],
                "date": r_data.get("date", "Ранее")
            })
            
    if not events:
        st.info("Пока никто не оставил оценок. Будьте первым!")
    else:
        events.sort(key=lambda x: x["date"], reverse=True)
        
        for e in events[:15]:
            col_post, col_info = st.columns([1, 10])
            with col_post:
                if e["poster"]:
                    st.image(e["poster"], use_container_width=True)
                else:
                    st.write("🎬")
            with col_info:
                badge_style = get_badge_class(e['score'])
                st.markdown(f"""
                <span class="criticker-badge {badge_style}">{e['score']}</span> 
                <strong>{e['user']}</strong> оценил(а) фильм <strong>«{e['movie']}»</strong>
                <span style='color: #64748b; font-size: 0.8em; float: right;'>{e['date']}</span>
                """, unsafe_allow_html=True)
                if e["quip"]:
                    st.markdown(f'<div class="quip-box">“{e["quip"]}”</div>', unsafe_allow_html=True)
                st.markdown("<div style='margin-bottom:15px;'></div>", unsafe_allow_html=True)

# 2. ВКЛАДКА: Мой ТОП фильмов
with tab_list:
    st.subheader("🏆 Ваши оценки и управление")
    
    watched_movies = [m for m in movies if m.get("status") == "watched"]
    
    if not watched_movies:
        st.info("Вы пока не оценили ни одного фильма. Перенесите фильм из списка 'Посмотреть позже' или добавьте новый!")
    else:
        search_query = st.text_input("Поиск по вашему ТОПу", "").lower()
        filtered_watched = [m for m in watched_movies if search_query in m["title"].lower()]
        
        for m in filtered_watched:
            user_data = m["ratings"].get(st.session_state["current_user"], {})
            has_rated = bool(user_data)
            current_score = user_data.get("score", 50)
            current_quip = user_data.get("quip", "")
            
            edit_key = f"edit_{m['id']}"
            if edit_key not in st.session_state:
                st.session_state[edit_key] = not has_rated
            
            with st.container():
                st.markdown(f"""
                <div class="criticker-card" style="padding: 12px 18px; margin-bottom: 10px;">
                    <h3 style='margin:0;'>{m['title']}</h3>
                </div>
                """, unsafe_allow_html=True)
                
                col_img, col_inputs = st.columns([1, 8])
                with col_img:
                    if m.get("poster"):
                        st.image(m["poster"], use_container_width=True)
                
                with col_inputs:
                    if st.session_state[edit_key]:
                        new_score = st.slider("Ваша оценка (1-100)", 1, 100, int(current_score), key=f"score_slider_{m['id']}")
                        new_quip = st.text_input("Квип (короткое мнение / цитата)", value=current_quip, max_chars=200, key=f"quip_text_{m['id']}")
                        
                        others_ratings = {u: r for u, r in m["ratings"].items() if u != st.session_state["current_user"]}
                        if others_ratings:
                            st.markdown("<p style='font-size:0.85em; color:#94a3b8; margin:5px 0 2px 0;'>Оценки друзей:</p>", unsafe_allow_html=True)
                            friends_html = ""
                            for friend, f_data in others_ratings.items():
                                f_badge = get_badge_class(f_data['score'])
                                friends_html += f"""
                                <span style='margin-right: 15px; font-size: 0.85em;'>
                                    <strong>{friend}</strong>: 
                                    <span class="criticker-badge {f_badge}" style="padding: 1px 6px; font-size:0.8em;">{f_data['score']}</span>
                                </span>
                                """
                            st.markdown(friends_html, unsafe_allow_html=True)
                        
                        col_b1, col_b2, col_b3 = st.columns([2, 2, 6])
                        with col_b1:
                            if st.button("Сохранить", key=f"save_btn_{m['id']}", type="primary"):
                                set_rating(m['id'], st.session_state["current_user"], new_score, new_quip, datetime.now().strftime("%Y-%m-%d %H:%M"))
                                st.session_state[edit_key] = False 
                                st.rerun()
                        with col_b2:
                            if has_rated and st.button("Отмена", key=f"cancel_btn_{m['id']}"):
                                st.session_state[edit_key] = False
                                st.rerun()
                        with col_b3:
                            if st.button("🗑️ Удалить фильм", key=f"del_exp_{m['id']}", type="secondary"):
                                delete_movie(m['id'])
                                st.rerun()
                    else:
                        badge_style = get_badge_class(current_score)
                        st.markdown(f"""
                        <div style="margin-bottom: 8px;">
                            <span style="color:#94a3b8; font-size: 0.9em; margin-right: 5px;">Ваша оценка:</span> 
                            <span class="criticker-badge {badge_style}">{current_score}</span>
                        </div>
                        """, unsafe_allow_html=True)
                        
                        if current_quip:
                            st.markdown(f'<div class="quip-box" style="margin-bottom: 15px;">“{current_quip}”</div>', unsafe_allow_html=True)
                        
                        col_c1, col_c2 = st.columns([2, 8])
                        with col_c1:
                            if st.button("✏️ Изменить", key=f"edit_btn_{m['id']}"):
                                st.session_state[edit_key] = True
                                st.rerun()
                        with col_c2:
                            if st.button("🗑️ Удалить", key=f"del_comp_{m['id']}", type="secondary"):
                                delete_movie(m['id'])
                                st.rerun()

                st.markdown("<hr style='border: 1px solid #1e293b; margin-top:10px;'/>", unsafe_allow_html=True)

# 3. ВКЛАДКА: Посмотреть позже
with tab_watchlist:
    st.subheader("🎬 Ваш лист ожидания")
    
    watchlist_movies = [m for m in movies if m.get("status") == "watchlist"]
    
    if not watchlist_movies:
        st.info("Ваш список 'Посмотреть позже' пуст. Добавьте фильмы через вкладку добавления!")
    else:
        cols = st.columns(4)
        for idx, m in enumerate(watchlist_movies):
            with cols[idx % 4]:
                st.markdown(f"#### {m['title']}")
                
                adder = m.get('added_by', 'Неизвестно')
                st.markdown(f"<p style='font-size: 0.85em; color: #94a3b8; margin-top: -10px;'>👤 Добавил(а): <strong>{adder}</strong></p>", unsafe_allow_html=True)
                
                if m.get("poster"):
                    st.image(m["poster"], use_container_width=True)
                
                with st.popover("Оценить и перенести в ТОП", use_container_width=True):
                    watch_score = st.slider("Ваша оценка (1-100)", 1, 100, 70, key=f"watch_sc_{m['id']}")
                    watch_quip = st.text_input("Квип (отзыв)", key=f"watch_qp_{m['id']}")
                    
                    if st.button("Готово! Просмотрено", key=f"confirm_watch_{m['id']}", type="primary", use_container_width=True):
                        update_movie_status(m['id'], "watched")
                        set_rating(m['id'], st.session_state["current_user"], watch_score, watch_quip, datetime.now().strftime("%Y-%m-%d %H:%M"))
                        st.success(f"«{m['title']}» перенесен в ТОП!")
                        st.rerun()
                
                if st.button("Удалить из списка", key=f"delete_wl_{m['id']}", type="secondary", use_container_width=True):
                    delete_movie(m['id'])
                    st.rerun()

# 4. ВКЛАДКА: Добавить фильм
with tab_add:
    st.subheader("🔍 Поиск и добавление новых фильмов через TMDB")
    
    search = st.text_input("Введите оригинальное или русское название фильма")
    
    with st.expander("Добавить фильм вручную (без поиска)"):
        manual_title = st.text_input("Название фильма вручную")
        manual_poster = st.text_input("Ссылка на постер (необязательно)")
        manual_status = st.selectbox("Куда добавить?", ["Сразу в ТОП (просмотрено)", "В список Посмотреть позже"])
        
        if st.button("Добавить фильм вручную"):
            if manual_title:
                status = "watched" if "Сразу в ТОП" in manual_status else "watchlist"
                add_movie(manual_title, manual_poster if manual_poster else None, status, st.session_state["current_user"])
                st.success(f"Фильм «{manual_title}» успешно добавлен вручную!")
                st.rerun()
            else:
                st.error("Пожалуйста, заполните название!")

    if search:
        api_key = st.secrets.get("TMDB_API_KEY", "")
        
        if not api_key:
            st.warning("⚠️ TMDB_API_KEY отсутствует в st.secrets. Вы можете добавить фильм вручную выше, либо настроить API ключ.")
        else:
            try:
                res_raw = requests.get(
                    "https://api.themoviedb.org/3/search/movie", 
                    params={"api_key": api_key, "query": search, "language": "ru-RU"}
                )
                res = res_raw.json().get("results", [])
                
                if not res:
                    st.info("По вашему запросу ничего не найдено.")
                else:
                    st.write("### Результаты поиска:")
                    for item in res[:5]:
                        col_card_img, col_card_info = st.columns([1, 6])
                        poster_url = f"https://image.tmdb.org/t/p/w500{item['poster_path']}" if item.get('poster_path') else None
                        
                        with col_card_img:
                            if poster_url:
                                st.image(poster_url, use_container_width=True)
                            else:
                                st.write("Нет постера")
                                
                        with col_card_info:
                            st.markdown(f"**{item['title']}** ({item.get('release_date', 'Дата неизвестна')[:4]})")
                            st.write(item.get("overview", "Описание отсутствует."))
                            
                            col_b1, col_b2 = st.columns(2)
                            with col_b1:
                                if st.button(f"Добавить в ТОП (watched)", key=f"add_t_{item['id']}", use_container_width=True):
                                    add_movie(item['title'], poster_url, "watched", st.session_state["current_user"])
                                    st.success(f"«{item['title']}» добавлен в список просмотренных!")
                                    st.rerun()
                            with col_b2:
                                if st.button(f"В 'Посмотреть позже' (watchlist)", key=f"add_w_{item['id']}", use_container_width=True):
                                    add_movie(item['title'], poster_url, "watchlist", st.session_state["current_user"])
                                    st.success(f"«{item['title']}» добавлен в лист ожидания!")
                                    st.rerun()
                        st.markdown("---")
            except Exception as e:
                st.error(f"Ошибка при работе с TMDB API: {e}")

# 5. ВКЛАДКА: Сравнение, Анализ & TCI
with tab_rating:
    st.subheader("📊 Аналитика клуба")
    
    col_tci, col_leaderboard = st.columns([1, 1])
    
    with col_tci:
        st.write("### 🤝 Индекс совместимости (TCI) друзей")
        st.markdown("Показывает схожесть оценок на основе общих просмотренных фильмов.")
        
        all_users = list(users_db.keys())
        
        if len(all_users) < 2:
            st.info("Для расчета совместимости требуется хотя бы 2 зарегистрированных пользователя.")
        else:
            tci_data = []
            for i, u1 in enumerate(all_users):
                for u2 in all_users[i+1:]:
                    tci, shared_count = calculate_tci(u1, u2, movies)
                    if tci is not None:
                        tci_data.append({
                            "Пара друзей": f"👥 {u1} и {u2}",
                            "Индекс TCI": f"🎯 {tci}%",
                            "Общих фильмов": f"🎬 {shared_count} шт."
                        })
            
            if tci_data:
                st.dataframe(pd.DataFrame(tci_data), use_container_width=True, hide_index=True)
            else:
                st.info("Пока нет общих просмотренных фильмов у пользователей (нужно минимум 3 общих фильма для подсчета TCI).")
                
    with col_leaderboard:
        st.write("### 🏆 Общий Топ фильмов клуба")
        st.markdown("Средневзвешенный рейтинг фильмов по версии участников клуба.")
        
        leaderboard = []
        for m in [m for m in movies if m.get("status") == "watched"]:
            ratings_list = [r_data["score"] for r_data in m["ratings"].values() if isinstance(r_data, dict) and "score" in r_data]
            if ratings_list:
                avg_score = int(np.mean(ratings_list))
                leaderboard.append({
                    "Постер": m.get("poster"),
                    "Фильм": m["title"],
                    "Средний балл": avg_score,
                    "Голосов": len(ratings_list)
                })
                
        if leaderboard:
            leaderboard.sort(key=lambda x: x["Средний балл"], reverse=True)
            
            top_cols = st.columns(2)
            for idx, item in enumerate(leaderboard[:6]):
                with top_cols[idx % 2]:
                    badge_color = get_badge_class(item["Средний балл"])
                    st.markdown(f"""
                    <div class="criticker-card" style='text-align: center; padding: 10px; margin-bottom: 10px;'>
                        <span class="criticker-badge {badge_color}" style="font-size: 0.9em; margin-bottom: 4px;">★ {item["Средний балл"]}</span>
                        <h5 style='min-height: 40px; margin: 5px 0; font-size: 0.95em;'>{item["Фильм"]}</h5>
                    </div>
                    """, unsafe_allow_html=True)
                    if item["Постер"]:
                        st.image(item["Постер"], use_container_width=True)
                    st.caption(f"Голосов: {item['Голосов']}")
        else:
            st.info("Ни один фильм еще не оценен.")
