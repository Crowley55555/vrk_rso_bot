from __future__ import annotations

import asyncio
import logging

from max_bot.config import (
    ACCIDENTS_SHEET,
    COMPLETED_SHEET,
    IN_PROGRESS_SHEET,
    LOG_SHEET,
    NOT_STARTED_SHEET,
    Settings,
    SHEET_KEY_TO_NAME,
)
from max_bot.handlers.common_max import (
    BaseMaxHandler,
    MaxCtx,
    TaskMapper,
    TextFormatter,
    get_user_display_name,
)
from max_bot.keyboards import KeyboardFactory
from max_bot.states import AdminStates, CONV_END
from shared.api_client import (
    SheetsServiceError,
    append_task,
    delete_row,
    get_all_tasks,
    move_task,
    update_cell,
    write_log,
)


logger = logging.getLogger(__name__)

ACCIDENTS_LIST_BUTTON = "📋 Список аварий"
ADD_ACCIDENT_BUTTON = "➕ Добавить аварию"

ConversationHandler = type("ConversationHandler", (), {"END": CONV_END})


class AdminTaskHandler(BaseMaxHandler):
    """Административные диалоги управления задачами."""


    async def safe_delete_message(
        self,
        ctx: MaxCtx,
        message_id: str | None,
    ) -> None:
        """Безопасно удаляет сообщение через общий менеджер сообщений."""

        await self.message_manager.delete_message(ctx.user_id, ctx.user_data, message_id)

    async def show_accidents_menu(self, ctx: MaxCtx) -> int:
        """Показывает администратору reply-подменю раздела аварий."""

        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        ctx.user_data["flow_mode"] = "accidents_menu"
        ctx.user_data.pop("flow_data", None)
        ctx.user_data.pop("current_task", None)

        if ctx.incoming_message_mid:
            self.message_manager.remember_user_message(ctx)
            await self.safe_delete_message(ctx, ctx.incoming_message_mid)

        return await self._send_accidents_menu(ctx)

    async def _send_accidents_menu(self, ctx: MaxCtx) -> int:
        """Отправляет сообщение подменю раздела аварий."""

        ctx.user_data["current_state"] = AdminStates.ACCIDENTS_MENU
        await self.send_text(ctx,
            "🚨 Раздел Аварии. Выберите действие:",
            attachments=KeyboardFactory.get_accidents_submenu_keyboard(),
            remember_as_last=True,
        )
        return AdminStates.ACCIDENTS_MENU

    async def show_accident_tasks_from_menu(self, ctx: MaxCtx) -> int:
        """Открывает список аварий из подменю раздела аварий."""

        await self.message_manager.delete_step_messages(ctx)
        ctx.user_data["flow_mode"] = "accidents_list"
        await self._show_task_list_for_sheet(ctx, "accidents")
        ctx.user_data["current_state"] = AdminStates.ACCIDENTS_MENU
        return AdminStates.ACCIDENTS_MENU

    async def show_accident_tasks(self, ctx: MaxCtx) -> None:
        """Показывает список аварий."""

        await self._show_task_list_for_sheet(ctx, "accidents")

    async def show_task_card(self, ctx: MaxCtx) -> None:
        """Показывает карточку выбранной задачи или аварии внутри админского сценария."""


        _, sheet_key, row_index_raw = ctx.callback_payload.split("_", maxsplit=2)
        row_index = int(row_index_raw)

        try:
            task = await self.fetch_task(sheet_key, row_index)
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось загрузить задачу из Google Sheets.")
            return

        if task is None:
            await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
            await self.send_text(ctx,
                (
                    "Авария не найдена. Возможно, она уже была изменена."
                    if sheet_key == "accidents"
                    else "Задача не найдена. Возможно, она уже была изменена."
                ),
                attachments=KeyboardFactory.home_only_menu(),
            )
            return

        ctx.user_data["selected_task"] = {
            "sheet_key": sheet_key,
            "row_index": row_index,
        }
        ctx.user_data["current_task"] = {
            "sheet_name": task.sheet_name,
            "sheet_key": sheet_key,
            "row_index": row_index,
            "A": task.date or "",
            "B": task.task_name or "",
            "C": task.comments or "",
            "D": task.responsible or "",
            "E": task.deadline or "",
            "F": task.added_by or "",
        }

        if sheet_key == "accidents":
            ctx.user_data["flow_mode"] = "accidents_menu"

        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        await self.send_preformatted_text(ctx,
            TextFormatter.task_details(task),
            attachments=KeyboardFactory.task_detail_keyboard(
                sheet_key, row_index, is_admin=True
            ),
        )
        await self.send_text(ctx,
            "Используйте кнопки ниже для навигации",
            attachments=KeyboardFactory.navigation_menu(),
        )

    async def show_logs(self, ctx: MaxCtx) -> None:
        """Показывает последние записи листа 'Лог'."""

        try:
            rows = await get_all_tasks(LOG_SHEET)
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось загрузить лог.")
            return

        ctx.user_data["current_state"] = AdminStates.VIEW_LOGS
        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)

        if not rows:
            await self.send_text(ctx,
                "Лог пуст",
                attachments=KeyboardFactory.home_only_menu(),
            )
            return

        latest_rows = list(reversed(rows))[:50]
        payload = [
            {
                "row_index": int(row["row_index"]),
                "title": f"{row.get('Дата и время', '')} | {row.get('Действие', '')}",
            }
            for row in latest_rows
        ]

        await self.send_text(ctx,
            "Выберите запись лога",
            attachments=KeyboardFactory.log_list_keyboard(payload),
        )
        await self.send_text(ctx,
            "Используйте кнопки ниже для навигации",
            attachments=KeyboardFactory.navigation_menu(),
        )

    async def show_log_detail(self, ctx: MaxCtx) -> None:
        """Показывает карточку выбранной записи лога."""

        row_index = int(ctx.callback_payload.split("_")[-1])

        try:
            row = await self._fetch_row(LOG_SHEET, row_index)
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось загрузить запись лога.")
            return
        if row is None:
            await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
            await self.send_text(ctx,
                "Запись лога не найдена.",
                attachments=KeyboardFactory.home_only_menu(),
            )
            return

        text = (
            "📊 **Запись лога**\n\n"
            f"📅 Дата и время: {TextFormatter.escape(row.get('Дата и время', ''))}\n"
            f"👤 Кто: {TextFormatter.escape(row.get('Кто', ''))}\n"
            f"⚙️ Действие: {TextFormatter.escape(row.get('Действие', ''))}\n"
            f"📌 Задача/Авария: {TextFormatter.escape(row.get('Задача', ''))}\n"
            f"📋 Лист: {TextFormatter.escape(row.get('Лист', ''))}\n"
            f"📝 Подробности: {TextFormatter.escape(row.get('Подробности', ''))}"
        )

        ctx.user_data["current_state"] = AdminStates.VIEW_LOG_DETAIL
        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        await self.send_preformatted_text(ctx,
            text,
            attachments=KeyboardFactory.back_to_logs_keyboard(),
        )
        await self.send_text(ctx,
            "Используйте кнопки ниже для навигации",
            attachments=KeyboardFactory.navigation_menu(),
        )

    async def back_to_logs(self, ctx: MaxCtx) -> None:
        """Возвращает администратора к списку логов."""

        await self.show_logs(ctx)

    async def start_add_task(self, ctx: MaxCtx) -> int:
        """Запускает административный сценарий добавления задачи."""

        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        ctx.user_data["flow_mode"] = "admin_add"
        ctx.user_data["flow_data"] = {}

        if ctx.incoming_message_mid:
            self.message_manager.remember_user_message(ctx)
            await self.message_manager.delete_message(
                ctx.user_id,
                ctx.user_data,
                ctx.incoming_message_mid,
            )

        await self._ask_add_task_name(ctx)
        return AdminStates.ADD_TASK_NAME

    async def receive_task_name(self, ctx: MaxCtx) -> int:
        """Сохраняет название задачи и спрашивает комментарий."""

        ctx.user_data.setdefault("flow_data", {})["task_name"] = (ctx.text or '').strip()
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_add_comments(ctx)
        return AdminStates.ADD_COMMENTS

    async def receive_comments(self, ctx: MaxCtx) -> int:
        """Сохраняет комментарий и спрашивает ответственных."""

        raw_value = (ctx.text or '').strip()
        ctx.user_data.setdefault("flow_data", {})["comments"] = "" if raw_value == "-" else raw_value
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_add_responsible(ctx)
        return AdminStates.ADD_RESPONSIBLE

    async def receive_responsible(self, ctx: MaxCtx) -> int:
        """Сохраняет ответственных и спрашивает ФИО автора."""

        raw_value = (ctx.text or '').strip()
        ctx.user_data.setdefault("flow_data", {})["responsible"] = "" if raw_value == "-" else raw_value
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_add_full_name(ctx)
        return AdminStates.ADD_FULL_NAME

    async def receive_full_name(self, ctx: MaxCtx) -> int:
        """Сохраняет ФИО автора и спрашивает срок."""

        ctx.user_data.setdefault("flow_data", {})["full_name"] = (ctx.text or '').strip()
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_add_deadline(ctx)
        return AdminStates.ADD_DEADLINE

    async def finish_add_task(self, ctx: MaxCtx) -> int:
        """Сохраняет административную задачу в лист 'Не начатые'."""

        raw_deadline = (ctx.text or '').strip()
        flow_data = ctx.user_data.setdefault("flow_data", {})
        flow_data["deadline"] = "" if raw_deadline == "-" else raw_deadline

        row_data = [
            self.now_date(),
            flow_data["task_name"],
            flow_data.get("comments", ""),
            flow_data.get("responsible", ""),
            flow_data.get("deadline", ""),
            flow_data["full_name"],
        ]

        try:
            await append_task(NOT_STARTED_SHEET, row_data)
        except SheetsServiceError:
            await self.message_manager.delete_step_messages(ctx)
            await self.show_error(ctx, "Не удалось сохранить задачу. Попробуйте позже.")
            return CONV_END

        who = get_user_display_name(ctx.user_proxy)
        details = f"Срок: {flow_data.get('deadline') or ''}. Ответственные: {flow_data.get('responsible') or ''}. Кто добавил: {flow_data['full_name']}"
        await write_log(who, "Добавлена задача", flow_data["task_name"], NOT_STARTED_SHEET, details)

        await self.message_manager.delete_step_messages(ctx)
        ctx.user_data.pop("flow_data", None)
        ctx.user_data.pop("flow_mode", None)
        await self.send_text(ctx, "Задача успешно добавлена")
        await self.show_main_menu(ctx.user_id, ctx.user_data)
        return CONV_END

    async def start_add_accident(self, ctx: MaxCtx) -> int:
        """Запускает административный сценарий добавления аварии."""

        await self.message_manager.delete_step_messages(ctx)
        ctx.user_data["flow_mode"] = "admin_add_accident"
        ctx.user_data["flow_data"] = {}
        await self._ask_admin_accident_short(ctx)
        return AdminStates.ADMIN_ACCIDENT_SHORT

    async def receive_admin_accident_short(self, ctx: MaxCtx) -> int:
        """Сохраняет краткое описание аварии и участок."""

        ctx.user_data.setdefault("flow_data", {})["accident_short"] = (ctx.text or '').strip()
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_admin_accident_detail(ctx)
        return AdminStates.ADMIN_ACCIDENT_DETAIL

    async def receive_admin_accident_detail(self, ctx: MaxCtx) -> int:
        """Сохраняет подробное описание аварии."""

        raw_value = (ctx.text or '').strip()
        ctx.user_data.setdefault("flow_data", {})["accident_detail"] = "" if raw_value == "-" else raw_value
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_admin_accident_responsible(ctx)
        return AdminStates.ADMIN_ACCIDENT_RESPONSIBLE

    async def receive_admin_accident_responsible(self, ctx: MaxCtx) -> int:
        """Сохраняет ответственных по аварии."""

        raw_value = (ctx.text or '').strip()
        ctx.user_data.setdefault("flow_data", {})["accident_responsible"] = "" if raw_value == "-" else raw_value
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_admin_accident_urgency(ctx)
        return AdminStates.ADMIN_ACCIDENT_URGENCY

    async def receive_admin_accident_urgency(self, ctx: MaxCtx) -> int:
        """Сохраняет срочность ремонта и спрашивает ФИО."""

        raw_value = (ctx.text or '').strip()
        ctx.user_data.setdefault("flow_data", {})["accident_urgency"] = "" if raw_value == "-" else raw_value
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_admin_accident_who(ctx)
        return AdminStates.ADMIN_ACCIDENT_WHO

    async def finish_add_accident(self, ctx: MaxCtx) -> int:
        """Сохраняет аварию, добавленную администратором, в лист 'Аварии'."""

        flow_data = ctx.user_data.setdefault("flow_data", {})
        accident_short = str(flow_data.get("accident_short", "")).strip()
        accident_detail = str(flow_data.get("accident_detail", "")).strip()
        accident_responsible = str(flow_data.get("accident_responsible", "")).strip()
        accident_urgency = str(flow_data.get("accident_urgency", "")).strip()
        accident_who = (ctx.text or '').strip()
        flow_data["accident_who"] = accident_who

        row_data = [
            self.now_datetime_minutes(),
            accident_short,
            accident_detail,
            accident_responsible,
            accident_urgency,
            accident_who,
        ]

        try:
            await append_task(ACCIDENTS_SHEET, row_data)
        except SheetsServiceError:
            await self.message_manager.delete_step_messages(ctx)
            await self.show_error(ctx, "Не удалось сохранить аварию. Попробуйте позже.")
            return CONV_END

        who = get_user_display_name(ctx.user_proxy)
        details = (
            f"Срочность: {accident_urgency}. "
            f"Ответственные: {accident_responsible}. "
            f"Кто добавил: {accident_who}"
        )
        await write_log(
            who,
            "Добавлена авария администратором",
            accident_short,
            ACCIDENTS_SHEET,
            details,
        )

        await self.message_manager.delete_step_messages(ctx)
        ctx.user_data.pop("flow_data", None)
        ctx.user_data["flow_mode"] = "accidents_list"
        await self.send_text(ctx, "✅ Авария добавлена в таблицу.")
        await self._show_task_list_for_sheet(ctx, "accidents")
        ctx.user_data["current_state"] = AdminStates.ACCIDENTS_MENU
        return AdminStates.ACCIDENTS_MENU

    async def start_edit_task(self, ctx: MaxCtx) -> int:
        """Запускает редактирование названия, комментария, срока и ответственных."""

        _, sheet_key, row_index_raw = ctx.callback_payload.split("_", maxsplit=2)
        row_index = int(row_index_raw)

        try:
            task = await self.fetch_task(sheet_key, row_index)
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось загрузить задачу для редактирования.")
            return CONV_END

        if task is None:
            await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
            await self.send_text(ctx,
                "Авария не найдена" if sheet_key == "accidents" else "Задача не найдена",
                attachments=KeyboardFactory.home_only_menu(),
            )
            return CONV_END

        ctx.user_data["current_task"] = {
            "sheet_name": task.sheet_name,
            "sheet_key": sheet_key,
            "row_index": row_index,
            "A": task.date or "",
            "B": task.task_name or "",
            "C": task.comments or "",
            "D": task.responsible or "",
            "E": task.deadline or "",
            "F": task.added_by or "",
        }
        if sheet_key == "accidents":
            ctx.user_data["flow_mode"] = "edit_accident"
            ctx.user_data["flow_data"] = {
                "sheet_key": sheet_key,
                "row_index": row_index,
                "current_title": task.task_name,
                "current_description": task.comments,
                "current_responsible": task.responsible,
                "current_urgency": task.deadline,
                "current_who": task.added_by,
            }
            await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
            await self._ask_edit_accident_title(ctx)
            return AdminStates.EDIT_ACCIDENT_B

        flow_data = {
            "sheet_key": sheet_key,
            "row_index": row_index,
            "current_task_name": task.task_name,
            "current_comments": task.comments,
            "current_deadline": task.deadline,
        }
        if sheet_key == "progress":
            flow_data["current_responsible"] = task.responsible
        ctx.user_data["flow_mode"] = "edit"
        ctx.user_data["flow_data"] = flow_data

        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        await self._ask_edit_task_name(ctx)
        return AdminStates.EDIT_TASK_NAME

    async def receive_edit_task_name(self, ctx: MaxCtx) -> int:
        """Принимает новое название задачи и спрашивает комментарий."""

        ctx.user_data.setdefault("flow_data", {})["new_task_name"] = (ctx.text or '').strip()
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_edit_comments(ctx)
        return AdminStates.EDIT_COMMENTS

    async def receive_edit_comment(self, ctx: MaxCtx) -> int:
        """Принимает новый комментарий и спрашивает новый срок."""

        ctx.user_data.setdefault("flow_data", {})["new_comments"] = (ctx.text or '').strip()
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_edit_deadline(ctx)
        return AdminStates.EDIT_DEADLINE

    async def receive_edit_deadline(self, ctx: MaxCtx) -> int:
        """Принимает новый срок. Для листа «В работе» — запрашивает ответственных, иначе завершает редактирование."""

        flow_data = ctx.user_data.setdefault("flow_data", {})
        flow_data["new_deadline"] = (ctx.text or '').strip()
        await self.message_manager.delete_step_messages(ctx)

        if flow_data.get("sheet_key") == "progress":
            ctx.user_data["current_state"] = AdminStates.EDIT_DEADLINE
            await self._ask_edit_responsible(ctx)
            return AdminStates.EDIT_RESPONSIBLE

        return await self._apply_edit_and_finish(ctx)

    async def receive_edit_responsible(self, ctx: MaxCtx) -> int:
        """Принимает новых ответственных и применяет все правки к задаче из листа «В работе»."""

        flow_data = ctx.user_data.setdefault("flow_data", {})
        flow_data["new_responsible"] = (ctx.text or '').strip()
        await self.message_manager.delete_step_messages(ctx)
        return await self._apply_edit_and_finish(ctx)

    async def receive_edit_accident_title(self, ctx: MaxCtx) -> int:
        """Принимает новое краткое описание аварии и спрашивает подробности."""

        ctx.user_data.setdefault("flow_data", {})["new_title"] = (ctx.text or '').strip()
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_edit_accident_description(ctx)
        return AdminStates.EDIT_ACCIDENT_C

    async def receive_edit_accident_description(self, ctx: MaxCtx) -> int:
        """Принимает новое подробное описание аварии и спрашивает ответственных."""

        ctx.user_data.setdefault("flow_data", {})["new_description"] = (ctx.text or '').strip()
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_edit_accident_responsible(ctx)
        return AdminStates.EDIT_ACCIDENT_D

    async def receive_edit_accident_responsible(self, ctx: MaxCtx) -> int:
        """Принимает новых ответственных по аварии и спрашивает срочность."""

        ctx.user_data.setdefault("flow_data", {})["new_responsible"] = (ctx.text or '').strip()
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_edit_accident_urgency(ctx)
        return AdminStates.EDIT_ACCIDENT_E

    async def receive_edit_accident_urgency(self, ctx: MaxCtx) -> int:
        """Принимает новую срочность аварии и спрашивает автора записи."""

        ctx.user_data.setdefault("flow_data", {})["new_urgency"] = (ctx.text or '').strip()
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_edit_accident_who(ctx)
        return AdminStates.EDIT_ACCIDENT_F

    async def receive_edit_accident_who(self, ctx: MaxCtx) -> int:
        """Принимает нового автора записи аварии и завершает редактирование."""

        ctx.user_data.setdefault("flow_data", {})["new_who"] = (ctx.text or '').strip()
        await self.message_manager.delete_step_messages(ctx)
        return await self._apply_accident_edit_and_finish(ctx)

    async def _apply_accident_edit_and_finish(self, ctx: MaxCtx) -> int:
        """Применяет изменения всех полей аварии."""

        flow_data = ctx.user_data.setdefault("flow_data", {})
        new_title = (
            flow_data["current_title"]
            if flow_data.get("new_title") == "-"
            else flow_data.get("new_title", flow_data["current_title"])
        )
        new_description = (
            flow_data["current_description"]
            if flow_data.get("new_description") == "-"
            else flow_data.get("new_description", flow_data["current_description"])
        )
        new_responsible = (
            flow_data["current_responsible"]
            if flow_data.get("new_responsible") == "-"
            else flow_data.get("new_responsible", flow_data["current_responsible"])
        )
        new_urgency = (
            flow_data["current_urgency"]
            if flow_data.get("new_urgency") == "-"
            else flow_data.get("new_urgency", flow_data["current_urgency"])
        )
        new_who = (
            flow_data["current_who"]
            if flow_data.get("new_who") == "-"
            else flow_data.get("new_who", flow_data["current_who"])
        )
        row_index = int(flow_data["row_index"])
        current_task = ctx.user_data.get("current_task") or {}

        changes: list[str] = []
        try:
            if new_title != flow_data["current_title"]:
                await update_cell(ACCIDENTS_SHEET, row_index, 2, new_title)
                current_task["B"] = new_title
                changes.append(f'Краткое описание: "{flow_data["current_title"]}" → "{new_title}"')
            if new_description != flow_data["current_description"]:
                await update_cell(ACCIDENTS_SHEET, row_index, 3, new_description)
                current_task["C"] = new_description
                changes.append(f'Подробное описание: "{flow_data["current_description"]}" → "{new_description}"')
            if new_responsible != flow_data["current_responsible"]:
                await update_cell(ACCIDENTS_SHEET, row_index, 4, new_responsible)
                current_task["D"] = new_responsible
                changes.append(f'Ответственные: "{flow_data["current_responsible"]}" → "{new_responsible}"')
            if new_urgency != flow_data["current_urgency"]:
                await update_cell(ACCIDENTS_SHEET, row_index, 5, new_urgency)
                current_task["E"] = new_urgency
                changes.append(f'Срочность: "{flow_data["current_urgency"]}" → "{new_urgency}"')
            if new_who != flow_data["current_who"]:
                await update_cell(ACCIDENTS_SHEET, row_index, 6, new_who)
                current_task["F"] = new_who
                changes.append(f'Кто сообщил: "{flow_data["current_who"]}" → "{new_who}"')
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось обновить аварию.")
            return CONV_END

        details = "; ".join(changes) if changes else "Изменений не внесено"
        who = get_user_display_name(ctx.user_proxy)
        await write_log(
            who,
            "Редактирование аварии",
            current_task.get("B") or new_title,
            ACCIDENTS_SHEET,
            details,
        )

        ctx.user_data["current_task"] = current_task
        ctx.user_data.pop("flow_data", None)
        ctx.user_data.pop("flow_mode", None)
        await self.send_text(ctx, "Авария успешно обновлена")
        await self.show_main_menu(ctx.user_id, ctx.user_data)
        return CONV_END

    async def _apply_edit_and_finish(self, ctx: MaxCtx) -> int:
        """Применяет изменения названия, комментария, срока и ответственных и завершает редактирование."""

        flow_data = ctx.user_data.setdefault("flow_data", {})
        new_task_name = (
            flow_data["current_task_name"]
            if flow_data.get("new_task_name") == "-"
            else flow_data.get("new_task_name", flow_data["current_task_name"])
        )
        new_comments = flow_data["current_comments"] if flow_data.get("new_comments") == "-" else flow_data.get("new_comments", flow_data["current_comments"])
        new_deadline = flow_data["current_deadline"] if flow_data.get("new_deadline") == "-" else flow_data.get("new_deadline", flow_data["current_deadline"])
        sheet_name = self._sheet_name(flow_data["sheet_key"])
        row_index = int(flow_data["row_index"])

        current_task = ctx.user_data.get("current_task") or {}

        changes: list[str] = []
        try:
            if new_task_name != flow_data["current_task_name"]:
                await update_cell(sheet_name, row_index, 2, new_task_name)
                current_task["B"] = new_task_name
                changes.append(f'Название: "{flow_data["current_task_name"]}" → "{new_task_name}"')
            if new_comments != flow_data["current_comments"]:
                await update_cell(sheet_name, row_index, 3, new_comments)
                current_task["C"] = new_comments
                changes.append(f'Комментарий: "{flow_data["current_comments"]}" → "{new_comments}"')
            if new_deadline != flow_data["current_deadline"]:
                await update_cell(sheet_name, row_index, 5, new_deadline)
                current_task["E"] = new_deadline
                changes.append(f'Срок: "{flow_data["current_deadline"]}" → "{new_deadline}"')
            if flow_data.get("sheet_key") == "progress" and "new_responsible" in flow_data:
                new_responsible = flow_data["current_responsible"] if flow_data["new_responsible"] == "-" else flow_data["new_responsible"]
                if new_responsible != flow_data.get("current_responsible"):
                    await update_cell(sheet_name, row_index, 4, new_responsible)
                    current_task["D"] = new_responsible
                    changes.append(f'Ответственные: "{flow_data.get("current_responsible", "")}" → "{new_responsible}"')
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось обновить задачу.")
            return CONV_END

        details = "; ".join(changes) if changes else "Изменений не внесено"
        who = get_user_display_name(ctx.user_proxy)
        await write_log(
            who,
            "Редактирование задачи",
            current_task.get("B") or flow_data["current_task_name"],
            sheet_name,
            details,
        )

        ctx.user_data["current_task"] = current_task
        ctx.user_data.pop("flow_data", None)
        ctx.user_data.pop("flow_mode", None)
        await self.send_text(ctx, "Задача успешно обновлена")
        await self.show_main_menu(ctx.user_id, ctx.user_data)
        return CONV_END

    async def mark_done_from_todo(self, ctx: MaxCtx) -> int:
        """Переносит задачу из «Не начатые» напрямую в «Выполненные» (минуя «В работе»)."""

        row_index = int(ctx.callback_payload.split("_")[-1])

        try:
            task = await self.fetch_task("todo", row_index)
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось загрузить задачу.")
            return CONV_END

        if task is None:
            await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
            await self.send_text(ctx, "Задача не найдена", attachments=KeyboardFactory.home_only_menu())
            return CONV_END

        row_data = [
            self.now_date(),
            task.task_name,
            task.comments,
            task.responsible,
            task.deadline,
            task.added_by,
        ]

        try:
            await move_task(
                NOT_STARTED_SHEET,
                COMPLETED_SHEET,
                row_index,
                {"row_data": row_data},
            )
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось перенести задачу в архив.")
            return CONV_END

        who = get_user_display_name(ctx.user_proxy)
        await write_log(
            who,
            "Задача выполнена (без взятия в работу)",
            task.task_name,
            "Не начатые → Выполненные",
            f"Ответственные: {task.responsible}",
        )

        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        await self.send_text(ctx,
            "✅ Задача отмечена как выполненная и перенесена в архив.",
            attachments=KeyboardFactory.inline_home_menu(),
        )
        return CONV_END

    async def show_delete_confirmation(self, ctx: MaxCtx) -> None:
        """Показывает подтверждение удаления задачи (только для администраторов)."""


        if not self.is_admin_ctx(ctx):
            return

        parts = ctx.callback_payload.split("_", maxsplit=3)
        if len(parts) < 4:
            return
        sheet_key = parts[2]
        try:
            row_index = int(parts[3])
        except ValueError:
            return

        try:
            task = await self.fetch_task(sheet_key, row_index)
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось загрузить задачу.")
            return

        if task is None:
            await self.send_text(ctx,
                "Авария не найдена." if sheet_key == "accidents" else "Задача не найдена.",
                attachments=KeyboardFactory.home_only_menu(),
            )
            return

        try:
            await self.safe_delete_message(ctx, ctx.callback_message_mid)
        except Exception:
            pass

        entity_name = "аварию" if sheet_key == "accidents" else "задачу"
        marker = "🚨" if sheet_key == "accidents" else "📌"
        confirmation_text = (
            f"⚠️ **Вы уверены, что хотите удалить {entity_name}?**\n\n"
            f"{marker} " + TextFormatter.escape(task.task_name) + "\n\n"
            "**Это действие необратимо.**"
        )
        mid_del = await self.max_api.send_message(
            ctx.user_id,
            text=confirmation_text,
            attachments=KeyboardFactory.delete_confirm_keyboard(sheet_key, row_index),
            format_="markdown",
        )
        if mid_del:
            self.message_manager.remember_message(ctx.user_data, mid_del)

    async def confirm_delete_task(self, ctx: MaxCtx) -> None:
        """Удаляет задачу из листа и показывает обновлённый список."""


        if not self.is_admin_ctx(ctx):
            return

        parts = ctx.callback_payload.split("_", maxsplit=3)
        if len(parts) < 4:
            return
        sheet_key = parts[2]
        try:
            row_index = int(parts[3])
        except ValueError:
            return

        sheet_name = self._sheet_name(sheet_key)

        try:
            task = await self.fetch_task(sheet_key, row_index)
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось загрузить задачу.")
            return
        if task is None:
            await self.send_text(ctx,
                "Авария не найдена." if sheet_key == "accidents" else "Задача не найдена.",
                attachments=KeyboardFactory.home_only_menu(),
            )
            return
        task_name = task.task_name

        try:
            await delete_row(sheet_name, row_index)
        except SheetsServiceError:
            try:
                await self.safe_delete_message(ctx, ctx.callback_message_mid)
            except Exception:
                pass
            await self.send_text(ctx,
                "❌ Ошибка при удалении задачи. Попробуйте снова.",
                attachments=KeyboardFactory.home_only_menu(),
            )
            return

        who = get_user_display_name(ctx.user_proxy)
        if sheet_key == "accidents":
            await write_log(who, "Авария удалена", task_name, ACCIDENTS_SHEET, "Удалено администратором")
        else:
            await write_log(who, "Задача удалена", task_name, sheet_name, "Удалено администратором")

        try:
            await self.safe_delete_message(ctx, ctx.callback_message_mid)
        except Exception:
            pass

        await self.send_text(ctx,
            "🗑 Авария удалена." if sheet_key == "accidents" else "🗑 Задача удалена.",
        )

        await asyncio.sleep(1.5)
        await self._show_task_list_for_sheet(ctx, sheet_key)

    async def cancel_delete_task(self, ctx: MaxCtx) -> None:
        """Отменяет удаление и снова показывает карточку задачи."""


        if not self.is_admin_ctx(ctx):
            return

        parts = ctx.callback_payload.split("_", maxsplit=3)
        if len(parts) < 4:
            return
        sheet_key = parts[2]
        try:
            row_index = int(parts[3])
        except ValueError:
            return

        try:
            await self.safe_delete_message(ctx, ctx.callback_message_mid)
        except Exception:
            pass

        try:
            task = await self.fetch_task(sheet_key, row_index)
        except SheetsServiceError:
            await self.send_text(ctx,
                "Не удалось загрузить задачу.",
                attachments=KeyboardFactory.home_only_menu(),
            )
            return

        if task is None:
            await self.send_text(ctx,
                "Авария не найдена." if sheet_key == "accidents" else "Задача не найдена.",
                attachments=KeyboardFactory.home_only_menu(),
            )
            return

        await self.send_preformatted_text(ctx,
            TextFormatter.task_details(task),
            attachments=KeyboardFactory.task_detail_keyboard(
                sheet_key, row_index, is_admin=True
            ),
        )
        await self.send_text(ctx,
            "Используйте кнопки ниже для навигации",
            attachments=KeyboardFactory.navigation_menu(),
        )

    async def _show_task_list_for_sheet(
        self,
        ctx: MaxCtx,
        sheet_key: str,
    ) -> None:
        """Показывает список задач указанного листа (для администратора после удаления)."""

        sheet_name = SHEET_KEY_TO_NAME[sheet_key]
        try:
            tasks = await get_all_tasks(sheet_name)
        except SheetsServiceError:
            await self.send_text(ctx,
                "Не удалось загрузить список задач.",
                attachments=KeyboardFactory.home_only_menu(),
            )
            return

        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)

        if not tasks:
            await self.send_text(ctx,
                "Список аварий пуст" if sheet_key == "accidents" else "Список задач пуст",
                attachments=KeyboardFactory.home_only_menu(),
            )
            return

        task_views = [TaskMapper.from_sheet_row(sheet_key, row) for row in tasks]
        latest_tasks = task_views[-30:]
        note = ""
        if len(task_views) > 30:
            note = "\n\nПоказаны последние 30 аварий" if sheet_key == "accidents" else "\n\nПоказаны последние 30 задач"
        payload = [
            {"task_name": t.task_name or "Без названия", "row_index": t.row_index}
            for t in latest_tasks
        ]
        await self.send_text(ctx,
            f"{'Выберите аварию' if sheet_key == 'accidents' else 'Выберите задачу'}{note}",
            attachments=KeyboardFactory.task_list_keyboard(payload, sheet_key),
        )
        await self.send_text(ctx,
            "Используйте кнопки ниже для навигации",
            attachments=KeyboardFactory.navigation_menu(),
        )

    async def start_take_in_work(self, ctx: MaxCtx) -> int:
        """Запускает перевод задачи в лист 'В работе'."""

        _, sheet_key, row_index_raw = ctx.callback_payload.split("_", maxsplit=2)
        row_index = int(row_index_raw)

        try:
            task = await self.fetch_task(sheet_key, row_index)
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось загрузить задачу для перевода в работу.")
            return CONV_END

        if task is None:
            await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
            await self.send_text(ctx,
                "Авария не найдена" if sheet_key == "accidents" else "Задача не найдена",
                attachments=KeyboardFactory.home_only_menu(),
            )
            return CONV_END

        if sheet_key == "accidents":
            ctx.user_data["flow_mode"] = "take_accident_in_work"
            ctx.user_data["flow_data"] = {
                "sheet_key": sheet_key,
                "row_index": row_index,
                "task_name": task.task_name,
                "current_comments": task.comments,
                "deadline": task.deadline,
                "added_by": task.added_by,
            }
            await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
            await self._ask_take_responsible(ctx)
            return AdminStates.TAKE_IN_WORK_RESPONSIBLE

        ctx.user_data["flow_mode"] = "take_in_work"
        ctx.user_data["flow_data"] = {
            "sheet_key": sheet_key,
            "row_index": row_index,
            "task_name": task.task_name,
            "current_comments": task.comments,
            "deadline": task.deadline,
            "added_by": task.added_by,
        }

        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        await self._ask_take_comment(ctx)
        return AdminStates.TAKE_IN_WORK_COMMENTS

    async def receive_take_comment(self, ctx: MaxCtx) -> int:
        """Принимает комментарий для перевода в работу."""

        ctx.user_data.setdefault("flow_data", {})["take_comments"] = (ctx.text or '').strip()
        await self.message_manager.delete_step_messages(ctx)
        await self._ask_take_responsible(ctx)
        return AdminStates.TAKE_IN_WORK_RESPONSIBLE

    async def finish_take_in_work(self, ctx: MaxCtx) -> int:
        """Переносит задачу в лист 'В работе'."""

        flow_data = ctx.user_data.setdefault("flow_data", {})
        responsible = (ctx.text or '').strip()

        if ctx.user_data.get("flow_mode") == "take_accident_in_work":
            row_data = [
                self.now_date(),
                flow_data["task_name"],
                flow_data["current_comments"],
                responsible,
                flow_data["deadline"],
                flow_data["added_by"],
            ]

            try:
                await move_task(
                    ACCIDENTS_SHEET,
                    IN_PROGRESS_SHEET,
                    int(flow_data["row_index"]),
                    {"row_data": row_data},
                )
            except SheetsServiceError:
                await self.message_manager.delete_step_messages(ctx)
                await self.show_error(ctx, "Не удалось перевести аварию в работу.")
                return CONV_END

            who = get_user_display_name(ctx.user_proxy)
            await write_log(
                who,
                "Авария взята в работу",
                flow_data["task_name"],
                "Аварии → В работе",
                f"Ответственные: {responsible}",
            )

            await self.message_manager.delete_step_messages(ctx)
            ctx.user_data.pop("flow_data", None)
            ctx.user_data.pop("flow_mode", None)
            await self.send_text(ctx, "Авария переведена в статус «В работе»")
            await self.show_main_menu(ctx.user_id, ctx.user_data)
            return CONV_END

        comments = (
            flow_data["current_comments"]
            if flow_data["take_comments"] == "-"
            else flow_data["take_comments"]
        )

        row_data = [
            self.now_date(),
            flow_data["task_name"],
            comments,
            responsible,
            flow_data["deadline"],
            flow_data["added_by"],
        ]

        try:
            await move_task(
                NOT_STARTED_SHEET,
                IN_PROGRESS_SHEET,
                int(flow_data["row_index"]),
                {"row_data": row_data},
            )
        except SheetsServiceError:
            await self.message_manager.delete_step_messages(ctx)
            await self.show_error(ctx, "Не удалось перевести задачу в работу.")
            return CONV_END

        who = get_user_display_name(ctx.user_proxy)
        await write_log(
            who,
            "Задача взята в работу",
            flow_data["task_name"],
            "Не начатые → В работе",
            f"Ответственные: {responsible}",
        )

        await self.message_manager.delete_step_messages(ctx)
        ctx.user_data.pop("flow_data", None)
        ctx.user_data.pop("flow_mode", None)
        await self.send_text(ctx, "Задача переведена в статус «В работе»")
        await self.show_main_menu(ctx.user_id, ctx.user_data)
        return CONV_END

    async def complete_accident(self, ctx: MaxCtx) -> int:
        """Переводит аварию из листа 'Аварии' в лист 'Выполненные'."""

        row_index = int(ctx.callback_payload.split("_")[-1])

        try:
            task = await self.fetch_task("accidents", row_index)
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось загрузить аварию для завершения.")
            return CONV_END

        if task is None:
            await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
            await self.send_text(ctx, "Авария не найдена", attachments=KeyboardFactory.home_only_menu())
            return CONV_END

        row_data = [
            self.now_date(),
            task.task_name,
            task.comments,
            task.responsible,
            task.deadline,
            task.added_by,
        ]

        try:
            await move_task(
                ACCIDENTS_SHEET,
                COMPLETED_SHEET,
                row_index,
                {"row_data": row_data},
            )
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось отметить аварию как выполненную.")
            return CONV_END

        who = get_user_display_name(ctx.user_proxy)
        await write_log(
            who,
            "Авария выполнена",
            task.task_name,
            "Аварии → Выполненные",
            f"Ответственные: {task.responsible}",
        )

        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        await self.send_text(ctx, "Авария отмечена как выполненная")
        await self.show_main_menu(ctx.user_id, ctx.user_data)
        return CONV_END

    async def complete_task(self, ctx: MaxCtx) -> int:
        """Переводит задачу из листа 'В работе' в лист 'Выполненные'."""

        _, sheet_key, row_index_raw = ctx.callback_payload.split("_", maxsplit=2)
        row_index = int(row_index_raw)

        try:
            task = await self.fetch_task(sheet_key, row_index)
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось загрузить задачу для завершения.")
            return CONV_END

        if task is None:
            await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
            await self.send_text(ctx, "Задача не найдена", attachments=KeyboardFactory.home_only_menu())
            return CONV_END

        row_data = [
            self.now_date(),
            task.task_name,
            task.comments,
            task.responsible,
            task.deadline,
            task.added_by,
        ]

        try:
            await move_task(
                IN_PROGRESS_SHEET,
                COMPLETED_SHEET,
                row_index,
                {"row_data": row_data},
            )
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось отметить задачу как выполненную.")
            return CONV_END

        who = get_user_display_name(ctx.user_proxy)
        await write_log(
            who,
            "Задача выполнена",
            task.task_name,
            "В работе → Выполненные",
            f"Ответственные: {task.responsible}",
        )

        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        await self.send_text(ctx, "Задача отмечена как выполненная")
        await self.show_main_menu(ctx.user_id, ctx.user_data)
        return CONV_END

    async def go_back(self, ctx: MaxCtx) -> int:
        """Возвращает администратора на предыдущий шаг текущего сценария."""

        mode = ctx.user_data.get("flow_mode")
        current_state = AdminStates(ctx.user_data.get("current_state", AdminStates.ADD_TASK_NAME))
        flow_data = ctx.user_data.setdefault("flow_data", {})

        await self.message_manager.delete_step_messages(ctx)

        if mode == "admin_add":
            if current_state == AdminStates.ADD_TASK_NAME:
                ctx.user_data.pop("flow_data", None)
                ctx.user_data.pop("flow_mode", None)
                await self.show_main_menu(ctx.user_id, ctx.user_data)
                return CONV_END
            if current_state == AdminStates.ADD_COMMENTS:
                flow_data.pop("task_name", None)
                await self._ask_add_task_name(ctx)
                return AdminStates.ADD_TASK_NAME
            if current_state == AdminStates.ADD_RESPONSIBLE:
                flow_data.pop("comments", None)
                await self._ask_add_comments(ctx)
                return AdminStates.ADD_COMMENTS
            if current_state == AdminStates.ADD_FULL_NAME:
                flow_data.pop("responsible", None)
                await self._ask_add_responsible(ctx)
                return AdminStates.ADD_RESPONSIBLE

            flow_data.pop("full_name", None)
            await self._ask_add_full_name(ctx)
            return AdminStates.ADD_FULL_NAME

        if mode == "accidents_menu":
            ctx.user_data.pop("flow_data", None)
            ctx.user_data.pop("flow_mode", None)
            await self.show_main_menu(ctx.user_id, ctx.user_data)
            return CONV_END

        if mode == "accidents_list":
            ctx.user_data["flow_mode"] = "accidents_menu"
            return await self.show_accidents_menu(ctx)

        if mode == "admin_add_accident":
            if current_state == AdminStates.ADMIN_ACCIDENT_SHORT:
                ctx.user_data.pop("flow_data", None)
                ctx.user_data["flow_mode"] = "accidents_menu"
                return await self.show_accidents_menu(ctx)
            if current_state == AdminStates.ADMIN_ACCIDENT_DETAIL:
                flow_data.pop("accident_short", None)
                await self._ask_admin_accident_short(ctx)
                return AdminStates.ADMIN_ACCIDENT_SHORT
            if current_state == AdminStates.ADMIN_ACCIDENT_RESPONSIBLE:
                flow_data.pop("accident_detail", None)
                await self._ask_admin_accident_detail(ctx)
                return AdminStates.ADMIN_ACCIDENT_DETAIL
            if current_state == AdminStates.ADMIN_ACCIDENT_URGENCY:
                flow_data.pop("accident_responsible", None)
                await self._ask_admin_accident_responsible(ctx)
                return AdminStates.ADMIN_ACCIDENT_RESPONSIBLE

            flow_data.pop("accident_urgency", None)
            await self._ask_admin_accident_urgency(ctx)
            return AdminStates.ADMIN_ACCIDENT_URGENCY

        if mode == "edit":
            if current_state == AdminStates.EDIT_TASK_NAME:
                ctx.user_data.pop("flow_data", None)
                ctx.user_data.pop("flow_mode", None)
                return await self._return_to_current_task_card(ctx)
            if current_state == AdminStates.EDIT_COMMENTS:
                flow_data.pop("new_task_name", None)
                await self._ask_edit_task_name(ctx)
                return AdminStates.EDIT_TASK_NAME
            if current_state == AdminStates.EDIT_DEADLINE:
                flow_data.pop("new_comments", None)
                await self._ask_edit_comments(ctx)
                return AdminStates.EDIT_COMMENTS
            if current_state == AdminStates.EDIT_RESPONSIBLE:
                flow_data.pop("new_deadline", None)
                await self._ask_edit_deadline(ctx)
                return AdminStates.EDIT_DEADLINE

        if mode == "edit_accident":
            if current_state == AdminStates.EDIT_ACCIDENT_B:
                flow_data.pop("new_title", None)
                return await self._return_to_current_task_card(ctx)
            if current_state == AdminStates.EDIT_ACCIDENT_C:
                flow_data.pop("new_description", None)
                await self._ask_edit_accident_description(ctx)
                return AdminStates.EDIT_ACCIDENT_B
            if current_state == AdminStates.EDIT_ACCIDENT_D:
                flow_data.pop("new_responsible", None)
                await self._ask_edit_accident_responsible(ctx)
                return AdminStates.EDIT_ACCIDENT_C
            if current_state == AdminStates.EDIT_ACCIDENT_E:
                flow_data.pop("new_urgency", None)
                await self._ask_edit_accident_urgency(ctx)
                return AdminStates.EDIT_ACCIDENT_D
            if current_state == AdminStates.EDIT_ACCIDENT_F:
                flow_data.pop("new_who", None)
                await self._ask_edit_accident_who(ctx)
                return AdminStates.EDIT_ACCIDENT_E

        if mode == "take_in_work":
            if current_state == AdminStates.TAKE_IN_WORK_COMMENTS:
                ctx.user_data.pop("flow_data", None)
                ctx.user_data.pop("flow_mode", None)
                return await self._return_to_current_task_card(ctx)

            flow_data.pop("take_comments", None)
            await self._ask_take_comment(ctx)
            return AdminStates.TAKE_IN_WORK_COMMENTS

        if mode == "take_accident_in_work":
            ctx.user_data.pop("flow_data", None)
            ctx.user_data.pop("flow_mode", None)
            return await self._return_to_current_task_card(ctx)

        await self.show_main_menu(ctx.user_id, ctx.user_data)
        return CONV_END

    async def go_home(self, ctx: MaxCtx) -> int:
        """Завершает активный административный сценарий и возвращает в меню."""

        await self.message_manager.delete_step_messages(ctx)
        ctx.user_data.pop("flow_data", None)
        ctx.user_data.pop("flow_mode", None)
        ctx.user_data.pop("current_task", None)
        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        await self.show_main_menu(ctx.user_id, ctx.user_data)
        return CONV_END

    async def cancel(self, ctx: MaxCtx) -> int:
        """Отменяет текущий административный сценарий."""

        await self.message_manager.delete_step_messages(ctx)
        ctx.user_data.pop("flow_data", None)
        ctx.user_data.pop("flow_mode", None)
        ctx.user_data.pop("current_task", None)
        await self.message_manager.cleanup_session(ctx.user_id, ctx.user_data)
        await self.send_text(ctx, "Действие отменено")
        await self.show_main_menu(ctx.user_id, ctx.user_data)
        return CONV_END

    async def _ask_add_task_name(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.ADD_TASK_NAME
        await self.send_text(ctx,
            "Введите наименование задачи",
            attachments=KeyboardFactory.navigation_menu(include_back=False),
            remember_as_last=True,
        )

    async def _ask_add_comments(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.ADD_COMMENTS
        await self.send_text(ctx,
            "Введите комментарии к задаче (или «-» чтобы пропустить)",
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_add_responsible(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.ADD_RESPONSIBLE
        await self.send_text(ctx,
            "Введите ответственных (или «-» чтобы пропустить)",
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_add_full_name(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.ADD_FULL_NAME
        await self.send_text(ctx,
            "Введите ваши ФИО",
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_add_deadline(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.ADD_DEADLINE
        await self.send_text(ctx,
            "Введите срок выполнения (или «-» чтобы пропустить)",
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_admin_accident_short(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.ADMIN_ACCIDENT_SHORT
        await self.send_text(ctx,
            "Введите краткое описание аварии и укажите на каком участке она произошла:",
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_admin_accident_detail(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.ADMIN_ACCIDENT_DETAIL
        await self.send_text(ctx,
            "Введите подробное описание произошедшего (или «-» чтобы пропустить):",
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_admin_accident_responsible(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.ADMIN_ACCIDENT_RESPONSIBLE
        await self.send_text(ctx,
            "Введите ответственных (или «-» чтобы пропустить):",
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_admin_accident_urgency(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.ADMIN_ACCIDENT_URGENCY
        await self.send_text(ctx,
            "Как срочно требуется ремонт? (или «-» чтобы пропустить):",
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_admin_accident_who(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.ADMIN_ACCIDENT_WHO
        await self.send_text(ctx,
            "Введите ваши ФИО:",
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    @staticmethod
    def _format_edit_current_value(value: str | None) -> str:
        """Возвращает строку текущего значения для вставки в сообщение (MarkdownV2). Пустое — «(не заполнено)»."""

        if value is None or str(value).strip() == "":
            return "*(не заполнено)*"
        return TextFormatter.escape(str(value).strip())

    async def _ask_edit_task_name(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.EDIT_TASK_NAME
        current_task = ctx.user_data.get("current_task") or {}
        current_b = current_task.get("B") or ""
        display = self._format_edit_current_value(current_b)
        text = (
            "✏️ Редактирование названия задачи\n\n"
            "Текущее значение:\n"
            f"{display}\n\n"
            "Введите новое название или «\\-» чтобы оставить без изменений\\."
        )
        await self.send_preformatted_text(ctx,
            text,
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_edit_comments(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.EDIT_COMMENTS
        current_task = ctx.user_data.get("current_task") or {}
        current_c = current_task.get("C") or ""
        display = self._format_edit_current_value(current_c)
        text = (
            "✏️ Редактирование комментария\n\n"
            "Текущее значение:\n"
            f"{display}\n\n"
            "Введите новый комментарий или «\\-» чтобы оставить без изменений\\."
        )
        await self.send_preformatted_text(ctx,
            text,
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_edit_deadline(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.EDIT_DEADLINE
        current_task = ctx.user_data.get("current_task") or {}
        current_e = current_task.get("E") or ""
        display = self._format_edit_current_value(current_e)
        text = (
            "✏️ Редактирование срока выполнения\n\n"
            "Текущее значение:\n"
            f"{display}\n\n"
            "Введите новый срок или «\\-» чтобы оставить без изменений\\."
        )
        await self.send_preformatted_text(ctx,
            text,
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_edit_responsible(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.EDIT_RESPONSIBLE
        current_task = ctx.user_data.get("current_task") or {}
        current_d = current_task.get("D") or ""
        display = self._format_edit_current_value(current_d)
        text = (
            "✏️ Редактирование ответственных\n\n"
            "Текущее значение:\n"
            f"{display}\n\n"
            "Введите новых ответственных или «\\-» чтобы оставить без изменений\\."
        )
        await self.send_preformatted_text(ctx,
            text,
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_edit_accident_title(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.EDIT_ACCIDENT_B
        current_task = ctx.user_data.get("current_task") or {}
        display = self._format_edit_current_value(current_task.get("B") or "")
        text = (
            "✏️ *Редактирование краткого описания*\n\n"
            "Текущее значение:\n"
            f"{display}\n\n"
            "Введите новое значение или «\\-» чтобы оставить без изменений\\."
        )
        await self.send_preformatted_text(ctx,
            text,
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_edit_accident_description(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.EDIT_ACCIDENT_C
        current_task = ctx.user_data.get("current_task") or {}
        current_c = current_task.get("C") or ""
        display = self._format_edit_current_value(current_c)
        text = (
            "✏️ *Редактирование подробного описания*\n\n"
            "Текущее значение:\n"
            f"{display}\n\n"
            "Введите новое описание или «\\-» чтобы оставить без изменений\\."
        )
        await self.send_preformatted_text(ctx,
            text,
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_edit_accident_responsible(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.EDIT_ACCIDENT_D
        current_task = ctx.user_data.get("current_task") or {}
        current_d = current_task.get("D") or ""
        display = self._format_edit_current_value(current_d)
        text = (
            "✏️ *Редактирование ответственных*\n\n"
            "Текущее значение:\n"
            f"{display}\n\n"
            "Введите новое значение или «\\-» чтобы оставить без изменений\\."
        )
        await self.send_preformatted_text(ctx,
            text,
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_edit_accident_urgency(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.EDIT_ACCIDENT_E
        current_task = ctx.user_data.get("current_task") or {}
        current_e = current_task.get("E") or ""
        display = self._format_edit_current_value(current_e)
        text = (
            "✏️ *Редактирование срочности*\n\n"
            "Текущее значение:\n"
            f"{display}\n\n"
            "Введите новое значение или «\\-» чтобы оставить без изменений\\."
        )
        await self.send_preformatted_text(ctx,
            text,
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_edit_accident_who(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.EDIT_ACCIDENT_F
        current_task = ctx.user_data.get("current_task") or {}
        current_f = current_task.get("F") or ""
        display = self._format_edit_current_value(current_f)
        text = (
            "✏️ *Редактирование автора записи*\n\n"
            "Текущее значение:\n"
            f"{display}\n\n"
            "Введите новое значение или «\\-» чтобы оставить без изменений\\."
        )
        await self.send_preformatted_text(ctx,
            text,
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_take_comment(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.TAKE_IN_WORK_COMMENTS
        await self.send_text(ctx,
            "Комментарии (оставьте текущие / введите новые / или «-»)",
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    async def _ask_take_responsible(self, ctx: MaxCtx) -> None:
        ctx.user_data["current_state"] = AdminStates.TAKE_IN_WORK_RESPONSIBLE
        await self.send_text(ctx,
            "Введите ответственных",
            attachments=KeyboardFactory.navigation_menu(),
            remember_as_last=True,
        )

    @staticmethod
    def _sheet_name(sheet_key: str) -> str:
        if sheet_key == "todo":
            return NOT_STARTED_SHEET
        if sheet_key == "progress":
            return IN_PROGRESS_SHEET
        if sheet_key == "accidents":
            return ACCIDENTS_SHEET
        return COMPLETED_SHEET

    async def _fetch_row(self, sheet_name: str, row_index: int) -> dict | None:
        """Возвращает сырую строку листа по номеру строки."""

        rows = await get_all_tasks(sheet_name)
        for row in rows:
            if int(row["row_index"]) == row_index:
                return row
        return None

    async def _return_to_current_task_card(self, ctx: MaxCtx) -> int:
        """Возвращает пользователя к карточке текущей задачи или аварии."""

        current_task = ctx.user_data.get("current_task") or ctx.user_data.get("selected_task") or {}
        sheet_key = current_task.get("sheet_key")
        row_index = current_task.get("row_index")
        if not sheet_key or row_index is None:
            await self.show_main_menu(ctx.user_id, ctx.user_data)
            return CONV_END

        try:
            task = await self.fetch_task(str(sheet_key), int(row_index))
        except SheetsServiceError:
            await self.show_error(ctx, "Не удалось загрузить задачу.")
            return CONV_END

        if task is None:
            await self.send_text(ctx,
                "Авария не найдена." if sheet_key == "accidents" else "Задача не найдена.",
                attachments=KeyboardFactory.home_only_menu(),
            )
            return CONV_END

        ctx.user_data["selected_task"] = {
            "sheet_key": sheet_key,
            "row_index": int(row_index),
        }
        ctx.user_data["current_task"] = {
            "sheet_name": task.sheet_name,
            "sheet_key": sheet_key,
            "row_index": int(row_index),
            "A": task.date or "",
            "B": task.task_name or "",
            "C": task.comments or "",
            "D": task.responsible or "",
            "E": task.deadline or "",
            "F": task.added_by or "",
        }

        await self.send_preformatted_text(ctx,
            TextFormatter.task_details(task),
            attachments=KeyboardFactory.task_detail_keyboard(
                str(sheet_key), int(row_index), is_admin=True
            ),
        )
        await self.send_text(ctx,
            "Используйте кнопки ниже для навигации",
            attachments=KeyboardFactory.navigation_menu(),
        )
        return CONV_END

