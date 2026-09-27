from aiogram.fsm.state import State, StatesGroup


class OwnerStates(StatesGroup):
    waiting_studio_name = State()
    waiting_resource_name = State()
    waiting_price = State()
    waiting_price_edit = State()
    waiting_weekend_price = State()
    waiting_night_price = State()
    waiting_extra_resource = State()
    waiting_block_interval = State()
    waiting_window = State()


class BookingStates(StatesGroup):
    waiting_time = State()
    waiting_name = State()
    waiting_phone = State()
    waiting_consent = State()
