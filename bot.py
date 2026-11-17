"""
Бот-магазин (кав'ярня) — етап 3: адмінка.

Власник (ADMIN_ID) командою /admin може:
  - додавати категорії і товари з фото
  - змінювати ціну і фото
  - ховати / повертати товари
  - бачити необроблені замовлення
"""

import asyncio
import logging
import os
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db

TOKEN = os.getenv("BOT_TOKEN")

# Власник магазину — йому приходять замовлення.
# strip() прибирає випадкові пробіли, а потім перетворюємо текст на число,
# щоб порівнювати число з числом (callback.from_user.id — теж число).
_admin = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(_admin) if _admin.isdigit() else None

# Фільтр «це власник». Додаємо його до всіх обробників адмінки:
# якщо пише хтось інший — обробник просто не спрацює.
IS_ADMIN = F.from_user.id == ADMIN_ID

dp = Dispatcher()


# ---------- СТАНИ ----------

class Checkout(StatesGroup):
    name = State()
    phone = State()
    delivery = State()
    address = State()
    confirm = State()


class AddCategory(StatesGroup):
    name = State()


class AddProduct(StatesGroup):
    category = State()
    name = State()
    description = State()
    price = State()
    photo = State()


class EditProduct(StatesGroup):
    price = State()
    photo = State()


# ---------- ДАНІ ----------

STATUSES = {
    "new": "🆕 Нове",
    "accepted": "👨‍🍳 Готується",
    "done": "✅ Виконано",
    "rejected": "❌ Відхилено",
}

DELIVERY = {
    "delivery": "🚚 Доставка",
    "pickup": "🏃 Самовивіз",
}


# ---------- КЛАВІАТУРИ ----------

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🛍 Каталог"), KeyboardButton(text="🛒 Кошик")],
        [KeyboardButton(text="📦 Мої замовлення"), KeyboardButton(text="ℹ️ Про нас")],
    ],
    resize_keyboard=True,
)

cancel_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Скасувати")]],
    resize_keyboard=True,
)

phone_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📱 Поділитися номером", request_contact=True)],
        [KeyboardButton(text="❌ Скасувати")],
    ],
    resize_keyboard=True,
)


def categories_kb():
    builder = InlineKeyboardBuilder()
    for cat in db.get_categories():
        builder.button(text=cat["name"], callback_data=f"cat:{cat['id']}")
    builder.adjust(1)
    return builder.as_markup()


def products_kb(category_id: int):
    builder = InlineKeyboardBuilder()
    for p in db.get_products(category_id):
        builder.button(text=f"{p['name']} — {p['price']} грн", callback_data=f"prod:{p['id']}")
    builder.button(text="⬅️ До категорій", callback_data="catalog")
    builder.adjust(1)
    return builder.as_markup()


def product_kb(product):
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Додати в кошик", callback_data=f"add:{product['id']}")
    builder.button(text="⬅️ Назад", callback_data=f"cat:{product['category_id']}")
    builder.adjust(1)
    return builder.as_markup()


def delivery_kb():
    builder = InlineKeyboardBuilder()
    for key, label in DELIVERY.items():
        builder.button(text=label, callback_data=f"dlv:{key}")
    builder.adjust(2)
    return builder.as_markup()


def confirm_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Підтвердити", callback_data="order:yes")
    builder.button(text="❌ Скасувати", callback_data="order:no")
    builder.adjust(2)
    return builder.as_markup()


def admin_order_kb(order_id: int, status: str):
    """Кнопки для власника — залежать від поточного статусу."""
    builder = InlineKeyboardBuilder()
    if status == "new":
        builder.button(text="👨‍🍳 Прийняти", callback_data=f"st:{order_id}:accepted")
        builder.button(text="❌ Відхилити", callback_data=f"st:{order_id}:rejected")
    elif status == "accepted":
        builder.button(text="✅ Виконано", callback_data=f"st:{order_id}:done")
    builder.adjust(2)
    return builder.as_markup()


# --- клавіатури адмінки ---

def admin_menu_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Додати товар", callback_data="adm:add")
    builder.button(text="✏️ Редагувати товари", callback_data="adm:edit")
    builder.button(text="📁 Додати категорію", callback_data="adm:addcat")
    builder.button(text="📋 Необроблені замовлення", callback_data="adm:orders")
    builder.adjust(1)
    return builder.as_markup()


def admin_categories_kb(prefix: str):
    """Список категорій для адміна. prefix визначає, що буде після вибору."""
    builder = InlineKeyboardBuilder()
    for cat in db.get_categories():
        builder.button(text=cat["name"], callback_data=f"{prefix}:{cat['id']}")
    builder.button(text="⬅️ Меню адмінки", callback_data="adm:menu")
    builder.adjust(1)
    return builder.as_markup()


def admin_products_kb(category_id: int):
    builder = InlineKeyboardBuilder()
    for p in db.get_products(category_id, include_hidden=True):
        mark = "" if p["active"] else "🙈 "
        builder.button(text=f"{mark}{p['name']} — {p['price']} грн", callback_data=f"adm_prod:{p['id']}")
    builder.button(text="⬅️ До категорій", callback_data="adm:edit")
    builder.adjust(1)
    return builder.as_markup()


def admin_product_kb(product):
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Змінити ціну", callback_data=f"adm_price:{product['id']}")
    builder.button(text="🖼 Змінити фото", callback_data=f"adm_photo:{product['id']}")
    toggle_text = "🙈 Сховати з каталогу" if product["active"] else "👁 Повернути в каталог"
    builder.button(text=toggle_text, callback_data=f"adm_toggle:{product['id']}")
    builder.button(text="⬅️ До товарів", callback_data=f"adm_ecat:{product['category_id']}")
    builder.adjust(2, 1, 1)  # ряди: 2 кнопки, 1, 1
    return builder.as_markup()


def skip_photo_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="⏭ Без фото", callback_data="adm_skip_photo")
    return builder.as_markup()


# ---------- ДОПОМІЖНЕ ----------

async def show_screen(callback: CallbackQuery, text: str, markup=None, photo_id=None):
    """
    Показує «екран» на місці старого повідомлення.

    Telegram не вміє перетворити текстове повідомлення на фото (і навпаки)
    через edit_text. Тому якщо фото є зараз або було раніше — видаляємо старе
    повідомлення і надсилаємо нове. Інакше — просто редагуємо текст.
    """
    msg = callback.message
    if photo_id is None and not msg.photo:
        await msg.edit_text(text, reply_markup=markup, parse_mode="HTML")
        return
    try:
        await msg.delete()
    except Exception:
        pass  # повідомлення могло бути вже видалене — не страшно
    if photo_id:
        await msg.answer_photo(photo_id, caption=text, reply_markup=markup, parse_mode="HTML")
    else:
        await msg.answer(text, reply_markup=markup, parse_mode="HTML")


def product_card_text(product) -> str:
    # escape() захищає від символів < > &, які зламали б HTML-розмітку
    return (
        f"<b>{escape(product['name'])}</b>\n\n"
        f"{escape(product['description'])}\n\n"
        f"💰 {product['price']} грн"
    )


def admin_product_text(product) -> str:
    category = db.get_category(product["category_id"])
    return (
        product_card_text(product)
        + f"\n\n📁 {escape(category['name'])}"
        + f"\n🖼 Фото: {'є' if product['photo_id'] else 'немає'}"
        + f"\nСтатус: {'👁 у каталозі' if product['active'] else '🙈 приховано'}"
    )


def cart_view(user_id: int):
    """Повертає текст кошика і кнопки до нього."""
    items = db.get_cart(user_id)
    if not items:
        return "🛒 Кошик порожній.\nЗазирни в «🛍 Каталог» 🙂", None

    lines = []
    total = 0
    builder = InlineKeyboardBuilder()
    for item in items:
        subtotal = item["price"] * item["qty"]
        total += subtotal
        lines.append(f"• {escape(item['name'])} × {item['qty']} = {subtotal} грн")
        builder.row(
            InlineKeyboardButton(text="➖", callback_data=f"dec:{item['id']}"),
            InlineKeyboardButton(text=f"{item['name']} ×{item['qty']}", callback_data="noop"),
            InlineKeyboardButton(text="➕", callback_data=f"inc:{item['id']}"),
        )
    builder.row(
        InlineKeyboardButton(text="🗑 Очистити", callback_data="clear"),
        InlineKeyboardButton(text="✅ Оформити", callback_data="checkout"),
    )

    text = "🛒 Твій кошик:\n\n" + "\n".join(lines) + f"\n\n💰 Разом: {total} грн"
    return text, builder.as_markup()


def order_text(order_id: int, for_admin: bool = False) -> str:
    """Текст замовлення — для клієнта або для власника."""
    order = db.get_order(order_id)
    items = db.get_order_items(order_id)

    lines = [f"Замовлення #{order['id']} • {order['created_at']}", ""]
    for item in items:
        lines.append(f"• {escape(item['name'])} × {item['qty']} = {item['price'] * item['qty']} грн")
    lines.append(f"\n💰 Разом: {order['total']} грн")
    lines.append(DELIVERY[order["delivery"]])
    if order["address"]:
        lines.append(f"📍 {escape(order['address'])}")
    if for_admin:
        lines.append(f"👤 {escape(order['name'])} (@{order['username'] or '—'})")
        lines.append(f"📞 {order['phone']}")
    lines.append(f"\nСтатус: {STATUSES[order['status']]}")
    return "\n".join(lines)


async def notify_admin(bot: Bot, text: str, reply_markup=None):
    if ADMIN_ID is None:
        return
    try:
        await bot.send_message(ADMIN_ID, text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logging.warning("Не вдалося надіслати повідомлення адміну: %s", e)


# ---------- КОМАНДИ ТА МЕНЮ ----------

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    text = (
        f"Привіт, {escape(message.from_user.first_name)}! ☕\n"
        "Це кав'ярня в Telegram. Обирай, замовляй — ми приготуємо."
    )
    if message.from_user.id == ADMIN_ID:
        text += "\n\n🔧 Ти власник магазину. Керування — командою /admin"
    await message.answer(text, reply_markup=main_menu, parse_mode="HTML")


@dp.message(StateFilter("*"), F.text == "❌ Скасувати")
async def cancel_any(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Скасовано ✅", reply_markup=main_menu)


@dp.message(F.text == "ℹ️ Про нас")
async def about(message: Message):
    await message.answer(
        "☕ Кав'ярня «Demo Coffee»\n"
        "📍 Київ, вул. Хрещатик, 1\n"
        "🕙 Щодня 08:00–21:00\n"
        "📞 +380 00 000 00 00"
    )


@dp.message(F.text == "🛍 Каталог")
async def catalog(message: Message):
    await message.answer("Обери категорію:", reply_markup=categories_kb())


@dp.message(F.text == "🛒 Кошик")
async def cart(message: Message):
    text, markup = cart_view(message.from_user.id)
    await message.answer(text, reply_markup=markup, parse_mode="HTML")


@dp.message(F.text == "📦 Мої замовлення")
async def my_orders(message: Message):
    orders = db.get_user_orders(message.from_user.id)
    if not orders:
        await message.answer("У тебе ще немає замовлень 🙂")
        return
    for order in orders:
        await message.answer(order_text(order["id"]), parse_mode="HTML")


# ---------- КАТАЛОГ (inline) ----------

@dp.callback_query(F.data == "catalog")
async def cb_catalog(callback: CallbackQuery):
    await show_screen(callback, "Обери категорію:", categories_kb())
    await callback.answer()


@dp.callback_query(F.data.startswith("cat:"))
async def cb_category(callback: CallbackQuery):
    category_id = int(callback.data.split(":", 1)[1])
    await show_screen(callback, "Обери товар:", products_kb(category_id))
    await callback.answer()


@dp.callback_query(F.data.startswith("prod:"))
async def cb_product(callback: CallbackQuery):
    product = db.get_product(int(callback.data.split(":", 1)[1]))
    if product is None or not product["active"]:
        await callback.answer("Цього товару вже немає 😔", show_alert=True)
        return
    await show_screen(callback, product_card_text(product), product_kb(product), product["photo_id"])
    await callback.answer()


@dp.callback_query(F.data.startswith("add:"))
async def cb_add(callback: CallbackQuery):
    product = db.get_product(int(callback.data.split(":", 1)[1]))
    if product is None or not product["active"]:
        await callback.answer("Цього товару вже немає 😔", show_alert=True)
        return
    db.add_to_cart(callback.from_user.id, product["id"])
    await callback.answer("Додано в кошик ✅")


# ---------- КОШИК (inline) ----------

async def refresh_cart(callback: CallbackQuery):
    text, markup = cart_view(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")


@dp.callback_query(F.data.startswith("inc:"))
async def cb_inc(callback: CallbackQuery):
    db.add_to_cart(callback.from_user.id, int(callback.data.split(":", 1)[1]))
    await refresh_cart(callback)
    await callback.answer()


@dp.callback_query(F.data.startswith("dec:"))
async def cb_dec(callback: CallbackQuery):
    db.decrease_in_cart(callback.from_user.id, int(callback.data.split(":", 1)[1]))
    await refresh_cart(callback)
    await callback.answer()


@dp.callback_query(F.data == "clear")
async def cb_clear(callback: CallbackQuery):
    db.clear_cart(callback.from_user.id)
    await refresh_cart(callback)
    await callback.answer("Кошик очищено")


@dp.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


# ---------- ОФОРМЛЕННЯ ЗАМОВЛЕННЯ ----------

@dp.callback_query(F.data == "checkout")
async def cb_checkout(callback: CallbackQuery, state: FSMContext):
    if not db.get_cart(callback.from_user.id):
        await callback.answer("Кошик порожній 🙂", show_alert=True)
        return
    await callback.answer()
    await state.set_state(Checkout.name)
    await callback.message.answer("Як до тебе звертатися?", reply_markup=cancel_kb)


@dp.message(Checkout.name, F.text)
async def checkout_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not 2 <= len(name) <= 50:
        await message.answer("Напиши ім'я від 2 до 50 символів.")
        return
    await state.update_data(name=name)
    await state.set_state(Checkout.phone)
    await message.answer("Надішли номер телефону — кнопкою нижче 👇", reply_markup=phone_kb)


@dp.message(Checkout.phone, F.contact)
async def checkout_phone(message: Message, state: FSMContext):
    if message.contact.user_id != message.from_user.id:
        await message.answer("Будь ласка, надішли саме свій номер — кнопкою нижче.")
        return
    await state.update_data(phone=message.contact.phone_number)
    await message.answer("Номер отримано ✅", reply_markup=cancel_kb)
    await state.set_state(Checkout.delivery)
    await message.answer("Як отримаєш замовлення?", reply_markup=delivery_kb())


@dp.message(Checkout.phone)
async def checkout_phone_wrong(message: Message):
    await message.answer("Натисни кнопку «📱 Поділитися номером» 👇", reply_markup=phone_kb)


@dp.callback_query(Checkout.delivery, F.data.startswith("dlv:"))
async def checkout_delivery(callback: CallbackQuery, state: FSMContext):
    delivery = callback.data.split(":", 1)[1]
    await state.update_data(delivery=delivery)
    await callback.message.edit_text(f"✅ {DELIVERY[delivery]}")
    await callback.answer()

    if delivery == "delivery":
        await state.set_state(Checkout.address)
        await callback.message.answer("Напиши адресу доставки (вулиця, будинок, квартира):")
    else:
        await state.update_data(address=None)
        await ask_confirm(callback.message, state, callback.from_user.id)


@dp.message(Checkout.address, F.text)
async def checkout_address(message: Message, state: FSMContext):
    address = message.text.strip()
    if len(address) < 5:
        await message.answer("Адреса закоротка. Напиши вулицю, будинок і квартиру.")
        return
    await state.update_data(address=address)
    await ask_confirm(message, state, message.from_user.id)


async def ask_confirm(message: Message, state: FSMContext, user_id: int):
    data = await state.get_data()
    cart_text, _ = cart_view(user_id)
    lines = [cart_text, "", DELIVERY[data["delivery"]]]
    if data.get("address"):
        lines.append(f"📍 {escape(data['address'])}")
    lines.append(f"👤 {escape(data['name'])}")
    lines.append(f"📞 {data['phone']}")

    await state.set_state(Checkout.confirm)
    await message.answer("\n".join(lines), reply_markup=confirm_kb(), parse_mode="HTML")


@dp.callback_query(Checkout.confirm, F.data.startswith("order:"))
async def checkout_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    answer = callback.data.split(":", 1)[1]
    data = await state.get_data()
    await state.clear()

    if answer == "no":
        await callback.message.edit_text("❌ Оформлення скасовано. Товари лишилися в кошику.")
        await callback.answer()
        await callback.message.answer("Головне меню 👇", reply_markup=main_menu)
        return

    user = callback.from_user
    order_id = db.create_order(
        user_id=user.id,
        username=user.username,
        name=data["name"],
        phone=data["phone"],
        delivery=data["delivery"],
        address=data.get("address"),
    )
    if order_id is None:
        await callback.message.edit_text("Кошик порожній — нічого оформлювати 🙂")
        await callback.answer()
        await callback.message.answer("Головне меню 👇", reply_markup=main_menu)
        return

    await callback.message.edit_text(f"🎉 Дякуємо! Замовлення #{order_id} прийнято.")
    await callback.answer()
    await callback.message.answer(
        "Ми напишемо, коли почнемо готувати. Статус — у «📦 Мої замовлення».",
        reply_markup=main_menu,
    )

    await notify_admin(
        bot,
        "🆕 НОВЕ ЗАМОВЛЕННЯ\n\n" + order_text(order_id, for_admin=True),
        reply_markup=admin_order_kb(order_id, "new"),
    )


# ---------- СТАТУСИ ЗАМОВЛЕНЬ (власник) ----------

@dp.callback_query(F.data.startswith("st:"))
async def cb_status(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Це тільки для власника магазину.", show_alert=True)
        return

    _, order_id, status = callback.data.split(":")  # "st:5:accepted" → ["st", "5", "accepted"]
    order_id = int(order_id)
    order = db.get_order(order_id)
    if order is None:
        await callback.answer("Замовлення не знайдено.", show_alert=True)
        return

    db.set_order_status(order_id, status)
    await callback.message.edit_text(
        "📦 ЗАМОВЛЕННЯ\n\n" + order_text(order_id, for_admin=True),
        reply_markup=admin_order_kb(order_id, status),
        parse_mode="HTML",
    )
    await callback.answer(f"Статус: {STATUSES[status]}")

    messages = {
        "accepted": "👨‍🍳 Твоє замовлення #{id} прийнято і вже готується!",
        "done": "✅ Замовлення #{id} готове! Смачного ☕",
        "rejected": "😔 На жаль, замовлення #{id} відхилено. Зателефонуй нам, щоб уточнити.",
    }
    try:
        await bot.send_message(order["user_id"], messages[status].format(id=order_id))
    except Exception as e:
        logging.warning("Не вдалося повідомити клієнта: %s", e)


# ================================================================
#                           АДМІНКА
# ================================================================
# До кожного обробника додано фільтр IS_ADMIN — для інших користувачів
# цих команд і кнопок просто не існує.

@dp.message(Command("admin"), IS_ADMIN)
async def admin_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🔧 Адмінка магазину:", reply_markup=admin_menu_kb())


@dp.callback_query(F.data == "adm:menu", IS_ADMIN)
async def cb_admin_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_screen(callback, "🔧 Адмінка магазину:", admin_menu_kb())
    await callback.answer()


# ---------- Необроблені замовлення ----------

@dp.callback_query(F.data == "adm:orders", IS_ADMIN)
async def cb_admin_orders(callback: CallbackQuery):
    orders = db.get_active_orders()
    await callback.answer()
    if not orders:
        await callback.message.answer("✨ Необроблених замовлень немає.")
        return
    for order in orders:
        await callback.message.answer(
            order_text(order["id"], for_admin=True),
            reply_markup=admin_order_kb(order["id"], order["status"]),
            parse_mode="HTML",
        )


# ---------- Додати категорію ----------

@dp.callback_query(F.data == "adm:addcat", IS_ADMIN)
async def cb_add_category(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddCategory.name)
    await callback.answer()
    await callback.message.answer(
        "Напиши назву нової категорії (можна з емодзі, наприклад «🥪 Сендвічі»):",
        reply_markup=cancel_kb,
    )


@dp.message(AddCategory.name, F.text, IS_ADMIN)
async def add_category_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not 2 <= len(name) <= 40:
        await message.answer("Назва має бути від 2 до 40 символів.")
        return
    db.add_category(name)
    await state.clear()
    await message.answer(f"✅ Категорію «{escape(name)}» додано.", reply_markup=main_menu, parse_mode="HTML")
    await message.answer("🔧 Адмінка магазину:", reply_markup=admin_menu_kb())


# ---------- Додати товар: категорія → назва → опис → ціна → фото ----------

@dp.callback_query(F.data == "adm:add", IS_ADMIN)
async def cb_add_product(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddProduct.category)
    await show_screen(callback, "У яку категорію додати товар?", admin_categories_kb("adm_cat"))
    await callback.answer()


@dp.callback_query(AddProduct.category, F.data.startswith("adm_cat:"), IS_ADMIN)
async def add_product_category(callback: CallbackQuery, state: FSMContext):
    category_id = int(callback.data.split(":", 1)[1])
    category = db.get_category(category_id)
    await state.update_data(category_id=category_id)
    await state.set_state(AddProduct.name)
    await callback.message.edit_text(f"📁 Категорія: {escape(category['name'])}", parse_mode="HTML")
    await callback.answer()
    await callback.message.answer("Назва товару:", reply_markup=cancel_kb)


@dp.message(AddProduct.name, F.text, IS_ADMIN)
async def add_product_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not 2 <= len(name) <= 40:
        await message.answer("Назва має бути від 2 до 40 символів.")
        return
    await state.update_data(name=name)
    await state.set_state(AddProduct.description)
    await message.answer("Короткий опис (склад, об'єм, вага):")


@dp.message(AddProduct.description, F.text, IS_ADMIN)
async def add_product_description(message: Message, state: FSMContext):
    description = message.text.strip()
    if not 3 <= len(description) <= 500:
        await message.answer("Опис має бути від 3 до 500 символів.")
        return
    await state.update_data(description=description)
    await state.set_state(AddProduct.price)
    await message.answer("Ціна в гривнях (тільки число, наприклад 85):")


@dp.message(AddProduct.price, F.text, IS_ADMIN)
async def add_product_price(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit() or not 1 <= int(text) <= 100_000:
        await message.answer("Напиши ціну цілим числом, наприклад 85.")
        return
    await state.update_data(price=int(text))
    await state.set_state(AddProduct.photo)
    await message.answer("Надішли фото товару 📷 або натисни «Без фото»:", reply_markup=skip_photo_kb())


@dp.message(AddProduct.photo, F.photo, IS_ADMIN)
async def add_product_photo(message: Message, state: FSMContext):
    # Telegram надсилає фото в кількох розмірах; [-1] — найбільший.
    # Зберігаємо не сам файл, а його file_id — «посилання» на фото на серверах Telegram.
    photo_id = message.photo[-1].file_id
    await save_new_product(message, state, photo_id)


@dp.callback_query(AddProduct.photo, F.data == "adm_skip_photo", IS_ADMIN)
async def add_product_skip_photo(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("⏭ Без фото")
    await callback.answer()
    await save_new_product(callback.message, state, None)


@dp.message(AddProduct.photo, IS_ADMIN)
async def add_product_photo_wrong(message: Message):
    await message.answer("Надішли саме фото (не файлом) або натисни «Без фото».", reply_markup=skip_photo_kb())


async def save_new_product(message: Message, state: FSMContext, photo_id):
    data = await state.get_data()
    await state.clear()
    product_id = db.add_product(
        data["category_id"], data["name"], data["description"], data["price"], photo_id
    )
    product = db.get_product(product_id)
    await message.answer("✅ Товар додано в каталог!", reply_markup=main_menu)
    if photo_id:
        await message.answer_photo(
            photo_id, caption=admin_product_text(product),
            reply_markup=admin_product_kb(product), parse_mode="HTML",
        )
    else:
        await message.answer(
            admin_product_text(product), reply_markup=admin_product_kb(product), parse_mode="HTML"
        )


# ---------- Редагувати товари ----------

@dp.callback_query(F.data == "adm:edit", IS_ADMIN)
async def cb_edit_products(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_screen(callback, "Обери категорію:", admin_categories_kb("adm_ecat"))
    await callback.answer()


@dp.callback_query(F.data.startswith("adm_ecat:"), IS_ADMIN)
async def cb_edit_category(callback: CallbackQuery):
    category_id = int(callback.data.split(":", 1)[1])
    await show_screen(
        callback,
        "Обери товар (🙈 — прихований з каталогу):",
        admin_products_kb(category_id),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("adm_prod:"), IS_ADMIN)
async def cb_edit_product(callback: CallbackQuery):
    product = db.get_product(int(callback.data.split(":", 1)[1]))
    await show_screen(
        callback, admin_product_text(product), admin_product_kb(product), product["photo_id"]
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("adm_toggle:"), IS_ADMIN)
async def cb_toggle_product(callback: CallbackQuery):
    product_id = int(callback.data.split(":", 1)[1])
    active = db.toggle_active(product_id)
    product = db.get_product(product_id)
    await show_screen(
        callback, admin_product_text(product), admin_product_kb(product), product["photo_id"]
    )
    await callback.answer("Товар знову в каталозі 👁" if active else "Товар приховано 🙈")


@dp.callback_query(F.data.startswith("adm_price:"), IS_ADMIN)
async def cb_edit_price(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(":", 1)[1])
    await state.set_state(EditProduct.price)
    await state.update_data(product_id=product_id)
    await callback.answer()
    await callback.message.answer("Нова ціна в гривнях (число):", reply_markup=cancel_kb)


@dp.message(EditProduct.price, F.text, IS_ADMIN)
async def edit_price(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit() or not 1 <= int(text) <= 100_000:
        await message.answer("Напиши ціну цілим числом, наприклад 85.")
        return
    data = await state.get_data()
    await state.clear()
    db.update_price(data["product_id"], int(text))
    product = db.get_product(data["product_id"])
    await message.answer(f"✅ Нова ціна: {product['price']} грн", reply_markup=main_menu)
    await message.answer(
        admin_product_text(product), reply_markup=admin_product_kb(product), parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("adm_photo:"), IS_ADMIN)
async def cb_edit_photo(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(":", 1)[1])
    await state.set_state(EditProduct.photo)
    await state.update_data(product_id=product_id)
    await callback.answer()
    await callback.message.answer("Надішли нове фото товару 📷", reply_markup=cancel_kb)


@dp.message(EditProduct.photo, F.photo, IS_ADMIN)
async def edit_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    db.update_photo(data["product_id"], message.photo[-1].file_id)
    product = db.get_product(data["product_id"])
    await message.answer("✅ Фото оновлено", reply_markup=main_menu)
    await message.answer_photo(
        product["photo_id"], caption=admin_product_text(product),
        reply_markup=admin_product_kb(product), parse_mode="HTML",
    )


@dp.message(EditProduct.photo, IS_ADMIN)
async def edit_photo_wrong(message: Message):
    await message.answer("Надішли саме фото (не файлом) або натисни «❌ Скасувати».")


# ---------- СТАРІ КНОПКИ ----------

@dp.callback_query()
async def old_button(callback: CallbackQuery):
    await callback.answer("Ця кнопка вже неактивна 🙂", show_alert=True)


async def main():
    if not TOKEN:
        raise SystemExit("Не задано BOT_TOKEN. Додай його в Run Configuration.")
    if ADMIN_ID is None:
        logging.warning("ADMIN_ID не задано — адмінка і сповіщення працювати не будуть.")
    logging.basicConfig(level=logging.INFO)
    db.init_db()
    bot = Bot(token=TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())