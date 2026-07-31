from aiogram.fsm.state import State, StatesGroup


class EditPreview(StatesGroup):
    waiting_for_tags = State()
    waiting_for_caption = State()
    waiting_for_title = State()
    waiting_for_description = State()
    waiting_for_schedule = State()


class CreatePublication(StatesGroup):
    waiting_for_urls = State()
    waiting_for_channel = State()


class ManageChannel(StatesGroup):
    waiting_for_interval = State()


class ManageQueue(StatesGroup):
    waiting_for_schedule = State()
