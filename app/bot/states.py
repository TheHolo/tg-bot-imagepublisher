from aiogram.fsm.state import State, StatesGroup


class EditPreview(StatesGroup):
    waiting_for_tags = State()
    waiting_for_channel = State()
