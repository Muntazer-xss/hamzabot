import asyncio
import logging
import os
import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# ============================================================
# الإعدادات
# ============================================================

API_TOKEN = os.getenv("BOT_TOKEN", "8970646388:AAGXxh2kIROtpQINhypFfFVXVhZ81IMsEcQ")

DB_PATH = "medical_bot.db"
ITEMS_PER_PAGE = 8

ADMIN_IDS = {
    440967050,
}

MAIN_DEPARTMENTS = {
    "gyn": "نسائية وتوليد",
    "ped": "اطفال",
    "surg": "جراحة حواس",
    "med": "باطنية حواس",
}

SUB_DEPARTMENTS = {
    "surg": {"ent": "ENT", "opho": "عيون"},
    "med": {"hem": "أمراض دم", "derm": "جلدية", "neuro": "طب أعصاب"},
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("medical_bot")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# ============================================================
# حالات المحادثة (FSM)
# ============================================================

class BulkUploadState(StatesGroup):
    collecting_files = State()

class SearchState(StatesGroup):
    waiting_query = State()

# ============================================================
# قاعدة البيانات
# ============================================================

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pdf_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                department TEXT NOT NULL,
                sub_department TEXT NOT NULL,
                file_id TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_unique_id TEXT
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_dept ON pdf_files(department, sub_department)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_name ON pdf_files(file_name)")
        await db.commit()

async def insert_files(dept: str, sub_dept: str, files: list[dict]) -> int:
    inserted = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for f in files:
            cursor = await db.execute(
                "SELECT 1 FROM pdf_files WHERE file_unique_id = ? LIMIT 1",
                (f["file_unique_id"],),
            )
            if await cursor.fetchone():
                continue
            await db.execute(
                "INSERT INTO pdf_files (department, sub_department, file_id, file_name, file_unique_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (dept, sub_dept, f["file_id"], f["file_name"], f["file_unique_id"]),
            )
            inserted += 1
        await db.commit()
    return inserted

async def get_files_by_department(dept: str, sub_dept: str) -> list[tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, file_name, file_id FROM pdf_files "
            "WHERE department = ? AND sub_department = ? ORDER BY file_name",
            (dept, sub_dept),
        )
        return await cursor.fetchall()

async def search_files(query: str) -> list[tuple]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, file_name, file_id FROM pdf_files "
            "WHERE file_name LIKE ? ORDER BY file_name LIMIT 100",
            (f"%{query}%",),
        )
        return await cursor.fetchall()

async def get_file_by_id(file_db_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT file_id, file_name FROM pdf_files WHERE id = ?", (file_db_id,)
        )
        return await cursor.fetchone()

async def delete_file_by_id(file_db_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pdf_files WHERE id = ?", (file_db_id,))
        await db.commit()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ============================================================
# لوحات المفاتيح والقوائم
# ============================================================

def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton(text="تصفح الأقسام", callback_data="menu_download")],
        [InlineKeyboardButton(text="بحث عن محاضرة", callback_data="menu_search")],
        [InlineKeyboardButton(text="رفع محاضرات", callback_data="menu_upload")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_departments_keyboard(prefix: str):
    keyboard = []
    for code, name in MAIN_DEPARTMENTS.items():
        keyboard.append([InlineKeyboardButton(text=name, callback_data=f"{prefix}_{code}")])
    keyboard.append([InlineKeyboardButton(text="القائمة الرئيسية", callback_data="back_home")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def build_results_keyboard(
    files: list[tuple],
    page: int,
    nav_prefix: str,
    back_callback: str,
    back_text: str,
):
    total_pages = max(1, (len(files) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start_idx = page * ITEMS_PER_PAGE
    current_page_files = files[start_idx:start_idx + ITEMS_PER_PAGE]

    keyboard = []
    for file_id_db, file_name, _ in current_page_files:
        label = file_name if len(file_name) <= 45 else file_name[:42] + "..."
        keyboard.append([InlineKeyboardButton(text=label, callback_data=f"getfile_{file_id_db}")])

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="السابق", callback_data=f"{nav_prefix}_{page - 1}"))
    nav_buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="التالي", callback_data=f"{nav_prefix}_{page + 1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton(text=back_text, callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard), total_pages

# ============================================================
# الأوامر الأساسية
# ============================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "مرحباً بك في نظام إدارة ومشاركة المحاضرات الطبية.\nاختر أحد الخيارات أدناه:",
        reply_markup=get_main_menu_keyboard(),
    )

@dp.callback_query(F.data == "back_home")
async def back_home(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text("القائمة الرئيسية:", reply_markup=get_main_menu_keyboard())
    except Exception:
        await callback.message.answer("القائمة الرئيسية:", reply_markup=get_main_menu_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "ignore")
async def ignore_callback(callback: types.CallbackQuery):
    await callback.answer()

# ============================================================
# نظام التصفح وعرض الملفات
# ============================================================

@dp.callback_query(F.data == "menu_download")
async def start_download(callback: types.CallbackQuery):
    try:
        await callback.message.edit_text(
            "اختر القسم الرئيسي لتصفح المحاضرات:",
            reply_markup=get_departments_keyboard("dl_main"),
        )
    except Exception:
        pass
    await callback.answer()

@dp.callback_query(F.data.startswith("dl_main_"))
async def process_download_main(callback: types.CallbackQuery):
    dept_code = callback.data.split("_", 2)[2]
    if dept_code in SUB_DEPARTMENTS:
        keyboard = []
        for sub_code, sub_name in SUB_DEPARTMENTS[dept_code].items():
            keyboard.append([InlineKeyboardButton(text=sub_name, callback_data=f"dl_sub_{dept_code}_{sub_code}")])
        keyboard.append([InlineKeyboardButton(text="عودة للأقسام", callback_data="menu_download")])
        await callback.message.edit_text("اختر الفرع المطلوب:", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    else:
        await show_department_files(callback.message, dept_code, "none", page=0)
    await callback.answer()

@dp.callback_query(F.data.startswith("dl_sub_"))
async def process_download_sub(callback: types.CallbackQuery):
    _, _, dept_code, sub_code = callback.data.split("_", 3)
    await show_department_files(callback.message, dept_code, sub_code, page=0)
    await callback.answer()

@dp.callback_query(F.data.startswith("deptpage_"))
async def paginate_department_files(callback: types.CallbackQuery):
    _, dept, sub_dept, page = callback.data.split("_", 3)
    await show_department_files(callback.message, dept, sub_dept, page=int(page))
    await callback.answer()

async def show_department_files(message: types.Message, dept: str, sub_dept: str, page: int):
    files = await get_files_by_department(dept, sub_dept)
    dept_name = MAIN_DEPARTMENTS.get(dept, "")
    sub_name = SUB_DEPARTMENTS.get(dept, {}).get(sub_dept, "")
    title = f"قسم {dept_name}" + (f" - {sub_name}" if sub_name else "") + f"\n(عدد المحاضرات: {len(files)})"

    if not files:
        # تحديد زر العودة الصحيح بناءً على ما إذا كان القسم فرعياً أم رئيسياً
        back_callback = f"dl_main_{dept}" if sub_dept != "none" else "menu_download"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="عودة", callback_data=back_callback)]
        ])
        text = f"لا توجد محاضرات مرفوعة حالياً في {sub_name if sub_name else dept_name}."
    else:
        back_callback = f"dl_main_{dept}" if sub_dept != "none" else "menu_download"
        keyboard, _ = build_results_keyboard(
            files, page,
            nav_prefix=f"deptpage_{dept}_{sub_dept}",
            back_callback=back_callback,
            back_text="عودة",
        )
        text = title

    try:
        await message.edit_text(text, reply_markup=keyboard)
    except Exception:
        await message.answer(text, reply_markup=keyboard)

@dp.callback_query(F.data.startswith("getfile_"))
async def send_pdf_file(callback: types.CallbackQuery):
    file_db_id = int(callback.data.split("_", 1)[1])
    row = await get_file_by_id(file_db_id)
    if row:
        file_telegram_id, file_name = row
        
        delete_keyboard = None
        if is_admin(callback.from_user.id):
            delete_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="حذف هذه المحاضرة", callback_data=f"delfile_{file_db_id}")]
            ])

        try:
            await bot.send_document(
                callback.from_user.id, 
                file_telegram_id, 
                caption=file_name, 
                reply_markup=delete_keyboard
            )
            await callback.answer("تم إرسال المحاضرة.")
        except Exception:
            await callback.answer("تعذّر إرسال الملف.", show_alert=True)
    else:
        await callback.answer("الملف غير موجود أو تم حذفه.", show_alert=True)

@dp.callback_query(F.data.startswith("delfile_"))
async def delete_pdf_file(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("عذراً، هذه الصلاحية للمشرفين فقط.", show_alert=True)
        return

    file_db_id = int(callback.data.split("_", 1)[1])
    
    await delete_file_by_id(file_db_id)
    await callback.answer("تم حذف المحاضرة من النظام بنجاح.", show_alert=True)
    
    try:
        await callback.message.delete()
    except Exception:
        try:
            await callback.message.edit_caption(
                caption="[تم حذف هذه المحاضرة نهائياً من البوت]", 
                reply_markup=None
            )
        except Exception:
            pass

# ============================================================
# نظام البحث عن المحاضرات
# ============================================================

@dp.callback_query(F.data == "menu_search")
async def start_search(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SearchState.waiting_query)
    await callback.message.edit_text(
        "اكتب اسم المحاضرة أو جزءاً منها للبحث عنها:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="إلغاء", callback_data="back_home")]
        ]),
    )
    await callback.answer()

@dp.message(SearchState.waiting_query, F.text)
async def process_search_query(message: types.Message, state: FSMContext):
    query = message.text.strip()
    if len(query) < 2:
        await message.answer("يرجى كتابة كلمة بحث لا تقل عن حرفين.")
        return

    files = await search_files(query)
    await state.update_data(last_query=query)

    if not files:
        await message.answer(
            f"لا توجد نتائج مطابقة لـ «{query}».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="بحث جديد", callback_data="menu_search")],
                [InlineKeyboardButton(text="القائمة الرئيسية", callback_data="back_home")],
            ]),
        )
        return

    keyboard, _ = build_results_keyboard(
        files, 0,
        nav_prefix="searchpage",
        back_callback="back_home",
        back_text="القائمة الرئيسية",
    )
    await message.answer(
        f"نتائج البحث عن «{query}» ({len(files)} نتيجة):",
        reply_markup=keyboard,
    )

@dp.callback_query(F.data.startswith("searchpage_"))
async def paginate_search_results(callback: types.CallbackQuery, state: FSMContext):
    page = int(callback.data.split("_", 1)[1])
    data = await state.get_data()
    query = data.get("last_query", "")
    files = await search_files(query) if query else []
    keyboard, _ = build_results_keyboard(
        files, page,
        nav_prefix="searchpage",
        back_callback="back_home",
        back_text="القائمة الرئيسية",
    )
    try:
        await callback.message.edit_text(
            f"نتائج البحث عن «{query}» ({len(files)} نتيجة):",
            reply_markup=keyboard,
        )
    except Exception:
        pass
    await callback.answer()

# ============================================================
# نظام الرفع الجماعي (متاح للجميع)
# ============================================================

@dp.callback_query(F.data == "menu_upload")
async def start_upload(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "اختر القسم الذي تريد رفع المحاضرات إليه:",
        reply_markup=get_departments_keyboard("up_main"),
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("up_main_"))
async def process_upload_main(callback: types.CallbackQuery, state: FSMContext):
    dept_code = callback.data.split("_", 2)[2]
    await state.update_data(department=dept_code)

    if dept_code in SUB_DEPARTMENTS:
        keyboard = []
        for sub_code, sub_name in SUB_DEPARTMENTS[dept_code].items():
            keyboard.append([InlineKeyboardButton(text=sub_name, callback_data=f"up_sub_{sub_code}")])
        keyboard.append([InlineKeyboardButton(text="عودة للأقسام", callback_data="menu_upload")])
        await callback.message.edit_text("اختر الفرع المستهدف:", reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    else:
        await state.update_data(sub_department="none")
        await begin_bulk_upload(callback.message, state)
    await callback.answer()

@dp.callback_query(F.data.startswith("up_sub_"))
async def process_upload_sub(callback: types.CallbackQuery, state: FSMContext):
    sub_code = callback.data.split("_", 2)[2]
    await state.update_data(sub_department=sub_code)
    await begin_bulk_upload(callback.message, state)
    await callback.answer()

async def begin_bulk_upload(message: types.Message, state: FSMContext):
    await state.set_state(BulkUploadState.collecting_files)
    await state.update_data(files_list=[])
    sent_msg = await message.edit_text(
        "وضع الرفع الجماعي مفعل.\nعدد الملفات في السلة: 0 ملف.\n"
        "أرسل ملفات الـ PDF الآن، وعند الانتهاء اضغط على الزر أدناه:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="حفظ وإنهاء الرفع", callback_data="save_bulk_files")],
            [InlineKeyboardButton(text="إلغاء", callback_data="back_home")],
        ]),
    )
    await state.update_data(control_message_id=sent_msg.message_id)

@dp.message(BulkUploadState.collecting_files, F.document)
async def collect_pdf_files(message: types.Message, state: FSMContext):
    document = message.document
    if not document.file_name.lower().endswith(".pdf"):
        try:
            await message.delete()
        except Exception:
            pass
        return

    data = await state.get_data()
    files_list = data.get("files_list", [])
    control_message_id = data.get("control_message_id")

    files_list.append({
        "file_id": document.file_id,
        "file_name": document.file_name,
        "file_unique_id": document.file_unique_id,
    })
    await state.update_data(files_list=files_list)

    text = (
        "وضع الرفع الجماعي مفعل.\n"
        f"عدد الملفات في السلة: {len(files_list)} ملف.\n"
        "أرسل بقية الملفات، وعند الانتهاء اضغط على الزر أدناه:"
    )
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="حفظ وإنهاء الرفع", callback_data="save_bulk_files")],
        [InlineKeyboardButton(text="إلغاء", callback_data="back_home")],
    ])

    if control_message_id:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=control_message_id,
                text=text,
                reply_markup=reply_markup,
            )
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass

@dp.callback_query(BulkUploadState.collecting_files, F.data == "save_bulk_files")
async def save_bulk_files(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    files_list = data.get("files_list", [])
    dept = data.get("department")
    sub_dept = data.get("sub_department", "none")

    if not files_list:
        await callback.answer("لم تقم بإرسال أي ملفات بعد!", show_alert=True)
        return

    inserted = await insert_files(dept, sub_dept, files_list)
    skipped = len(files_list) - inserted
    await state.clear()

    summary = f"تمت إضافة {inserted} ملفاً جديداً بنجاح."
    if skipped:
        summary += f"\n(تم تجاهل {skipped} ملفاً مكرراً)."

    await callback.message.edit_text(
        summary,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="القائمة الرئيسية", callback_data="back_home")]
        ]),
    )
    await callback.answer()

# ============================================================
# تشغيل البوت
# ============================================================

async def main():
    await init_db()
    logger.info("البوت يعمل الآن...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())