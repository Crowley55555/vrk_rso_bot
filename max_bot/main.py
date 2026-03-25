from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
from typing import Any
from max_bot.config import BACK_BUTTON, HOME_BUTTON, is_max_report_accident_text, load_settings
from max_bot.handlers.admin import AdminTaskHandler
from max_bot.handlers.common_max import CommonHandlersMax, MaxCtx, MaxMessageManager
from max_bot.handlers.user import UserTaskHandlerMax
from max_bot.max_api import MaxApi, _extract_mid, message_body_text, sender_user_id
from max_bot.states import CONV_END, AdminStates, UserStates
from shared.api_client import configure_api_client


logger = logging.getLogger(__name__)

user_states: dict[int, int] = {}
user_data: dict[int, dict] = {}
# MAX после callback по «Назад»/«Домой» иногда шлёт ещё message_created с тем же текстом — игнорируем короткое окно.
_suppress_nav_text_echo_until: dict[int, float] = {}


def _ud(uid: int) -> dict:
    return user_data.setdefault(uid, {})


def _set_state_after_admin_int(uid: int, result: int) -> None:
    if result == CONV_END:
        user_states.pop(uid, None)
    else:
        user_states[uid] = result


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _parse_callback_update(raw: dict) -> tuple[str, str, int | None, str | None, dict | None]:
    """Разбор message_callback (см. MessageCallbackUpdate в MAX Bot API)."""

    cb: dict[str, Any] = {}
    if isinstance(raw.get("callback"), dict):
        cb = raw["callback"]
    elif isinstance(raw.get("message_callback"), dict):
        cb = raw["message_callback"]
    else:
        msg_outer = raw.get("message")
        if isinstance(msg_outer, dict):
            inner = msg_outer.get("callback") or msg_outer.get("message_callback")
            if isinstance(inner, dict):
                cb = inner
    if not cb:
        cb = raw if isinstance(raw, dict) else {}

    # У официального API сообщение с кнопками лежит в корне апдейта: { "callback": {...}, "message": {...} }
    msg_for_mid: dict[str, Any] = {}
    if isinstance(raw.get("message"), dict):
        msg_for_mid = raw["message"]
    elif isinstance(cb.get("message"), dict):
        msg_for_mid = cb["message"]

    callback_id = str(
        cb.get("callback_id")
        or cb.get("id")
        or raw.get("callback_id")
        or raw.get("id")
        or ""
    )
    payload_raw = cb.get("payload", raw.get("payload", ""))
    if isinstance(payload_raw, str):
        payload = payload_raw.strip()
    else:
        payload = str(payload_raw).strip() if payload_raw is not None else ""

    user = cb.get("user")
    if not isinstance(user, dict):
        user = {}

    uid = user.get("user_id")
    if uid is None:
        uid = sender_user_id(msg_for_mid)
    if uid is None and isinstance(raw.get("message"), dict):
        raw_msg = raw["message"]
        uid = sender_user_id(raw_msg)
        if not user and isinstance(raw_msg.get("sender"), dict):
            user = raw_msg["sender"]
        if not msg_for_mid:
            msg_for_mid = raw_msg

    if uid is None:
        uid = sender_user_id(msg_for_mid)

    if uid is not None:
        uid = int(uid)

    mid = _extract_mid(msg_for_mid)
    return callback_id, payload, uid, mid, user if user else None


def _parse_message_created(raw: dict) -> tuple[int | None, str, str | None, dict | None]:
    msg = raw.get("message") or {}
    uid = sender_user_id(msg)
    if uid is not None:
        uid = int(uid)
    text = message_body_text(msg)
    mid = _extract_mid(msg)
    sender = msg.get("sender") if isinstance(msg.get("sender"), dict) else None
    return uid, text, mid, sender


def _partition_updates(updates: list[dict[str, Any]]) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Порядок важен: сначала callback, потом message_created — иначе дублируются сценарии по тексту кнопки."""

    bot_started: list[dict] = []
    callbacks: list[dict] = []
    messages: list[dict] = []
    other: list[dict] = []
    for u in updates:
        ut = u.get("update_type")
        if ut == "bot_started":
            bot_started.append(u)
        elif ut == "message_callback":
            callbacks.append(u)
        elif ut == "message_created":
            messages.append(u)
        else:
            other.append(u)
    return bot_started, callbacks, messages, other


def _mark_nav_callback_echo_suppression(uid: int) -> None:
    _suppress_nav_text_echo_until[uid] = time.monotonic() + 0.85


def _should_suppress_nav_text_echo(uid: int) -> bool:
    until = _suppress_nav_text_echo_until.get(uid)
    if until is None:
        return False
    if time.monotonic() >= until:
        _suppress_nav_text_echo_until.pop(uid, None)
        return False
    return True


def _user_in_accident_wizard(state: int | None) -> bool:
    if state is None:
        return False
    s = int(state)
    return int(UserStates.ACCIDENT_SHORT) <= s <= int(UserStates.ACCIDENT_URGENCY)


def _admin_in_add_task_wizard(state: int | None) -> bool:
    if state is None:
        return False
    s = AdminStates(int(state))
    return AdminStates.ADD_TASK_NAME <= s <= AdminStates.ADD_DEADLINE


def _admin_in_add_accident_wizard(state: int | None) -> bool:
    if state is None:
        return False
    s = int(state)
    return int(AdminStates.ADMIN_ACCIDENT_SHORT) <= s <= int(AdminStates.ADMIN_ACCIDENT_WHO)


async def main_async() -> None:
    settings = load_settings()
    configure_api_client(settings.api_base_url, settings.api_key)

    max_api = MaxApi(settings.max_bot_token, settings.max_api_base)
    mm = MaxMessageManager(max_api)
    common = CommonHandlersMax(settings, mm, max_api)
    user_h = UserTaskHandlerMax(settings, mm, max_api)
    admin_h = AdminTaskHandler(settings, mm, max_api)

    marker: int | None = None
    logger.info("Max-бот запущен, long polling /updates")
    logger.info(
        "Если нажатия не доходят: в MAX нельзя одновременно Webhook и long polling — "
        "отключите webhook (DELETE /subscriptions) или не запускайте второй процесс бота."
    )

    try:
        while True:
            try:
                updates, marker = await max_api.get_updates(marker=marker)
            except Exception:
                logger.exception("Ошибка long polling, повтор через 5 с")
                await asyncio.sleep(5)
                continue

            bot_u, cb_u, msg_u, other_u = _partition_updates(updates)

            for upd in bot_u:
                u = upd.get("user")
                if not isinstance(u, dict) or u.get("user_id") is None:
                    continue
                uid = int(u["user_id"])
                ud = _ud(uid)
                ud.clear()
                user_states.pop(uid, None)
                await mm.cleanup_session(uid, ud)
                await common.start_clear(uid, ud)

            for upd in cb_u:
                cb_id, payload, uid, cb_mid, user_d = _parse_callback_update(upd)
                if uid is None:
                    logger.warning("message_callback без user_id: %s", list(upd.keys()))
                    continue
                ud = _ud(uid)
                ctx = MaxCtx(
                    user_id=uid,
                    user_data=ud,
                    max_api=max_api,
                    message_manager=mm,
                    callback_payload=payload,
                    callback_id=cb_id or None,
                    callback_message_mid=cb_mid,
                    sender=user_d,
                )
                await handle_callback(ctx, settings, common, user_h, admin_h)

            for upd in msg_u:
                uid, text, mid, sender = _parse_message_created(upd)
                if uid is None:
                    continue
                ud = _ud(uid)
                ctx = MaxCtx(
                    user_id=uid,
                    user_data=ud,
                    max_api=max_api,
                    message_manager=mm,
                    text=text,
                    incoming_message_mid=mid,
                    sender=sender,
                )
                await handle_message(ctx, settings, common, user_h, admin_h)

            for upd in other_u:
                ut = upd.get("update_type")
                logger.warning("MAX: пропуск update_type=%r keys=%s", ut, list(upd.keys()))
    finally:
        await max_api.aclose()


async def handle_message(
    ctx: MaxCtx,
    settings,
    common: CommonHandlersMax,
    user_h: UserTaskHandlerMax,
    admin_h: AdminTaskHandler,
) -> None:
    uid = ctx.user_id
    ud = ctx.user_data
    text = (ctx.text or "").strip()

    if text.startswith("/start"):
        ud.clear()
        user_states.pop(uid, None)
        await ctx.message_manager.cleanup_session(uid, ud)
        await common.start_clear(uid, ud)
        return

    if text.startswith("/cancel"):
        ud.clear()
        user_states.pop(uid, None)
        await ctx.message_manager.cleanup_session(uid, ud)
        await common.send_text(ctx, "Действие отменено")
        await common.show_main_menu(uid, ud)
        return

    is_adm = settings.is_admin(uid)
    st = user_states.get(uid)

    if text in (BACK_BUTTON, HOME_BUTTON):
        if _should_suppress_nav_text_echo(uid):
            if ctx.incoming_message_mid:
                await ctx.message_manager.delete_message(uid, ud, ctx.incoming_message_mid)
            return
        if is_adm:
            res = await admin_h.go_back(ctx) if st else await admin_h.go_home(ctx)
            _set_state_after_admin_int(uid, res)
        else:
            if st:
                if text == HOME_BUTTON:
                    r = await user_h.go_home(ctx)
                else:
                    r = await user_h.go_back(ctx)
                if r == CONV_END:
                    user_states.pop(uid, None)
                else:
                    user_states[uid] = r
            else:
                await common.go_home(ctx)
                user_states.pop(uid, None)
        return

    if is_adm:
        if st is not None:
            nxt = await dispatch_admin_text(st, ctx, admin_h)
            if nxt == CONV_END:
                user_states.pop(uid, None)
            else:
                user_states[uid] = nxt
            return
        await common.send_text(ctx, "Используйте кнопки меню или команду /start")
        return

    if st is not None:
        nxt = await dispatch_user_text(st, ctx, user_h)
        if nxt == CONV_END:
            user_states.pop(uid, None)
        else:
            user_states[uid] = nxt
        return

    if is_max_report_accident_text(text):
        user_states[uid] = await user_h.start(ctx)
        return

    await common.send_text(ctx, "Нажмите /start или выберите действие в меню.")


async def dispatch_user_text(state: int, ctx: MaxCtx, user_h: UserTaskHandlerMax) -> int:
    if state == int(UserStates.ACCIDENT_SHORT):
        return await user_h.receive_accident_short(ctx)
    if state == int(UserStates.ACCIDENT_DETAIL):
        return await user_h.receive_accident_detail(ctx)
    if state == int(UserStates.ACCIDENT_WHO):
        return await user_h.receive_accident_who(ctx)
    if state == int(UserStates.ACCIDENT_URGENCY):
        return await user_h.finish_creation(ctx)
    return CONV_END


async def dispatch_admin_text(state: int, ctx: MaxCtx, admin_h: AdminTaskHandler) -> int:
    s = AdminStates(state)
    if s == AdminStates.ADD_TASK_NAME:
        return await admin_h.receive_task_name(ctx)
    if s == AdminStates.ADD_COMMENTS:
        return await admin_h.receive_comments(ctx)
    if s == AdminStates.ADD_RESPONSIBLE:
        return await admin_h.receive_responsible(ctx)
    if s == AdminStates.ADD_FULL_NAME:
        return await admin_h.receive_full_name(ctx)
    if s == AdminStates.ADD_DEADLINE:
        return await admin_h.finish_add_task(ctx)
    if s == AdminStates.ADMIN_ACCIDENT_SHORT:
        return await admin_h.receive_admin_accident_short(ctx)
    if s == AdminStates.ADMIN_ACCIDENT_DETAIL:
        return await admin_h.receive_admin_accident_detail(ctx)
    if s == AdminStates.ADMIN_ACCIDENT_RESPONSIBLE:
        return await admin_h.receive_admin_accident_responsible(ctx)
    if s == AdminStates.ADMIN_ACCIDENT_URGENCY:
        return await admin_h.receive_admin_accident_urgency(ctx)
    if s == AdminStates.ADMIN_ACCIDENT_WHO:
        return await admin_h.finish_add_accident(ctx)
    if s == AdminStates.EDIT_TASK_NAME:
        return await admin_h.receive_edit_task_name(ctx)
    if s == AdminStates.EDIT_COMMENTS:
        return await admin_h.receive_edit_comment(ctx)
    if s == AdminStates.EDIT_DEADLINE:
        return await admin_h.receive_edit_deadline(ctx)
    if s == AdminStates.EDIT_RESPONSIBLE:
        return await admin_h.receive_edit_responsible(ctx)
    if s == AdminStates.EDIT_ACCIDENT_B:
        return await admin_h.receive_edit_accident_title(ctx)
    if s == AdminStates.EDIT_ACCIDENT_C:
        return await admin_h.receive_edit_accident_description(ctx)
    if s == AdminStates.EDIT_ACCIDENT_D:
        return await admin_h.receive_edit_accident_responsible(ctx)
    if s == AdminStates.EDIT_ACCIDENT_E:
        return await admin_h.receive_edit_accident_urgency(ctx)
    if s == AdminStates.EDIT_ACCIDENT_F:
        return await admin_h.receive_edit_accident_who(ctx)
    if s == AdminStates.TAKE_IN_WORK_COMMENTS:
        return await admin_h.receive_take_comment(ctx)
    if s == AdminStates.TAKE_IN_WORK_RESPONSIBLE:
        return await admin_h.finish_take_in_work(ctx)
    return CONV_END


async def handle_callback(
    ctx: MaxCtx,
    settings,
    common: CommonHandlersMax,
    user_h: UserTaskHandlerMax,
    admin_h: AdminTaskHandler,
) -> None:
    uid = ctx.user_id
    ud = ctx.user_data
    p = (ctx.callback_payload or "").strip()
    is_adm = settings.is_admin(uid)

    # Для текущих сценариев нам не нужен отдельный ответ на callback:
    # после нажатия мы сразу отправляем/обновляем сообщения сами.
    # Вызов POST /answers без notification/message даёт 400 и только шумит в логах.

    if p == "nav_home" or p == "home_menu":
        _mark_nav_callback_echo_suppression(uid)
        if is_adm:
            _set_state_after_admin_int(uid, await admin_h.go_home(ctx))
        else:
            await common.go_home_from_callback(ctx)
            user_states.pop(uid, None)
        return

    if p == "nav_back":
        _mark_nav_callback_echo_suppression(uid)
        if is_adm:
            _set_state_after_admin_int(uid, await admin_h.go_back(ctx))
        else:
            r = await user_h.go_back(ctx)
            if r == CONV_END:
                user_states.pop(uid, None)
            else:
                user_states[uid] = r
        return

    if p == "nav_back_accidents_menu":
        _mark_nav_callback_echo_suppression(uid)
        if is_adm:
            _set_state_after_admin_int(uid, await admin_h.go_back(ctx))
        return

    if not is_adm:
        if p == "menu_report_accident":
            if _user_in_accident_wizard(user_states.get(uid)):
                return
            user_states[uid] = await user_h.start(ctx)
        elif re.match(r"^task_(todo|progress|done|accidents)_\d+$", p):
            await common.show_task_card(ctx)
        return

    if p == "menu_add_task":
        if _admin_in_add_task_wizard(user_states.get(uid)):
            return
        _set_state_after_admin_int(uid, await admin_h.start_add_task(ctx))
        return
    if p == "menu_accidents":
        _set_state_after_admin_int(uid, await admin_h.show_accidents_menu(ctx))
        return
    if p == "menu_logs":
        await admin_h.show_logs(ctx)
        return
    if p == "menu_todo":
        await common.show_todo_tasks(ctx)
        return
    if p == "menu_progress":
        await common.show_in_progress_tasks(ctx)
        return
    if p == "menu_done":
        await common.show_done_tasks(ctx)
        return

    if p == "accidents_list":
        _set_state_after_admin_int(uid, await admin_h.show_accident_tasks_from_menu(ctx))
        return
    if p == "accidents_add":
        if _admin_in_add_accident_wizard(user_states.get(uid)):
            return
        _set_state_after_admin_int(uid, await admin_h.start_add_accident(ctx))
        return

    if p.startswith("log_"):
        await admin_h.show_log_detail(ctx)
        return
    if p == "back_to_logs":
        await admin_h.back_to_logs(ctx)
        return

    if ud.get("current_state") == int(AdminStates.ACCIDENTS_MENU) and re.match(r"^task_accidents_\d+$", p):
        await admin_h.show_task_card(ctx)
        return

    if re.match(r"^task_(todo|progress|done|accidents)_\d+$", p):
        await common.show_task_card(ctx)
        return

    if re.match(r"^edit_(todo|progress|done|accidents)_\d+$", p):
        _set_state_after_admin_int(uid, await admin_h.start_edit_task(ctx))
        return
    if re.match(r"^take_(todo|accidents)_\d+$", p):
        _set_state_after_admin_int(uid, await admin_h.start_take_in_work(ctx))
        return
    if re.match(r"^complete_progress_\d+$", p):
        _set_state_after_admin_int(uid, await admin_h.complete_task(ctx))
        return
    if re.match(r"^complete_accident_\d+$", p):
        _set_state_after_admin_int(uid, await admin_h.complete_accident(ctx))
        return
    if re.match(r"^mark_done_\d+$", p):
        _set_state_after_admin_int(uid, await admin_h.mark_done_from_todo(ctx))
        return
    if re.match(r"^delete_task_(todo|progress|done|accidents)_\d+$", p):
        await admin_h.show_delete_confirmation(ctx)
        return
    if re.match(r"^confirm_delete_(todo|progress|done|accidents)_\d+$", p):
        await admin_h.confirm_delete_task(ctx)
        return
    if re.match(r"^cancel_delete_(todo|progress|done|accidents)_\d+$", p):
        await admin_h.cancel_delete_task(ctx)
        return


def main() -> None:
    configure_logging()
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
