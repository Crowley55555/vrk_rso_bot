from enum import IntEnum


class UserStates(IntEnum):
    """Состояния диалога создания задачи обычным пользователем."""

    TASK_NAME = 1
    COMMENTS = 2
    FULL_NAME = 3
    DEADLINE = 4


class AdminStates(IntEnum):
    """Состояния административных сценариев."""

    ADD_TASK_NAME = 10
    ADD_COMMENTS = 11
    ADD_RESPONSIBLE = 12
    ADD_FULL_NAME = 13
    ADD_DEADLINE = 14

    EDIT_COMMENTS = 20
    EDIT_DEADLINE = 21
    EDIT_RESPONSIBLE = 22

    TAKE_IN_WORK_COMMENTS = 30
    TAKE_IN_WORK_RESPONSIBLE = 31
