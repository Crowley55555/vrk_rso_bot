from pathlib import Path

P = Path(__file__).resolve().parents[1] / "max_bot" / "handlers" / "admin.py"


def main() -> None:
    t = P.read_text(encoding="utf-8")

    t = t.replace(
        """    async def safe_delete_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        message_id: int | None,
    ) -> None:
        \"\"\"Безопасно удаляет сообщение через общий менеджер сообщений.\"\"\"

        if update.effective_chat is None:
            return
        await self.message_manager.delete_message(ctx.user_id, context, message_id)
""",
        """    async def safe_delete_message(
        self,
        ctx: MaxCtx,
        message_id: str | None,
    ) -> None:
        \"\"\"Безопасно удаляет сообщение через общий менеджер сообщений.\"\"\"

        await self.message_manager.delete_message(ctx.user_id, ctx.user_data, message_id)
""",
    )

    reps = [
        ("cleanup_session(ctx.user_id, context)", "cleanup_session(ctx.user_id, ctx.user_data)"),
        ("delete_message(ctx.user_id, context,", "delete_message(ctx.user_id, ctx.user_data,"),
        ("remember_message(context,", "remember_message(ctx.user_data,"),
        ("_show_task_list_for_sheet(update, context,", "_show_task_list_for_sheet(ctx,"),
        ("_send_accidents_menu(update, context)", "_send_accidents_menu(ctx)"),
        ("show_accidents_menu(update, context)", "show_accidents_menu(ctx)"),
        ("show_logs(update, context)", "show_logs(ctx)"),
        ("_ask_add_task_name(update, context)", "_ask_add_task_name(ctx)"),
        ("_ask_add_comments(update, context)", "_ask_add_comments(ctx)"),
        ("_ask_add_responsible(update, context)", "_ask_add_responsible(ctx)"),
        ("_ask_add_full_name(update, context)", "_ask_add_full_name(ctx)"),
        ("_ask_add_deadline(update, context)", "_ask_add_deadline(ctx)"),
        ("_ask_admin_accident_short(update, context)", "_ask_admin_accident_short(ctx)"),
        ("_ask_admin_accident_detail(update, context)", "_ask_admin_accident_detail(ctx)"),
        ("_ask_admin_accident_responsible(update, context)", "_ask_admin_accident_responsible(ctx)"),
        ("_ask_admin_accident_urgency(update, context)", "_ask_admin_accident_urgency(ctx)"),
        ("_ask_admin_accident_who(update, context)", "_ask_admin_accident_who(ctx)"),
        ("_ask_edit_task_name(update, context)", "_ask_edit_task_name(ctx)"),
        ("_ask_edit_comments(update, context)", "_ask_edit_comments(ctx)"),
        ("_ask_edit_deadline(update, context)", "_ask_edit_deadline(ctx)"),
        ("_ask_edit_responsible(update, context)", "_ask_edit_responsible(ctx)"),
        ("_ask_edit_accident_title(update, context)", "_ask_edit_accident_title(ctx)"),
        ("_ask_edit_accident_description(update, context)", "_ask_edit_accident_description(ctx)"),
        ("_ask_edit_accident_responsible(update, context)", "_ask_edit_accident_responsible(ctx)"),
        ("_ask_edit_accident_urgency(update, context)", "_ask_edit_accident_urgency(ctx)"),
        ("_ask_edit_accident_who(update, context)", "_ask_edit_accident_who(ctx)"),
        ("_ask_take_comment(update, context)", "_ask_take_comment(ctx)"),
        ("_ask_take_responsible(update, context)", "_ask_take_responsible(ctx)"),
        ("_apply_edit_and_finish(update, context)", "_apply_edit_and_finish(ctx)"),
        ("_apply_accident_edit_and_finish(update, context)", "_apply_accident_edit_and_finish(ctx)"),
        ("_return_to_current_task_card(update, context)", "_return_to_current_task_card(ctx)"),
        ("await self.safe_delete_message(update, context,", "await self.safe_delete_message(ctx,"),
        ("        query = update.callback_query\n", ""),
    ]
    for a, b in reps:
        t = t.replace(a, b)

    t = t.replace(
        "async def _show_task_list_for_sheet(\n        self,\n        update: Update,\n        context: ContextTypes.DEFAULT_TYPE,\n        sheet_key: str,\n    ) -> None:",
        "async def _show_task_list_for_sheet(\n        self,\n        ctx: MaxCtx,\n        sheet_key: str,\n    ) -> None:",
    )

    t = t.replace(
        """        if update.callback_query:
            await update.callback_query.answer()
        await self.show_logs(ctx)""",
        """        await ctx.answer_callback()
        await self.show_logs(ctx)""",
    )

    t = t.replace(
        """        if update.message:
            self.message_manager.remember_user_message(ctx)
            await self.message_manager.delete_message(
                ctx.user_id,
                ctx.user_data,
                update.message.message_id,
            )

        await self._ask_add_task_name(ctx)""",
        """        if ctx.incoming_message_mid:
            self.message_manager.remember_user_message(ctx)
            await self.message_manager.delete_message(
                ctx.user_id,
                ctx.user_data,
                ctx.incoming_message_mid,
            )

        await self._ask_add_task_name(ctx)""",
    )

    t = t.replace(
        """        if update.message:
            self.message_manager.remember_user_message(ctx)
            await self.safe_delete_message(ctx, update.message.message_id)""",
        """        if ctx.incoming_message_mid:
            self.message_manager.remember_user_message(ctx)
            await self.safe_delete_message(ctx, ctx.incoming_message_mid)""",
    )

    t = t.replace(
        """        confirmation_text = (
            f"⚠️ Вы уверены, что хотите удалить {entity_name}\\?\\n\\n"
            f"{marker} " + TextFormatter.escape(task.task_name) + "\\n\\n"
            "Это действие необратимо\\."
        )
        msg = await context.bot.send_message(
            chat_id=ctx.user_id,
            text=confirmation_text,
            parse_mode=ParseMode.MARKDOWN_V2,
            attachments=KeyboardFactory.delete_confirm_keyboard(sheet_key, row_index),
        )
        self.message_manager.remember_message(ctx.user_data, msg.message_id)""",
        """        confirmation_text = (
            f"⚠️ **Вы уверены, что хотите удалить {entity_name}?**\\n\\n"
            f"{marker} " + TextFormatter.escape(task.task_name) + "\\n\\n"
            "**Это действие необратимо.**"
        )
        mid_del = await self.max_api.send_message(
            ctx.user_id,
            text=confirmation_text.replace("\\\\n", "\\n"),
            attachments=KeyboardFactory.delete_confirm_keyboard(sheet_key, row_index),
            format_="markdown",
        )
        if mid_del:
            self.message_manager.remember_message(ctx.user_data, mid_del)""",
    )

    # Fix botched confirmation - the original uses \n in f-string - let me simpler replace block
    old = """        confirmation_text = (
            f"⚠️ Вы уверены, что хотите удалить {entity_name}\\?\\n\\n"
            f"{marker} " + TextFormatter.escape(task.task_name) + "\\n\\n"
            "Это действие необратимо\\."
        )
        msg = await context.bot.send_message(
            chat_id=ctx.user_id,
            text=confirmation_text,
            parse_mode=ParseMode.MARKDOWN_V2,
            attachments=KeyboardFactory.delete_confirm_keyboard(sheet_key, row_index),
        )
        self.message_manager.remember_message(ctx.user_data, msg.message_id)"""

    new = """        confirmation_text = (
            f"⚠️ **Вы уверены, что хотите удалить {entity_name}?**\\n\\n"
            f"{marker} " + TextFormatter.escape(task.task_name) + "\\n\\n"
            "**Это действие необратимо.**"
        )
        mid_del = await self.max_api.send_message(
            ctx.user_id,
            text=confirmation_text.replace("\\\\n", "\\n"),
            attachments=KeyboardFactory.delete_confirm_keyboard(sheet_key, row_index),
            format_="markdown",
        )
        if mid_del:
            self.message_manager.remember_message(ctx.user_data, mid_del)"""

    if old in t:
        t = t.replace(old, new)
    else:
        # try without double backslash in search
        t = t.replace(
            'f"⚠️ Вы уверены, что хотите удалить {entity_name}\\\\?\\n\\n"',
            'f"⚠️ **Вы уверены, что хотите удалить {entity_name}?**\\n\\n"',
        )
        t = t.replace('"Это действие необратимо\\\\."', '"**Это действие необратимо.**"')
        t = t.replace(
            """        msg = await context.bot.send_message(
            chat_id=ctx.user_id,
            text=confirmation_text,
            parse_mode=ParseMode.MARKDOWN_V2,
            attachments=KeyboardFactory.delete_confirm_keyboard(sheet_key, row_index),
        )
        self.message_manager.remember_message(ctx.user_data, msg.message_id)""",
            """        mid_del = await self.max_api.send_message(
            ctx.user_id,
            text=confirmation_text,
            attachments=KeyboardFactory.delete_confirm_keyboard(sheet_key, row_index),
            format_="markdown",
        )
        if mid_del:
            self.message_manager.remember_message(ctx.user_data, mid_del)""",
        )

    P.write_text(t, encoding="utf-8")
    print("fixed", P)


if __name__ == "__main__":
    main()
