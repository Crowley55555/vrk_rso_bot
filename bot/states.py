from enum import IntEnum


class UserStates(IntEnum):
    """Состояния сценария сообщения об аварии обычным пользователем."""

    ACCIDENT_SHORT = 1
    ACCIDENT_DETAIL = 2
    ACCIDENT_WHO = 3
    ACCIDENT_URGENCY = 4


class AdminStates(IntEnum):
    """Состояния административных сценариев."""

    ADD_TASK_NAME = 10
    ADD_COMMENTS = 11
    ADD_RESPONSIBLE = 12
    ADD_FULL_NAME = 13
    ADD_DEADLINE = 14

    EDIT_TASK_NAME = 20
    EDIT_COMMENTS = 21
    EDIT_DEADLINE = 22
    EDIT_RESPONSIBLE = 23
    EDIT_ACCIDENT_C = 24
    EDIT_ACCIDENT_E = 25

    TAKE_IN_WORK_COMMENTS = 30
    TAKE_IN_WORK_RESPONSIBLE = 31

    VIEW_LOGS = 40
    VIEW_LOG_DETAIL = 41
