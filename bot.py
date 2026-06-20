"""
☕ Бот учёта кофейни — без Google Sheets
Меню управляется командами управляющего прямо в боте.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (InlineKeyboardMarkup, InlineKeyboardButton,
                            ReplyKeyboardMarkup, KeyboardButton)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import database as db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN, parse_mode="HTML")
dp  = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)

# ═══════════════════════════════════════════════════════════
#  FSM STATES
# ═══════════════════════════════════════════════════════════

class RegState(StatesGroup):
    choose_role = State()

class AddDisplay(StatesGroup):
    choose_category = State()
    choose_item     = State()
    enter_quantity  = State()

class WriteoffFlow(StatesGroup):
    choose_item   = State()
    choose_reason = State()

class SaleFlow(StatesGroup):
    choose_item = State()

class CloseShift(StatesGroup):
    confirm = State()

# Управление меню
class MenuAddItem(StatesGroup):
    name      = State()
    category  = State()
    hours     = State()   # только для еды

class MenuEditHours(StatesGroup):
    enter_hours = State()

# ═══════════════════════════════════════════════════════════
#  KEYBOARDS
# ═══════════════════════════════════════════════════════════

def kb_main(role: str, tg_id: int) -> ReplyKeyboardMarkup:
    is_sa = (tg_id == config.SUPERADMIN_ID)
    rows = []
    if role in ("barista", "manager") or is_sa:
        rows.append([KeyboardButton(text="📥 На витрину"),
                     KeyboardButton(text="🗑 Списать")])
        rows.append([KeyboardButton(text="💰 Продажа"),
                     KeyboardButton(text="📋 Витрина")])
        rows.append([KeyboardButton(text="🔚 Закрыть смену")])
    if role == "manager" or is_sa:
        rows.append([KeyboardButton(text="📊 Остатки десертов"),
                     KeyboardButton(text="👥 Сотрудники")])
        rows.append([KeyboardButton(text="🍽 Управление меню")])
    if is_sa:
        rows.append([KeyboardButton(text="🔑 Управляющие")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def kb_inline(buttons: list[tuple]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d)] for t, d in buttons]
    )

def kb_inline_rows(rows: list[list[tuple]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
            for row in rows
        ]
    )

# ═══════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════

def hours_on_display(added_at_str: str) -> float:
    added = datetime.fromisoformat(str(added_at_str))
    return round((datetime.now() - added).total_seconds() / 3600, 1)

def status_emoji(hours_left) -> str:
    if hours_left is None or hours_left < 0: return "🔴"
    if hours_left < 12: return "🟡"
    return "🟢"

async def send_notification(text: str):
    try:
        await bot.send_message(config.NOTIFICATIONS_CHAT_ID, text)
    except Exception as e:
        logger.error(f"Уведомление не отправлено: {e}")

async def send_report(text: str):
    try:
        await bot.send_message(config.REPORTS_CHAT_ID, text)
    except Exception as e:
        logger.error(f"Отчёт не отправлен: {e}")

def get_role(tg_id: int) -> str:
    if tg_id == config.SUPERADMIN_ID:
        return "superadmin"
    user = db.get_user(tg_id)
    return user["role"] if user else None

def can_manage(tg_id: int) -> bool:
    return get_role(tg_id) in ("manager", "superadmin")

def can_work(tg_id: int) -> bool:
    return get_role(tg_id) in ("barista", "manager", "superadmin")

# ═══════════════════════════════════════════════════════════
#  /start — РЕГИСТРАЦИЯ
# ═══════════════════════════════════════════════════════════

@dp.message(Command("start"))
async def cmd_start(msg: types.Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id

    if uid == config.SUPERADMIN_ID:
        db.upsert_user(uid, msg.from_user.username, msg.from_user.full_name)
        await msg.answer("☕ <b>Добро пожаловать, суперадмин!</b>",
                         reply_markup=kb_main("superadmin", uid))
        return

    db.upsert_user(uid, msg.from_user.username, msg.from_user.full_name)
    user = db.get_user(uid)

    if user["role"] == "barista":
        await msg.answer("☕ <b>Привет!</b> Выберите действие:",
                         reply_markup=kb_main("barista", uid))
    elif user["role"] == "manager":
        await msg.answer("☕ <b>Привет, управляющий!</b>",
                         reply_markup=kb_main("manager", uid))
    elif user["role"] == "rejected":
        await msg.answer("⛔ Ваш аккаунт заблокирован. Обратитесь к управляющему.")
    elif user["role"] == "pending":
        await msg.answer("⏳ Заявка уже отправлена. Ожидайте одобрения.")
    else:
        kb = kb_inline([
            ("👨‍🍳 Я бариста",     "reg:barista"),
            ("👔 Я управляющий", "reg:manager"),
        ])
        await msg.answer(
            "☕ <b>Добро пожаловать!</b>\n\nКем вы работаете?",
            reply_markup=kb
        )
        await state.set_state(RegState.choose_role)

@dp.callback_query(RegState.choose_role, F.data.startswith("reg:"))
async def reg_role_chosen(call: types.CallbackQuery, state: FSMContext):
    role  = call.data.split(":")[1]
    uid   = call.from_user.id
    name  = call.from_user.full_name
    un    = call.from_user.username or "—"
    await state.clear()
    await call.message.edit_reply_markup()

    role_label = "бариста" if role == "barista" else "управляющий"
    await call.message.answer(f"⏳ Заявка отправлена! Роль: <b>{role_label}</b>\nОжидайте одобрения.")

    text = (
        f"🆕 <b>Новая заявка — {role_label}</b>\n"
        f"Имя: {name}\n@{un} | ID: <code>{uid}</code>"
    )
    kb = kb_inline([
        ("✅ Одобрить", f"approve_staff:{uid}:{role}"),
        ("❌ Отклонить", f"reject_staff:{uid}"),
    ])

    if role == "barista":
        notify_ids = [u["tg_id"] for u in db.get_users_by_role("manager")] + [config.SUPERADMIN_ID]
    else:
        notify_ids = [config.SUPERADMIN_ID]

    for mid in notify_ids:
        try:
            await bot.send_message(mid, text, reply_markup=kb)
        except Exception:
            pass

# ── Одобрение / отклонение ───────────────────────────────

@dp.callback_query(F.data.startswith("approve_staff:"))
async def approve_staff(call: types.CallbackQuery):
    if not can_manage(call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True)
        return
    _, uid_str, role = call.data.split(":")
    uid = int(uid_str)
    if role == "manager" and call.from_user.id != config.SUPERADMIN_ID:
        await call.answer("⛔ Только суперадмин одобряет управляющих.", show_alert=True)
        return
    db.set_user_role(uid, role, call.from_user.id)
    await call.message.edit_reply_markup()
    role_label = "бариста" if role == "barista" else "управляющий"
    await call.message.answer(f"✅ Одобрен как <b>{role_label}</b>.")
    try:
        await bot.send_message(uid,
            f"✅ <b>Заявка одобрена!</b> Роль: <b>{role_label}</b>\nНажмите /start")
    except Exception:
        pass

@dp.callback_query(F.data.startswith("reject_staff:"))
async def reject_staff(call: types.CallbackQuery):
    if not can_manage(call.from_user.id):
        await call.answer("⛔ Нет прав.", show_alert=True)
        return
    uid = int(call.data.split(":")[1])
    user = db.get_user(uid)
    if user and user["role"] == "manager" and call.from_user.id != config.SUPERADMIN_ID:
        await call.answer("⛔ Только суперадмин.", show_alert=True)
        return
    db.set_user_role(uid, "rejected", call.from_user.id)
    await call.message.edit_reply_markup()
    await call.message.answer("❌ Пользователь отклонён.")
    try:
        await bot.send_message(uid, "❌ Заявка отклонена. Обратитесь к управляющему.")
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════
#  СОТРУДНИКИ
# ═══════════════════════════════════════════════════════════

@dp.message(F.text == "👥 Сотрудники")
async def manage_staff(msg: types.Message):
    if not can_manage(msg.from_user.id):
        return
    staff = db.get_all_staff()
    if not staff:
        await msg.answer("Сотрудников пока нет.")
        return
    buttons = []
    for u in staff:
        label = "👔" if u["role"] == "manager" else "👨‍🍳"
        buttons.append((f"{label} {u['full_name']}", f"staff_action:{u['tg_id']}"))
    await msg.answer("Список сотрудников:", reply_markup=kb_inline(buttons))

@dp.callback_query(F.data.startswith("staff_action:"))
async def staff_action(call: types.CallbackQuery):
    if not can_manage(call.from_user.id):
        return
    uid  = int(call.data.split(":")[1])
    user = db.get_user(uid)
    if not user:
        await call.answer("Пользователь не найден.")
        return
    if user["role"] == "manager" and call.from_user.id != config.SUPERADMIN_ID:
        await call.answer("⛔ Управляющих может удалять только суперадмин.", show_alert=True)
        return
    role_label = "управляющий" if user["role"] == "manager" else "бариста"
    await call.message.answer(
        f"👤 <b>{user['full_name']}</b>\nРоль: {role_label}",
        reply_markup=kb_inline([("🔥 Заблокировать", f"fire_staff:{uid}")])
    )

@dp.callback_query(F.data.startswith("fire_staff:"))
async def fire_staff(call: types.CallbackQuery):
    if not can_manage(call.from_user.id):
        return
    uid  = int(call.data.split(":")[1])
    user = db.get_user(uid)
    if user["role"] == "manager" and call.from_user.id != config.SUPERADMIN_ID:
        await call.answer("⛔ Только суперадмин.", show_alert=True)
        return
    db.set_user_role(uid, "rejected", call.from_user.id)
    await call.message.edit_reply_markup()
    await call.message.answer(f"🔥 {user['full_name']} заблокирован.")
    try:
        await bot.send_message(uid, "❌ Ваш доступ к боту отозван.")
    except Exception:
        pass

@dp.message(F.text == "🔑 Управляющие")
async def manage_managers(msg: types.Message):
    if msg.from_user.id != config.SUPERADMIN_ID:
        return
    managers = db.get_users_by_role("manager")
    if not managers:
        await msg.answer("Управляющих пока нет.")
        return
    buttons = [(f"👔 {u['full_name']}", f"staff_action:{u['tg_id']}") for u in managers]
    await msg.answer("Список управляющих:", reply_markup=kb_inline(buttons))

# ═══════════════════════════════════════════════════════════
#  🍽 УПРАВЛЕНИЕ МЕНЮ (только управляющий / суперадмин)
# ═══════════════════════════════════════════════════════════

@dp.message(F.text == "🍽 Управление меню")
async def menu_management(msg: types.Message):
    if not can_manage(msg.from_user.id):
        return
    await msg.answer(
        "🍽 <b>Управление меню</b>\n\nВыберите действие:",
        reply_markup=kb_inline([
            ("➕ Добавить позицию",  "menu:add"),
            ("❌ Удалить позицию",   "menu:remove"),
            ("✏️ Изменить срок",     "menu:edit_hours"),
            ("📜 Показать меню",     "menu:show"),
        ])
    )

# ── ПОКАЗАТЬ МЕНЮ ─────────────────────────────────────────

@dp.callback_query(F.data == "menu:show")
async def menu_show(call: types.CallbackQuery):
    if not can_manage(call.from_user.id):
        return
    items = db.get_menu_items()
    if not items:
        await call.message.answer("Меню пустое. Добавьте позиции.")
        return
    desserts = [i for i in items if i["category"] == "dessert"]
    foods    = [i for i in items if i["category"] == "food"]
    lines    = ["📜 <b>Текущее меню:</b>\n"]
    if desserts:
        lines.append("🍰 <b>Десерты (120 ч.):</b>")
        for i in desserts:
            lines.append(f"  • {i['name']}")
    if foods:
        lines.append("\n🍽 <b>Еда:</b>")
        for i in foods:
            lines.append(f"  • {i['name']} — {i['shelf_hours']} ч.")
    await call.message.answer("\n".join(lines))

# ── ДОБАВИТЬ ПОЗИЦИЮ ──────────────────────────────────────

@dp.callback_query(F.data == "menu:add")
async def menu_add_start(call: types.CallbackQuery, state: FSMContext):
    if not can_manage(call.from_user.id):
        return
    await call.message.answer("Введите название новой позиции:")
    await state.set_state(MenuAddItem.name)

@dp.message(MenuAddItem.name)
async def menu_add_name(msg: types.Message, state: FSMContext):
    name = msg.text.strip()
    if not name:
        await msg.answer("Название не может быть пустым. Попробуйте ещё раз:")
        return
    # Проверяем дубликат
    existing = db.get_menu_items()
    if any(i["name"].lower() == name.lower() for i in existing):
        await msg.answer(f"❌ Позиция <b>{name}</b> уже есть в меню.", reply_markup=kb_inline([
            ("🍽 Управление меню", "menu:show")
        ]))
        await state.clear()
        return
    await state.update_data(name=name)
    await msg.answer(
        f"Позиция: <b>{name}</b>\n\nВыберите категорию:",
        reply_markup=kb_inline([
            ("🍰 Десерт (120 ч.)", "newcat:dessert"),
            ("🍽 Еда (задать срок)", "newcat:food"),
        ])
    )
    await state.set_state(MenuAddItem.category)

@dp.callback_query(MenuAddItem.category, F.data.startswith("newcat:"))
async def menu_add_category(call: types.CallbackQuery, state: FSMContext):
    category = call.data.split(":")[1]
    await state.update_data(category=category)
    await call.message.edit_reply_markup()
    if category == "dessert":
        data = await state.get_data()
        await state.clear()
        db.add_menu_item(data["name"], "dessert", 120)
        await call.message.answer(
            f"✅ <b>{data['name']}</b> добавлен как десерт (120 ч.)"
        )
    else:
        await call.message.answer(
            "Введите срок хранения в часах\n"
            "<i>Например: 24 (для суток), 8 (для 8 часов)</i>"
        )
        await state.set_state(MenuAddItem.hours)

@dp.message(MenuAddItem.hours)
async def menu_add_hours(msg: types.Message, state: FSMContext):
    try:
        hours = int(msg.text.strip())
        if hours < 1 or hours > 9999:
            raise ValueError
    except ValueError:
        await msg.answer("Введите целое число от 1 до 9999:")
        return
    data = await state.get_data()
    await state.clear()
    db.add_menu_item(data["name"], "food", hours)
    await msg.answer(f"✅ <b>{data['name']}</b> добавлен в меню (срок: {hours} ч.)")

# ── УДАЛИТЬ ПОЗИЦИЮ ───────────────────────────────────────

@dp.callback_query(F.data == "menu:remove")
async def menu_remove_start(call: types.CallbackQuery):
    if not can_manage(call.from_user.id):
        return
    items = db.get_menu_items()
    if not items:
        await call.message.answer("Меню пустое.")
        return
    buttons = []
    for i in items:
        em = "🍰" if i["category"] == "dessert" else "🍽"
        buttons.append((f"{em} {i['name']}", f"menu_rm:{i['id']}"))
    await call.message.answer("Какую позицию удалить?", reply_markup=kb_inline(buttons))

@dp.callback_query(F.data.startswith("menu_rm:"))
async def menu_remove_confirm(call: types.CallbackQuery):
    if not can_manage(call.from_user.id):
        return
    item_id = int(call.data.split(":")[1])
    item = db.get_menu_item_by_id(item_id)
    if not item:
        await call.answer("Позиция не найдена.")
        return
    await call.message.edit_reply_markup()
    await call.message.answer(
        f"Удалить <b>{item['name']}</b> из меню?",
        reply_markup=kb_inline_rows([
            [("✅ Да, удалить", f"menu_rm_ok:{item_id}"),
             ("❌ Отмена",      "menu_rm_cancel")]
        ])
    )

@dp.callback_query(F.data.startswith("menu_rm_ok:"))
async def menu_remove_ok(call: types.CallbackQuery):
    if not can_manage(call.from_user.id):
        return
    item_id = int(call.data.split(":")[1])
    item = db.get_menu_item_by_id(item_id)
    db.deactivate_menu_item(item_id)
    await call.message.edit_reply_markup()
    await call.message.answer(f"❌ <b>{item['name']}</b> удалён из меню.")

@dp.callback_query(F.data == "menu_rm_cancel")
async def menu_remove_cancel(call: types.CallbackQuery):
    await call.message.edit_reply_markup()
    await call.message.answer("Отмена.")

# ── ИЗМЕНИТЬ СРОК ХРАНЕНИЯ ────────────────────────────────

@dp.callback_query(F.data == "menu:edit_hours")
async def menu_edit_hours_start(call: types.CallbackQuery, state: FSMContext):
    if not can_manage(call.from_user.id):
        return
    # Только еда — у десертов срок фиксирован 120ч
    items = db.get_menu_items("food")
    if not items:
        await call.message.answer("Позиций еды в меню нет.")
        return
    buttons = [(f"🍽 {i['name']} ({i['shelf_hours']} ч.)", f"edit_hrs:{i['id']}") for i in items]
    await call.message.answer("У какой позиции изменить срок?", reply_markup=kb_inline(buttons))
    await state.set_state(MenuEditHours.enter_hours)

@dp.callback_query(MenuEditHours.enter_hours, F.data.startswith("edit_hrs:"))
async def menu_edit_hours_chosen(call: types.CallbackQuery, state: FSMContext):
    item_id = int(call.data.split(":")[1])
    item = db.get_menu_item_by_id(item_id)
    await state.update_data(item_id=item_id, item_name=item["name"])
    await call.message.edit_reply_markup()
    await call.message.answer(
        f"<b>{item['name']}</b>\nТекущий срок: {item['shelf_hours']} ч.\n\nВведите новый срок (часов):"
    )

@dp.message(MenuEditHours.enter_hours)
async def menu_edit_hours_enter(msg: types.Message, state: FSMContext):
    try:
        hours = int(msg.text.strip())
        if hours < 1 or hours > 9999:
            raise ValueError
    except ValueError:
        await msg.answer("Введите целое число от 1 до 9999:")
        return
    data = await state.get_data()
    await state.clear()
    db.update_menu_item_hours(data["item_id"], hours)
    await msg.answer(f"✅ <b>{data['item_name']}</b> — срок обновлён: {hours} ч.")

# ═══════════════════════════════════════════════════════════
#  📥 НА ВИТРИНУ
# ═══════════════════════════════════════════════════════════

@dp.message(F.text == "📥 На витрину")
async def add_to_display(msg: types.Message, state: FSMContext):
    if not can_work(msg.from_user.id):
        await msg.answer("⛔ Нет доступа.")
        return
    await msg.answer("Что ставим?", reply_markup=kb_inline([
        ("🍰 Десерт", "cat:dessert"),
        ("🍽 Еда",    "cat:food"),
    ]))
    await state.set_state(AddDisplay.choose_category)

@dp.callback_query(AddDisplay.choose_category, F.data.startswith("cat:"))
async def display_category(call: types.CallbackQuery, state: FSMContext):
    category = call.data.split(":")[1]
    items = db.get_menu_items(category)
    if not items:
        cat_label = "десертов" if category == "dessert" else "еды"
        await call.message.edit_text(
            f"❌ В меню нет {cat_label}.\nУправляющий должен добавить позиции через «🍽 Управление меню»."
        )
        await state.clear()
        return
    em = "🍰" if category == "dessert" else "🍽"
    buttons = [(f"{em} {r['name']}", f"disp_item:{r['id']}") for r in items]
    await call.message.edit_text("Выберите позицию:", reply_markup=kb_inline(buttons))
    await state.set_state(AddDisplay.choose_item)

@dp.callback_query(AddDisplay.choose_item, F.data.startswith("disp_item:"))
async def display_item_chosen(call: types.CallbackQuery, state: FSMContext):
    item_id = int(call.data.split(":")[1])
    item = db.get_menu_item_by_id(item_id)
    await state.update_data(item_id=item_id, item_name=item["name"],
                             shelf_hours=item["shelf_hours"],
                             category=item["category"])
    await call.message.edit_text(
        f"<b>{item['name']}</b> — срок {item['shelf_hours']} ч.\n\n"
        f"Сколько штук ставим на витрину?"
    )
    await state.set_state(AddDisplay.enter_quantity)

@dp.message(AddDisplay.enter_quantity)
async def display_quantity(msg: types.Message, state: FSMContext):
    try:
        qty = int(msg.text.strip())
        if qty < 1 or qty > 99:
            raise ValueError
    except ValueError:
        await msg.answer("Введите число от 1 до 99:")
        return
    data = await state.get_data()
    await state.clear()
    db.add_display_items(data["item_id"], data["shelf_hours"], msg.from_user.id, qty)
    expires = datetime.now() + timedelta(hours=data["shelf_hours"])
    role = get_role(msg.from_user.id)
    await msg.answer(
        f"✅ <b>{data['item_name']}</b> × {qty} шт. — на витрине\n"
        f"⏰ Годно до: {expires.strftime('%H:%M, %d.%m.%Y')}",
        reply_markup=kb_main(role, msg.from_user.id)
    )

# ═══════════════════════════════════════════════════════════
#  🗑 СПИСАНИЕ
# ═══════════════════════════════════════════════════════════

@dp.message(F.text == "🗑 Списать")
async def writeoff_start(msg: types.Message, state: FSMContext):
    if not can_work(msg.from_user.id):
        return
    items = db.get_active_display_items()
    if not items:
        await msg.answer("На витрине ничего нет.")
        return
    buttons = []
    for r in items:
        em = status_emoji(r["hours_left"])
        added = datetime.fromisoformat(str(r["added_at"])).strftime("%H:%M %d.%m")
        h = r["hours_left"]
        h_label = f"{h} ч." if h and h >= 0 else "ПРОСРОЧЕНО"
        buttons.append((f"{em} {r['name']} — {h_label} (с {added})", f"wo:{r['id']}"))
    await msg.answer("Что списываем?", reply_markup=kb_inline(buttons))
    await state.set_state(WriteoffFlow.choose_item)

@dp.callback_query(WriteoffFlow.choose_item, F.data.startswith("wo:"))
async def writeoff_item(call: types.CallbackQuery, state: FSMContext):
    disp_id = int(call.data.split(":")[1])
    await state.update_data(disp_id=disp_id)
    await call.message.edit_reply_markup()
    await call.message.answer("Причина:", reply_markup=kb_inline([
        ("⏰ Истёк срок",       "reason:expired"),
        ("💔 Брак / испорчено", "reason:defect"),
        ("📝 Другое",           "reason:other"),
    ]))
    await state.set_state(WriteoffFlow.choose_reason)

@dp.callback_query(WriteoffFlow.choose_reason, F.data.startswith("reason:"))
async def writeoff_reason(call: types.CallbackQuery, state: FSMContext):
    reasons = {"expired": "Истёк срок", "defect": "Брак / испорчено", "other": "Другое"}
    reason  = reasons[call.data.split(":")[1]]
    data    = await state.get_data()
    await state.clear()
    await call.message.edit_reply_markup()

    uid      = call.from_user.id
    user     = db.get_user(uid)
    emp_name = user["full_name"] if user else str(uid)
    role     = get_role(uid)

    items = db.get_active_display_items()
    item  = next((i for i in items if i["id"] == data["disp_id"]), None)
    if not item:
        await call.message.answer("❌ Позиция уже списана.")
        return

    hours = hours_on_display(item["added_at"])
    db.close_display_item(data["disp_id"], "written_off")
    db.add_operation(data["disp_id"], item["name"], uid, emp_name, "writeoff", reason, hours)

    await call.message.answer(
        f"🗑 <b>Списано:</b> {item['name']}\n"
        f"Причина: {reason} | На витрине: {hours} ч.",
        reply_markup=kb_main(role, uid)
    )
    await send_notification(
        f"🗑 <b>Списание</b>\n"
        f"Позиция: <b>{item['name']}</b>\n"
        f"Причина: {reason}\n"
        f"Сотрудник: {emp_name}\n"
        f"На витрине: {hours} ч.\n"
        f"🕐 {datetime.now().strftime('%H:%M, %d.%m.%Y')}"
    )

# ═══════════════════════════════════════════════════════════
#  💰 ПРОДАЖА
# ═══════════════════════════════════════════════════════════

@dp.message(F.text == "💰 Продажа")
async def sale_start(msg: types.Message, state: FSMContext):
    if not can_work(msg.from_user.id):
        return
    items = db.get_active_display_items()
    if not items:
        await msg.answer("На витрине ничего нет.")
        return
    em_map = {"dessert": "🍰", "food": "🍽"}
    buttons = [(f"{em_map.get(r['category'],'•')} {r['name']}", f"sale:{r['id']}") for r in items]
    await msg.answer("Что продали?", reply_markup=kb_inline(buttons))
    await state.set_state(SaleFlow.choose_item)

@dp.callback_query(SaleFlow.choose_item, F.data.startswith("sale:"))
async def sale_item(call: types.CallbackQuery, state: FSMContext):
    disp_id = int(call.data.split(":")[1])
    await state.clear()
    await call.message.edit_reply_markup()

    uid      = call.from_user.id
    user     = db.get_user(uid)
    emp_name = user["full_name"] if user else str(uid)
    role     = get_role(uid)

    items = db.get_active_display_items()
    item  = next((i for i in items if i["id"] == disp_id), None)
    if not item:
        await call.message.answer("❌ Позиция уже продана/списана.")
        return

    hours = hours_on_display(item["added_at"])
    db.close_display_item(disp_id, "sold")
    db.add_operation(disp_id, item["name"], uid, emp_name, "sale", None, hours)

    await call.message.answer(
        f"💰 <b>Продано:</b> {item['name']}",
        reply_markup=kb_main(role, uid)
    )
    await send_notification(
        f"💰 <b>Продажа</b>\n"
        f"Позиция: <b>{item['name']}</b>\n"
        f"Сотрудник: {emp_name}\n"
        f"🕐 {datetime.now().strftime('%H:%M, %d.%m.%Y')}"
    )

# ═══════════════════════════════════════════════════════════
#  📋 ВИТРИНА
# ═══════════════════════════════════════════════════════════

@dp.message(F.text == "📋 Витрина")
async def show_display(msg: types.Message):
    if not can_work(msg.from_user.id):
        return
    items = db.get_active_display_items()
    if not items:
        await msg.answer("Витрина пуста 🫥")
        return
    lines = ["<b>📋 На витрине:</b>\n"]
    for r in items:
        em      = status_emoji(r["hours_left"])
        added   = datetime.fromisoformat(str(r["added_at"])).strftime("%H:%M %d.%m")
        expires = datetime.fromisoformat(str(r["expires_at"])).strftime("%H:%M %d.%m")
        h       = r["hours_left"]
        h_label = f"{h} ч." if h and h >= 0 else "⚠️ ПРОСРОЧЕНО"
        cat_em  = "🍰" if r["category"] == "dessert" else "🍽"
        lines.append(
            f"{cat_em} <b>{r['name']}</b> {em} {h_label}\n"
            f"   📥 {added} → ⏰ {expires} | {r['added_by']}"
        )
    await msg.answer("\n".join(lines))

# ═══════════════════════════════════════════════════════════
#  📊 ОСТАТКИ ДЕСЕРТОВ
# ═══════════════════════════════════════════════════════════

@dp.message(F.text == "📊 Остатки десертов")
async def dessert_stock(msg: types.Message):
    if not can_manage(msg.from_user.id):
        return
    rows = db.get_dessert_stock()
    if not rows:
        await msg.answer("🍰 На витрине нет десертов.")
        return
    lines = ["<b>📊 Остатки десертов:</b>\n"]
    for r in rows:
        expired_note = f" | ⚠️ <b>{r['expired_qty']} просрочено</b>" if r["expired_qty"] else ""
        h_info = (f"{r['min_hours']} ч." if r["min_hours"] == r["max_hours"]
                  else f"{r['min_hours']}–{r['max_hours']} ч.")
        lines.append(f"🍰 <b>{r['name']}</b> — {r['qty']} шт., {h_info} осталось{expired_note}")
    total = sum(r["qty"] for r in rows)
    lines.append(f"\n📦 Итого: <b>{total} шт.</b>")
    await msg.answer("\n".join(lines))

# ═══════════════════════════════════════════════════════════
#  🔚 ЗАКРЫТЬ СМЕНУ
# ═══════════════════════════════════════════════════════════

@dp.message(F.text == "🔚 Закрыть смену")
async def close_shift_start(msg: types.Message, state: FSMContext):
    if not can_work(msg.from_user.id):
        return
    await msg.answer(
        "Подтвердите закрытие смены:",
        reply_markup=kb_inline([("✅ Подтвердить", "confirm_close")])
    )
    await state.set_state(CloseShift.confirm)

@dp.callback_query(CloseShift.confirm, F.data == "confirm_close")
async def close_shift_confirmed(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_reply_markup()
    uid      = call.from_user.id
    user     = db.get_user(uid)
    emp_name = user["full_name"] if user else str(uid)
    role     = get_role(uid)
    await call.message.answer("✅ Смена закрыта. Отправляю отчёт...")
    await _send_shift_report(emp_name)
    await call.message.answer("📊 Отчёт отправлен.", reply_markup=kb_main(role, uid))

async def _send_shift_report(closed_by: str):
    today = datetime.now().strftime("%d.%m.%Y")
    ops   = db.get_today_operations()
    if not ops:
        await send_report(
            f"📊 <b>Отчёт за {today}</b>\nОпераций не было. Закрыл: {closed_by}"
        )
        return
    writeoffs = [o for o in ops if o["op_type"] == "writeoff"]
    sales     = [o for o in ops if o["op_type"] == "sale"]
    lines     = [f"📊 <b>Отчёт за {today}</b> | Закрыл: {closed_by}\n"]
    if writeoffs:
        lines.append(f"🗑 <b>Списания ({len(writeoffs)}):</b>")
        for o in writeoffs:
            t = datetime.fromisoformat(str(o["created_at"])).strftime("%H:%M")
            lines.append(f"  • {o['item_name']} — {o['reason']} ({o['hours_on_display']} ч.) [{o['employee_name']}, {t}]")
    if sales:
        lines.append(f"\n💰 <b>Продажи ({len(sales)}):</b>")
        for o in sales:
            t = datetime.fromisoformat(str(o["created_at"])).strftime("%H:%M")
            lines.append(f"  • {o['item_name']} [{o['employee_name']}, {t}]")
    lines.append(f"\n📦 Итого: {len(writeoffs)} списаний, {len(sales)} продаж")
    await send_report("\n".join(lines))

# ═══════════════════════════════════════════════════════════
#  SCHEDULER — проверка сроков каждые 30 минут
# ═══════════════════════════════════════════════════════════

async def check_expiring():
    items = db.get_expiring_items()
    for item in items:
        expires    = datetime.fromisoformat(str(item["expires_at"]))
        hours_left = (expires - datetime.now()).total_seconds() / 3600
        sign       = "ПРОСРОЧЕНО" if hours_left < 0 else f"через {round(abs(hours_left), 1)} ч."
        await send_notification(
            f"⚠️ <b>Срок истекает!</b>\n"
            f"Позиция: <b>{item['name']}</b>\n"
            f"Годна до: {expires.strftime('%H:%M %d.%m')} ({sign})\n"
            f"Добавил: {item['added_by']}\n"
            f"Нажмите <b>🗑 Списать</b> в боте."
        )
        db.mark_reminded(item["id"])

# ═══════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════

async def main():
    db.init_db()
    scheduler.add_job(check_expiring, "interval", minutes=30)
    scheduler.start()
    logger.info("Бот запущен 🚀")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
