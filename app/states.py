from aiogram.fsm.state import State, StatesGroup


class Flow(StatesGroup):
    save_text = State()
    tag_name = State()
    seal_date = State()
    search_query = State()
    timezone = State()
    import_file = State()
    import_confirm = State()
