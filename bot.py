import asyncio
import logging
import sqlite3
import re
import time
from aiogram import Bot, Dispatcher, Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import ClientSession, ClientError

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

API_TOKEN = '7697670051:AAH6hD5wNfFFzCUNPRdZqNaaE_KNWeJ9TcU'
ADMIN_ID = 88938071  # Твой ID
CHANNEL_ID = '@vibejobs'

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# Определение состояний для пошагового ввода
class VacancyStates(StatesGroup):
    title = State()
    salary = State()
    company = State()
    location = State()
    description = State()
    confirmation = State()  # Новое состояние для подтверждения

# === База данных
try:
    conn = sqlite3.connect("hh.db")
    cursor = conn.cursor()
    # Проверка и создание/обновление таблицы
    cursor.execute('PRAGMA table_info(hh_vacancies)')
    columns = {row[1] for row in cursor.fetchall()}
    if 'user_submitted' not in columns or 'user_id' not in columns:
        if 'user_submitted' not in columns:
            cursor.execute('ALTER TABLE hh_vacancies ADD COLUMN user_submitted INTEGER DEFAULT 0')
        if 'user_id' not in columns:
            cursor.execute('ALTER TABLE hh_vacancies ADD COLUMN user_id INTEGER')
        conn.commit()
        logger.info("Колонки user_submitted и/или user_id добавлены в таблицу hh_vacancies")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS hh_vacancies (
            hh_id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            description TEXT,
            location TEXT,
            salary TEXT,
            remote INTEGER,
            is_approved INTEGER DEFAULT 0,
            is_published INTEGER DEFAULT 0,
            user_submitted INTEGER DEFAULT 0,
            user_id INTEGER
        )
    ''')
    conn.commit()
    logger.info("База данных инициализирована успешно")
except sqlite3.Error as e:
    logger.error(f"Ошибка базы данных: {e}")

# === Ключевые слова
IT_KEYWORDS = [
    "frontend", "backend", "fullstack", "flutter", "android", "ios", "mobile",
    "ui", "ux", "figma", "graphic", "designer", "qa", "quality assurance", "tester",
    "developer", "programmer", "software", "engineer", "web", "javascript", "react",
    "vue", "angular", "python", "java", "kotlin", "swift"
]

EXCLUDE_KEYWORDS = [
    "hr", "менеджер", "sales", "buhgalter", "бухгалтер", "преподаватель"
]

# === Список технологий
TECHNOLOGIES = [
    "HTML", "CSS", "JavaScript", "TypeScript", "React", "Vue", "Angular", "Node.js",
    "Python", "Django", "Flask", "FastAPI", "Java", "Spring", "Kotlin", "Swift",
    "Flutter", "Dart", "PHP", "Laravel", "Ruby", "Rails", "C#", ".NET", "C++",
    "Go", "Rust", "SQL", "MySQL", "PostgreSQL", "MongoDB", "Redis", "Docker",
    "Kubernetes", "AWS", "Azure", "GCP", "Git", "GitHub", "GitLab", "CI/CD",
    "Bootstrap", "Tailwind", "Figma", "Sketch", "Adobe XD", "GraphQL", "REST",
    "WebSocket", "Selenium", "Cypress", "Jest", "Mocha"
]

# === Фильтры
def is_it_vacancy(text: str) -> bool:
    if not text:
        return False
    text = text.lower()
    result = any(k in text for k in IT_KEYWORDS) and not any(e in text for e in EXCLUDE_KEYWORDS)
    logger.debug(f"Текст вакансии: {text[:100]}... | Это IT-вакансия: {result}")
    return result

def is_remote(description: str) -> bool:
    if not description:
        return False
    remote_keywords = [
        "удаленно", "remote", "дистанционно", "удаленная работа", "work from home",
        "telecommute", "telework", "удалённо", "удаленная", "wfh"
    ]
    result = any(word in description.lower() for word in remote_keywords)
    logger.debug(f"Описание: {description[:100]}... | Удаленная: {result}")
    return result

def is_uzbekistan(area_data: dict) -> bool:
    if not area_data:
        return False
    area_name = area_data.get("name", "").lower()
    area_id = str(area_data.get("id", ""))
    uzbekistan_names = ["узбекистан", "uzbekistan", "tashkent", "ташкент", "samarkand", "самарканд"]
    uzbekistan_ids = ["2214"]  # ID для Узбекистана
    result = any(name in area_name for name in uzbekistan_names) or area_id in uzbekistan_ids
    logger.debug(f"Данные области: {area_data} | Узбекистан: {result}")
    return result

# === Очистка HTML-тегов
def clean_description(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r'<[^>]+>', '', text)
    cleaned = ' '.join(cleaned.split())
    return cleaned

# === Извлечение технологий
def extract_technologies(text: str) -> str:
    if not text:
        return "Не указаны"
    text = text.lower()
    found_tech = []
    for tech in TECHNOLOGIES:
        if tech.lower() in text and tech not in found_tech:
            found_tech.append(tech)
    return ", ".join(found_tech) if found_tech else "Не указаны"

# === Шаблон сообщения для пользователя
def format_user_preview(v):
    remote = "Удаленно" if v['remote'] else "Офлайн"
    return (
        f"💼 Вакансия: {v['title']}\n"
        f"🏢 Компания: {v['company']}\n"
        f"🌍 Локация: {v['location']} ({remote})\n"
        f"💰 Зарплата: {v['salary']}\n"
        f"📝 Описание: {v['description']}\n"
        f"📌 Технологии: {v['technologies']}"
    )

# === Шаблон сообщения для канала
def format_it_post(v):
    remote = "Удаленно" if v['remote'] else "Офлайн"
    title = v['title']
    if "джун" in title.lower() or "junior" in title.lower():
        title = re.sub(
            r'(джун|junior)',
            f'<a href="https://hh.ru/vacancy/{v["id"]}">\\1</a>',
            title,
            flags=re.IGNORECASE
        )
    return (
        f"💼 Вакансия: {title}\n"
        f"🏢 Компания: {v['company']}\n"
        f"🌍 Локация: {v['location']} ({remote})\n"
        f"💰 Зарплата: {v['salary']}\n"
        f"🔗 <a href='https://hh.ru/vacancy/{v['id']}'>Подробности</a>\n"
        f"📌 Технологии: {v['technologies']}\n"
        f"#IT #Вакансия #Работа"
    )

# === Парсинг HH
async def parse_hh():
    apis = [
        {"url": "https://api.hh.ru/vacancies", "area": None, "schedule": "remote"},  # Россия, только удаленные
        {"url": "https://api.hh.uz/vacancies", "area": "2214"},  # Узбекистан
        {"url": "https://api.hh.kz/vacancies", "area": None}    # Казахстан
    ]
    params = {
        "text": " OR ".join(IT_KEYWORDS),
        "per_page": 100,
    }
    
    for api in apis:
        try:
            async with ClientSession() as session:
                api_params = params.copy()
                if api["area"]:
                    api_params["area"] = api["area"]
                if api["schedule"]:
                    api_params["schedule"] = api["schedule"]
                logger.info(f"Отправка запроса к {api['url']} с параметрами {api_params}")
                async with session.get(api['url'], params=api_params) as r:
                    if r.status != 200:
                        logger.error(f"Ошибка запроса API с статусом {r.status} для {api['url']}")
                        await bot.send_message(ADMIN_ID, f"Ошибка API {api['url']}: статус {r.status}")
                        continue
                    data = await r.json()
                    vacancies = data.get("items", [])
                    logger.info(f"Получено {len(vacancies)} вакансий от {api['url']}")

                    if not vacancies:
                        await bot.send_message(ADMIN_ID, f"Нет новых вакансий от {api['url']}")
                        continue

                    suitable_vacancies = 0
                    for item in vacancies:
                        hh_id = item.get("id")
                        title = item.get("name", "")
                        company = item.get("employer", {}).get("name", "Не указана")
                        area = item.get("area", {})
                        area_name = area.get("name", "Не указана")
                        desc = item.get("snippet", {}).get("requirement", "") or item.get("snippet", {}).get("responsibility", "") or ""
                        desc_cleaned = clean_description(desc)
                        technologies = extract_technologies(desc_cleaned)
                        full_text = f"{title} {desc_cleaned}"

                        if not is_it_vacancy(full_text):
                            logger.debug(f"Вакансия {hh_id} отфильтрована: не относится к IT")
                            continue

                        salary = item.get("salary")
                        sal_text = "не указана"
                        if salary:
                            sal_text = ""
                            if salary.get("from"):
                                sal_text += f"от {salary['from']}"
                            if salary.get("to"):
                                sal_text += f" до {salary['to']}"
                            sal_text += f" {salary.get('currency', '')}"

                        cursor.execute("SELECT 1 FROM hh_vacancies WHERE hh_id = ?", (hh_id,))
                        if cursor.fetchone():
                            logger.debug(f"Вакансия {hh_id} уже существует в базе")
                            continue

                        remote_flag = is_remote(desc_cleaned)
                        is_uz = is_uzbekistan(area)

                        try:
                            cursor.execute(
                                "INSERT INTO hh_vacancies (hh_id, title, company, description, location, salary, remote, is_approved, is_published) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (hh_id, title, company, desc_cleaned, area_name, sal_text, remote_flag, 0, 0)
                            )
                            conn.commit()
                            logger.info(f"Вакансия {hh_id} добавлена в базу")
                        except sqlite3.Error as e:
                            logger.error(f"Ошибка базы данных для вакансии {hh_id}: {e}")
                            continue

                        if is_uz or remote_flag:
                            suitable_vacancies += 1
                            post = format_it_post({
                                "id": hh_id,
                                "title": title,
                                "company": company,
                                "location": area_name,
                                "salary": sal_text,
                                "remote": remote_flag,
                                "technologies": technologies
                            })
                            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                [
                                    InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{hh_id}"),
                                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decline_{hh_id}")
                                ]
                            ])
                            try:
                                await bot.send_message(
                                    ADMIN_ID,
                                    post,
                                    parse_mode="HTML",
                                    reply_markup=keyboard
                                )
                                logger.info(f"Вакансия {hh_id} отправлена админу для одобрения")
                            except Exception as e:
                                logger.error(f"Не удалось отправить вакансию {hh_id} админу: {e}")

                    if suitable_vacancies == 0:
                        await bot.send_message(
                            ADMIN_ID,
                            f"Не найдено подходящих вакансий (Узбекистан или удаленные) от {api['url']}"
                        )
                        logger.info(f"Не найдено подходящих вакансий от {api['url']}")

        except ClientError as e:
            logger.error(f"Ошибка HTTP-клиента для {api['url']}: {e}")
            await bot.send_message(ADMIN_ID, f"Ошибка при запросе к {api['url']}: {e}")
        except Exception as e:
            logger.error(f"Неожиданная ошибка в parse_hh для {api['url']}: {e}")
            await bot.send_message(ADMIN_ID, f"Неизвестная ошибка для {api['url']}: {e}")

# === Начало работы и добавление вакансии
@router.message(Command("start"))
async def start_command(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Опубликовать вакансию", callback_data="publish_vacancy")]
    ])
    await message.answer("Добро пожаловать! Нажмите кнопку, чтобы добавить новую вакансию:", reply_markup=keyboard)

@router.callback_query(lambda c: c.data == "publish_vacancy")
async def process_publish_vacancy(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(VacancyStates.title)
    await callback.message.edit_text("Пожалуйста, введите данные вакансии шаг за шагом:\n1. Название вакансии")

@router.message(VacancyStates.title)
async def process_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(VacancyStates.salary)
    await message.answer("2. Зарплата (например, 'от 50000 до 100000 RUB')")

@router.message(VacancyStates.salary)
async def process_salary(message: types.Message, state: FSMContext):
    await state.update_data(salary=message.text)
    await state.set_state(VacancyStates.company)
    await message.answer("3. Компания")

@router.message(VacancyStates.company)
async def process_company(message: types.Message, state: FSMContext):
    await state.update_data(company=message.text)
    await state.set_state(VacancyStates.location)
    await message.answer("4. Локация")

@router.message(VacancyStates.location)
async def process_location(message: types.Message, state: FSMContext):
    await state.update_data(location=message.text)
    await state.set_state(VacancyStates.description)
    await message.answer("5. Описание")

@router.message(VacancyStates.description)
async def process_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    data = await state.get_data()
    technologies = extract_technologies(data['description'].lower())
    remote_flag = is_remote(data['description'])
    data['technologies'] = technologies
    data['remote'] = remote_flag

    # Показываем пользователю введенные данные для подтверждения
    preview = format_user_preview(data)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_vacancy")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_vacancy")]
    ])
    await message.answer(f"Проверьте введенные данные:\n\n{preview}", reply_markup=keyboard)
    await state.set_state(VacancyStates.confirmation)

@router.callback_query(lambda c: c.data == "confirm_vacancy", VacancyStates.confirmation)
async def process_confirm_vacancy(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = callback.from_user.id
    hh_id = f"user_{user_id}_{int(time.time())}"  # Уникальный ID

    try:
        cursor.execute(
            "INSERT INTO hh_vacancies (hh_id, title, company, description, location, salary, remote, is_approved, is_published, user_submitted, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (hh_id, data['title'], data['company'], data['description'], data['location'], data['salary'], data['remote'], 0, 0, 1, user_id)
        )
        conn.commit()
        logger.info(f"Вакансия {hh_id} от пользователя {user_id} добавлена в базу")
        post = format_it_post({
            "id": hh_id,
            "title": data['title'],
            "company": data['company'],
            "location": data['location'],
            "salary": data['salary'],
            "remote": data['remote'],
            "technologies": data['technologies']
        })
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{hh_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decline_{hh_id}")
            ]
        ])
        await bot.send_message(
            ADMIN_ID,
            f"Новая вакансия от пользователя {user_id}:\n\n{post}",
            parse_mode="HTML",
            reply_markup=keyboard
        )
        await callback.message.edit_text("Ваша вакансия отправлена на модерацию!")
        await state.clear()
    except sqlite3.Error as e:
        logger.error(f"Ошибка базы данных для вакансии {hh_id}: {e}")
        await callback.message.edit_text("Произошла ошибка при добавлении вакансии.")
        await state.clear()

@router.callback_query(lambda c: c.data == "cancel_vacancy", VacancyStates.confirmation)
async def process_cancel_vacancy(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Вакансия отменена.")
    await state.clear()

# === Запуск парсинга
@router.message(Command("start_hh_parser"))
async def start_hh_parser(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Доступ только для админа")
        logger.warning(f"Попытка несанкционированного доступа от пользователя {message.from_user.id}")
        return
    logger.info("Запуск парсера HH")
    await message.answer("Поиск IT-вакансий запущен.")
    asyncio.create_task(parse_hh())

# === Обработка действий админа
@router.callback_query(lambda c: c.data.startswith('approve_') or c.data.startswith('decline_'))
async def handle_vacancy_action(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        logger.warning(f"Попытка несанкционированного действия от пользователя {callback.from_user.id}")
        await callback.answer("Доступ только для админа")
        return

    action, hh_id = callback.data.split('_', 1)
    try:
        if action == 'approve':
            cursor.execute(
                "UPDATE hh_vacancies SET is_approved = 1, is_published = 1 WHERE hh_id = ?",
                (hh_id,)
            )
            conn.commit()
            cursor.execute("SELECT user_id, title, company, description, location, salary, remote FROM hh_vacancies WHERE hh_id = ?", (hh_id,))
            vacancy = cursor.fetchone()
            if vacancy:
                user_id = vacancy[0]
                post = format_it_post({
                    "id": hh_id,
                    "title": vacancy[1],
                    "company": vacancy[2],
                    "technologies": extract_technologies(vacancy[3]),
                    "location": vacancy[4],
                    "salary": vacancy[5],
                    "remote": vacancy[6]
                })
                await bot.send_message(
                    CHANNEL_ID,
                    post,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                await bot.send_message(user_id, f"Ваша вакансия '{vacancy[1]}' одобрена и опубликована!")
                await callback.message.edit_text(
                    f"Вакансия {hh_id} одобрена и опубликована",
                    reply_markup=None
                )
                logger.info(f"Вакансия {hh_id} одобрена и опубликована")
            else:
                await callback.message.edit_text(
                    f"Вакансия {hh_id} не найдена",
                    reply_markup=None
                )
                logger.warning(f"Вакансия {hh_id} не найдена для одобрения")
        else:  # decline
            await bot.send_message(ADMIN_ID, "Введите комментарий для отказа:")
            await bot.register_next_step_handler(callback.message, process_decline_comment, hh_id=hh_id)

        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка обработки {action} для вакансии {hh_id}: {e}")
        await callback.message.edit_text(
            f"Ошибка при обработке вакансии {hh_id}: {e}",
            reply_markup=None
        )
        await callback.answer()

async def process_decline_comment(message: types.Message, hh_id: str):
    comment = message.text
    cursor.execute("SELECT user_id, title FROM hh_vacancies WHERE hh_id = ?", (hh_id,))
    vacancy = cursor.fetchone()
    if vacancy:
        user_id = vacancy[0]
        await bot.send_message(user_id, f"Ваша вакансия '{vacancy[1]}' отклонена. Комментарий: {comment}")
        cursor.execute("DELETE FROM hh_vacancies WHERE hh_id = ?", (hh_id,))
        conn.commit()
        await message.edit_text(f"Вакансия {hh_id} отклонена с комментарием: {comment}")
        logger.info(f"Вакансия {hh_id} отклонена")
    else:
        await message.edit_text(f"Вакансия {hh_id} не найдена")

# === Парсинг и запуск
async def parse_hh():
    apis = [
        {"url": "https://api.hh.ru/vacancies", "area": None, "schedule": "remote"},  # Россия, только удаленные
        {"url": "https://api.hh.uz/vacancies", "area": "2214"},  # Узбекистан
        {"url": "https://api.hh.kz/vacancies", "area": None}    # Казахстан
    ]
    params = {
        "text": " OR ".join(IT_KEYWORDS),
        "per_page": 100,
    }
    
    for api in apis:
        try:
            async with ClientSession() as session:
                api_params = params.copy()
                if api["area"]:
                    api_params["area"] = api["area"]
                if api["schedule"]:
                    api_params["schedule"] = api["schedule"]
                logger.info(f"Отправка запроса к {api['url']} с параметрами {api_params}")
                async with session.get(api['url'], params=api_params) as r:
                    if r.status != 200:
                        logger.error(f"Ошибка запроса API с статусом {r.status} для {api['url']}")
                        await bot.send_message(ADMIN_ID, f"Ошибка API {api['url']}: статус {r.status}")
                        continue
                    data = await r.json()
                    vacancies = data.get("items", [])
                    logger.info(f"Получено {len(vacancies)} вакансий от {api['url']}")

                    if not vacancies:
                        await bot.send_message(ADMIN_ID, f"Нет новых вакансий от {api['url']}")
                        continue

                    suitable_vacancies = 0
                    for item in vacancies:
                        hh_id = item.get("id")
                        title = item.get("name", "")
                        company = item.get("employer", {}).get("name", "Не указана")
                        area = item.get("area", {})
                        area_name = area.get("name", "Не указана")
                        desc = item.get("snippet", {}).get("requirement", "") or item.get("snippet", {}).get("responsibility", "") or ""
                        desc_cleaned = clean_description(desc)
                        technologies = extract_technologies(desc_cleaned)
                        full_text = f"{title} {desc_cleaned}"

                        if not is_it_vacancy(full_text):
                            logger.debug(f"Вакансия {hh_id} отфильтрована: не относится к IT")
                            continue

                        salary = item.get("salary")
                        sal_text = "не указана"
                        if salary:
                            sal_text = ""
                            if salary.get("from"):
                                sal_text += f"от {salary['from']}"
                            if salary.get("to"):
                                sal_text += f" до {salary['to']}"
                            sal_text += f" {salary.get('currency', '')}"

                        cursor.execute("SELECT 1 FROM hh_vacancies WHERE hh_id = ?", (hh_id,))
                        if cursor.fetchone():
                            logger.debug(f"Вакансия {hh_id} уже существует в базе")
                            continue

                        remote_flag = is_remote(desc_cleaned)
                        is_uz = is_uzbekistan(area)

                        try:
                            cursor.execute(
                                "INSERT INTO hh_vacancies (hh_id, title, company, description, location, salary, remote, is_approved, is_published) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (hh_id, title, company, desc_cleaned, area_name, sal_text, remote_flag, 0, 0)
                            )
                            conn.commit()
                            logger.info(f"Вакансия {hh_id} добавлена в базу")
                        except sqlite3.Error as e:
                            logger.error(f"Ошибка базы данных для вакансии {hh_id}: {e}")
                            continue

                        if is_uz or remote_flag:
                            suitable_vacancies += 1
                            post = format_it_post({
                                "id": hh_id,
                                "title": title,
                                "company": company,
                                "location": area_name,
                                "salary": sal_text,
                                "remote": remote_flag,
                                "technologies": technologies
                            })
                            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                [
                                    InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{hh_id}"),
                                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decline_{hh_id}")
                                ]
                            ])
                            try:
                                await bot.send_message(
                                    ADMIN_ID,
                                    post,
                                    parse_mode="HTML",
                                    reply_markup=keyboard
                                )
                                logger.info(f"Вакансия {hh_id} отправлена админу для одобрения")
                            except Exception as e:
                                logger.error(f"Не удалось отправить вакансию {hh_id} админу: {e}")

                    if suitable_vacancies == 0:
                        await bot.send_message(
                            ADMIN_ID,
                            f"Не найдено подходящих вакансий (Узбекистан или удаленные) от {api['url']}"
                        )
                        logger.info(f"Не найдено подходящих вакансий от {api['url']}")

        except ClientError as e:
            logger.error(f"Ошибка HTTP-клиента для {api['url']}: {e}")
            await bot.send_message(ADMIN_ID, f"Ошибка при запросе к {api['url']}: {e}")
        except Exception as e:
            logger.error(f"Неожиданная ошибка в parse_hh для {api['url']}: {e}")
            await bot.send_message(ADMIN_ID, f"Неизвестная ошибка для {api['url']}: {e}")

# === Главная функция
async def main():
    logger.info("Запуск бота")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка опроса бота: {e}")

if __name__ == "__main__":
    asyncio.run(main())