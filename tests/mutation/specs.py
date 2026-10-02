"""For each recorded fix: the edit that UNDOES it, and the tests that must then fail.

Each spec: id, file, edits=[(old, new, replace_all)], optional anchor (the search starts there),
tests. `run.py` applies the edits, runs the tests, and restores the file. When you fix a bug, add
its spec here — a fix whose revert no test notices is not a fix (docs/FIX_VERIFICATION.md).
"""

from typing import Any


def spec(**fields: Any) -> dict[str, Any]:
    """One fix: id, file, edits=[(old, new, replace_all)], optional anchor, tests."""
    return fields


T_ISO = "tests/security/test_tenant_isolation.py"
T_REV = "tests/security/test_review_regressions.py"
T_FJR = "tests/integration/test_followup_jobs_review.py"
T_QR = "tests/unit/test_task_queue_review.py"
T_CLK = "tests/unit/test_clock_review.py"
T_RS = "tests/bot/test_relay_safety.py"
T_ROB = "tests/bot/test_robustness.py"

SPECS = [
    # ── web security ──
    spec(
        id="SF-005",
        file="web/app.py",
        edits=[
            (
                "_API_AUTH = [Depends(require_signed_in), Depends(csrf_protect)]",
                "_API_AUTH = [Depends(csrf_protect)]",
                False,
            )
        ],
        tests=[
            "tests/security/test_auth_surface.py::test_auth_is_checked_before_body_validation",
            "tests/integration/test_smoke_web.py::test_every_api_route_rejects_anonymous_access",
        ],
    ),
    spec(
        id="SF-006 (403 owner check)",
        file="web/deps.py",
        edits=[('if apt.get("therapist_id") != therapist["id"]:', "if False:", False)],
        tests=[T_ISO],
    ),
    spec(
        id="SF-007",
        file="web/routers/api/treatment.py",
        anchor="async def send_recommendations",
        # the original bug: the appointment was resolved without the caller's tenant
        edits=[
            (
                'apt_id = await _resolve_apt_id(patient_id, apt_date, apt_time, therapist["id"])',
                'apt_id = __import__("bot.db", fromlist=["get_db"]).get_db().execute('
                '"SELECT id FROM appointments WHERE patient_id=? AND date=? AND time=?", '
                '(patient_id, apt_date, apt_time.replace("-", ":"))).fetchone()[0]',
                False,
            )
        ],
        tests=[T_ISO + "::test_a_cannot_touch_b_treatment_notes"],
    ),
    spec(
        id="SF-013",
        file="web/routers/api/appointments.py",
        anchor="belongs_to_therapist, body.existing_patient_id",
        edits=[("if not owned", "if False", False)],
        tests=[
            "tests/integration/test_patient_identity.py::test_another_therapists_patient_cannot_be_booked"
        ],
    ),
    spec(
        id="SF-014",
        file="web/app.py",
        anchor="def docs_urls",
        edits=[("if not is_dev:", "if False:", False)],
        tests=[
            "tests/security/test_route_inventory.py::test_the_api_documentation_is_not_served_outside_dev"
        ],
    ),
    spec(
        id="SF-015",
        file="web/routers/auth.py",
        anchor="_therapist, redirect = _active_therapist_or_redirect(request)",
        edits=[("if redirect:", "if False:", False)],
        tests=[
            "tests/security/test_route_inventory.py::test_google_consent_cannot_be_started_by_a_stranger"
        ],
    ),
    spec(
        id="F11",
        file="web/app.py",
        edits=[
            (
                "app.include_router(system_router, dependencies=_API_AUTH)",
                "app.include_router(system_router)",
                False,
            )
        ],
        tests=["tests/security/test_auth_surface.py::test_status_endpoints_require_a_session"],
    ),
    spec(
        id="A4 CSRF",
        file="web/csrf.py",
        edits=[
            (
                "if not cookie or not submitted or not secrets.compare_digest(submitted, cookie):",
                "if False:",
                False,
            )
        ],
        tests=[
            "tests/security/test_csrf.py::test_a_post_without_the_token_is_refused",
            "tests/security/test_csrf.py::test_a_post_with_a_wrong_token_is_refused",
        ],
    ),
    spec(
        id="A3 fixation",
        file="web/session_policy.py",
        anchor="def start(",
        edits=[("session.clear()", "pass", False)],
        tests=["tests/security/test_session_policy.py::test_signing_in_starts_a_fresh_session"],
    ),
    spec(
        id="A3 revocation",
        file="web/session_policy.py",
        edits=[
            ('if is_revoked(str(session.get(SID_KEY) or "")):', "if False:", False),
            ('return not is_revoked(str(session.get(SID_KEY) or ""))', "return True", False),
        ],
        tests=[
            "tests/security/test_session_policy.py::test_a_signed_out_cookie_is_refused_even_if_it_is_replayed"
        ],
    ),
    spec(
        id="F9a cookie Secure",
        file="web/app.py",
        edits=[('"https_only": not is_dev,', '"https_only": False,', False)],
        tests=["tests/security/test_auth_surface.py::test_cookie_is_secure_only_outside_dev"],
    ),
    spec(
        id="F9c sign-in lockout",
        file="web/routers/auth.py",
        edits=[("wait = await login_guard.check_locked(request, email)", "wait = 0", False)],
        tests=[
            "tests/security/test_login_guard.py::test_repeated_bad_passwords_lock_even_the_right_one"
        ],
    ),
    spec(
        id="F9c sign-up cap",
        file="web/routers/auth.py",
        edits=[("rate_limit.signup_per_minute()", "0", False)],
        tests=["tests/security/test_abuse_limits.py::test_signup_is_capped_per_ip"],
    ),
    spec(
        id="A11 AI rate limit",
        file="web/routers/api/treatment.py",
        edits=[("rate_limit.ai_per_minute()", "0", False)],
        tests=["tests/security/test_abuse_limits.py::test_the_ai_endpoints_are_capped_per_account"],
    ),
    spec(
        id="A11 message length",
        file="web/routers/api/messages.py",
        edits=[("text: str = Field(max_length=4096)", "text: str", False)],
        tests=["tests/security/test_input_limits.py::test_an_oversized_message_is_refused"],
    ),
    spec(
        id="5.1 open redirect",
        file="web/routers/auth.py",
        edits=[("or any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in path):", "or False:", False)],
        tests=[
            "tests/security/test_google_oauth_next.py::test_anything_but_a_same_site_path_is_ignored"
        ],
    ),
    spec(
        id="SF-018 calendar callback",
        file="web/routers/auth.py",
        anchor="async def auth_callback",
        edits=[("if not _verify_oauth_state(request, state):", "if False:", False)],
        tests=["tests/security/test_oauth_state.py"],
    ),
    spec(
        id="SF-018 registration callback",
        file="web/deps.py",
        anchor="async def _handle_reg_google",
        edits=[("if not _verify_oauth_state(request, state):", "if False:", False)],
        tests=["tests/security/test_oauth_state.py"],
    ),
    # ── XSS / output ──
    spec(
        id="SF-011 escHtml quote",
        file="web/static/js/treatment/main.js",
        edits=[(".replace(/'/g, '&#39;')", "", False)],
        tests=["tests/security/test_treatment_page_xss.py"],
    ),
    spec(
        id="SF-011 advice text",
        file="web/static/js/treatment/advice.js",
        edits=[("${escHtml(item.text)}", "${item.text}", False)],
        tests=["tests/security/test_treatment_page_xss.py"],
    ),
    spec(
        id="SF-012 alert banner",
        file="web/templates/dashboard.html",
        edits=[("row.append(", 'row.insertAdjacentHTML("beforeend", ', False)],
        tests=[
            "tests/integration/test_followup_no_channel.py::test_the_pages_never_render_alert_text_as_markup",
            "tests/security/test_dashboard_pages_xss.py",
        ],
    ),
    spec(
        id="SF-021 dashboard names",
        file="web/templates/dashboard.html",
        edits=[("${ZF.esc(a.patient_name)}", "${a.patient_name}", False)],
        tests=["tests/security/test_dashboard_pages_xss.py"],
    ),
    spec(
        id="SF-021 relay message text",
        file="web/templates/messages.html",
        edits=[("ZF.esc(msg.text)", "msg.text", False)],
        tests=["tests/security/test_dashboard_pages_xss.py"],
    ),
    spec(
        id="SF-016 inline handler",
        file="web/templates/settings.html",
        edits=[('data-click="load-status"', 'onclick="loadStatus()"', False)],
        tests=[
            "tests/security/test_dashboard_pages_xss.py::test_no_inline_event_handlers_on_any_page_or_script"
        ],
    ),
    spec(
        id="SF-016 CSP enforce default",
        file="zenflow/settings.py",
        edits=[("csp_enforce: bool = True", "csp_enforce: bool = False", False)],
        tests=["tests/security/test_csp.py::test_the_policy_enforces_by_default"],
    ),
    spec(
        id="SF-017 follow-up Markdown",
        file="bot/services/followup_scheduler.py",
        edits=[("cat_md = escape_markdown(cat, version=1)", "cat_md = cat", False)],
        tests=["tests/security/test_markdown_injection.py"],
    ),
    spec(
        id="SF-017 treatment Markdown",
        file="web/routers/api/treatment.py",
        edits=[("{escape_markdown(cat, version=1)}", "{cat}", False)],
        tests=["tests/security/test_markdown_injection.py"],
    ),
    spec(
        id="A8 point cap",
        file="bot/patient_bot/services/ai_intake.py",
        edits=[("if len(out) >= MAX_AI_POINTS:", "if False:", False)],
        tests=["tests/security/test_llm_injection.py"],
    ),
    spec(
        id="A8 certainty clamp",
        file="web/routers/api/treatment.py",
        edits=[
            (
                'max(0, min(100, int(parsed.get("diagnosis_certainty", 0))))',
                'int(parsed.get("diagnosis_certainty", 0))',
                False,
            )
        ],
        tests=["tests/security/test_llm_injection.py"],
    ),
    spec(
        id="A13 token redaction",
        file="zenflow/logging.py",
        edits=[('"<redacted:telegram-token>"', '"' + "\\g<0>" + '"', False)],
        tests=["tests/unit/test_logging.py::test_redact_scrubs_every_known_secret_shape"],
    ),
    spec(
        id="T2 email redaction",
        file="zenflow/logging.py",
        edits=[("<redacted:email>@", "", False)],
        tests=["tests/unit/test_logging.py::test_an_email_keeps_only_its_domain"],
    ),
    spec(
        id="A7 activation flood",
        file="bot/therapist_bot/handlers.py",
        edits=[('if await flood.too_fast("activation", user_id):', "if False:", False)],
        tests=["tests/bot/test_flood.py::test_activation_code_attempts_are_throttled"],
    ),
    spec(
        id="SF-019 Redis AUTH",
        file="zenflow/settings.py",
        edits=[("not (redis.password or redis.username)", "False", False)],
        tests=["tests/security/test_transit.py::test_prod_refuses_a_non_local_redis_without_auth"],
    ),
    spec(
        id="F7 default secret",
        file="zenflow/settings.py",
        edits=[
            (
                "if not self.session_secret or self.session_secret == DEFAULT_SESSION_SECRET:",
                "if False:",
                False,
            )
        ],
        tests=["tests/unit/test_settings.py"],
    ),
    spec(
        id="F7 separate key",
        file="zenflow/settings.py",
        edits=[("elif self.token_encryption_key == self.session_secret:", "elif False:", False)],
        tests=["tests/unit/test_settings.py::test_prod_requires_a_separate_token_encryption_key"],
    ),
    spec(
        id="F9b X-Frame-Options",
        file="web/csp.py",
        edits=[('"X-Frame-Options": "DENY",', "", False)],
        tests=["tests/security/test_csp.py::test_static_security_headers_ride_every_response"],
    ),
    spec(
        id="R0 legacy token",
        file="web/gcal.py",
        edits=[
            (
                "fernet_for(s.session_secret).decrypt(",
                'fernet_for("not-the-old-key").decrypt(',
                False,
            )
        ],
        tests=[T_REV + "::test_legacy_encrypted_google_token_still_loads"],
    ),
    spec(
        id="F12 crypto pin",
        file="requirements.txt",
        edits=[("cryptography==", "cryptography>=", False)],
        tests=[
            "tests/unit/test_tooling.py::test_requirements_are_fully_pinned_and_use_https_index"
        ],
    ),
    spec(
        id="SF-009 .env.example",
        file=".env.example",
        edits=[("\nSESSION_SECRET=\n", "\nSESSION_SECRET=   # generate one\n", False)],
        tests=[T_REV + "::test_env_example_parses_clean_and_boots"],
    ),
    spec(
        id="Tenant scope required",
        file="web/repositories/appointment_repo.py",
        edits=[
            (
                "AND therapist_id=? ORDER BY created_at DESC LIMIT 1",
                "ORDER BY created_at DESC LIMIT 1",
                False,
            ),
            (
                "(patient_id, apt_date, time_str, therapist_id),",
                "(patient_id, apt_date, time_str),",
                False,
            ),
        ],
        tests=["tests/security/test_tenant_scope_required.py", T_ISO],
    ),
    # ── bot audit ──
    spec(
        id="B1a end_relay",
        file="bot/patient_bot/services/relay.py",
        edits=[('if (r.get(current_key) or "") == str(patient_id):', "if False:", False)],
        tests=[T_RS + "::test_end_relay_clears_current_only_for_that_patient"],
    ),
    spec(
        id="B1c ambiguous free typing",
        file="bot/therapist_bot/handlers.py",
        edits=[("if len(active) > 1:", "if False:", False)],
        tests=[
            T_RS + "::test_free_typing_with_two_active_chats_asks_to_reply",
            "tests/security/test_relay_isolation.py::test_free_typing_only_ever_reaches_the_typists_own_patient",
        ],
    ),
    spec(
        id="B2 relay loop plain text",
        file="bot/patient_bot/therapist.py",
        anchor="async def relay_to_therapist",
        edits=[
            (
                '{update.message.text}"\n        )',
                '{update.message.text}", markdown=True\n        )',
                False,
            )
        ],
        tests=[T_RS + "::test_a_follow_up_message_in_an_open_chat_is_plain_text_too"],
    ),
    spec(
        id="B3 gate group",
        file="bot/main.py",
        edits=[("handle_followup_reply), group=-1", "handle_followup_reply), group=1", False)],
        tests=["tests/bot/test_followup_routing.py"],
    ),
    spec(
        id="B3 gate stops",
        file="bot/patient_bot/followup.py",
        edits=[("raise ApplicationHandlerStop", "return", True)],
        tests=["tests/bot/test_followup_routing.py", "tests/bot/test_followup_gate.py"],
    ),
    spec(
        id="B4 clash check (index dropped by test)",
        file="bot/patient_bot/services/appointments.py",
        edits=[("raise SlotTaken(", "print(", False)],
        tests=[
            "tests/bot/test_double_booking.py::test_the_clash_check_alone_refuses_without_the_index"
        ],
    ),
    spec(
        id="B4 unique index",
        file="bot/db.py",
        edits=[
            (
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_appointments_active_slot",
                "CREATE INDEX IF NOT EXISTS ux_appointments_active_slot",
                False,
            )
        ],
        tests=[
            "tests/bot/test_double_booking.py::test_the_database_alone_refuses_a_second_active_row"
        ],
    ),
    spec(
        id="B5 / 12.2.4 registry is the database",
        file="bot/therapists.py",
        anchor="def get_active",
        edits=[
            (
                '"SELECT * FROM therapists WHERE id = ? AND active = 1"',
                '"SELECT * FROM therapists WHERE id = ?"',
                False,
            )
        ],
        tests=[T_RS],
    ),
    spec(
        id="12.2.4 therapist id race",
        file="web/repositories/therapist_repo.py",
        anchor="def insert_new",
        edits=[
            (
                "            if get_by_id(new_id) is None:\n                raise",
                "            raise",
                False,
            )
        ],
        tests=[
            "tests/integration/test_therapist_registry.py"
            "::test_two_processes_picking_the_same_id_both_succeed"
        ],
    ),
    spec(
        id="12.2.4 periodic once per interval",
        file="zenflow/periodic.py",
        edits=[
            (
                "ttl_seconds=periodic.every_seconds, renew=False)",
                "ttl_seconds=periodic.every_seconds)",
                False,
            )
        ],
        tests=[
            "tests/unit/test_periodic.py" "::test_a_due_task_runs_once_per_interval_across_workers"
        ],
    ),
    spec(
        id="12.2.5 webhook secret per bot",
        file="bot/webhooks.py",
        edits=[
            (
                "        if not master_secret or not hmac.compare_digest(",
                "        if False and not hmac.compare_digest(",
                False,
            )
        ],
        tests=[
            "tests/integration/test_bot_webhooks.py::test_without_this_bots_secret_nothing_is_queued"
        ],
    ),
    spec(
        id="12.2.5 webhook mode needs a secret",
        file="zenflow/settings.py",
        edits=[
            (
                "        if self.flags.webhook_mode:\n            self._check_webhook_mode()",
                "",
                False,
            )
        ],
        tests=[
            "tests/unit/test_settings.py::test_webhook_mode_refuses_to_start_without_a_secret_and_https"
        ],
    ),
    spec(
        id="12.2.6 the AWS task env boots prod",
        file="infra/terraform/app.tf",
        edits=[("5432/zenflow?sslmode=require", "5432/zenflow", False)],
        tests=["tests/unit/test_infra.py::test_the_task_environment_boots_the_app_in_prod_mode"],
    ),
    spec(
        id="12.2.6 one bots replica",
        file="infra/terraform/app.tf",
        anchor='resource "aws_ecs_service" "bots"',
        edits=[
            (
                "  desired_count                      = 1",
                "  desired_count                      = 2",
                False,
            )
        ],
        tests=[
            "tests/unit/test_infra.py::test_one_bots_replica_stopped_before_its_replacement_starts"
        ],
    ),
    spec(
        id="B6 /start says so",
        file="bot/patient_bot/start.py",
        edits=[
            (
                "dropped = clear_in_flight(context)",
                "dropped = clear_in_flight(context) and False",
                False,
            )
        ],
        tests=[T_ROB + "::test_start_drops_an_unfinished_booking_and_says_so"],
    ),
    spec(
        id="B7 media wiring",
        file="bot/main.py",
        edits=[
            (
                "MessageHandler(~filters.TEXT & ~filters.COMMAND, relay_unsupported_media),",
                "",
                False,
            )
        ],
        tests=[T_ROB + "::test_media_handlers_are_wired_into_both_bots"],
    ),
    spec(
        id="B8 error handler",
        file="bot/main.py",
        edits=[("app.add_error_handler(on_error)", "pass", False)],
        tests=[T_ROB + "::test_the_error_handler_is_installed_on_both_bots"],
    ),
    spec(
        id="B10 timeout",
        file="bot/main.py",
        edits=[("conversation_timeout=(_timeout_minutes() * 60) or None,", "", False)],
        tests=["tests/bot/test_timeout_and_lifecycle.py::test_the_conversation_expires"],
    ),
    spec(
        id="B11 stale buttons",
        file="bot/main.py",
        edits=[("CallbackQueryHandler(stale_button),", "", True)],
        tests=[
            T_ROB + "::test_a_stale_button_is_answered_and_the_menu_comes_back",
            T_ROB + "::test_stale_callbacks_are_reachable_from_outside_a_conversation",
        ],
    ),
    spec(
        id="B12 no reroute",
        file="bot/patient_bot/therapist.py",
        edits=[
            (
                "return active[0] if len(active) == 1 else None",
                "return active[0] if active else None",
                False,
            )
        ],
        tests=["tests/bot/test_therapist_contact_flow.py", T_RS],
    ),
    spec(
        id="B13 queued pipeline",
        file="bot/patient_bot/schedule.py",
        edits=[
            (
                "start_intake_pipeline(appointment_id, user_id)",
                "asyncio.ensure_future(asyncio.sleep(0))",
                False,
            )
        ],
        tests=[
            "tests/integration/test_generation_pipeline.py::test_the_handler_no_longer_fires_and_forgets"
        ],
    ),
    # ── follow-up & delivery ──
    spec(
        id="F1 send_email therapist_id",
        file="bot/services/followup_scheduler.py",
        anchor="email_service.send_email,",
        edits=[("therapist_id,", "", False)],
        tests=[
            "tests/integration/test_followup_jobs.py::test_manual_patient_email_fallback_passes_the_therapist_id_f1"
        ],
    ),
    spec(
        id="F2 completed_at UTC (endpoint)",
        file="web/routers/api/treatment.py",
        edits=[
            (
                'notes["completed_at"] = clock.iso_now()',
                'notes["completed_at"] = __import__("datetime").datetime.now().isoformat()',
                False,
            )
        ],
        tests=[
            "tests/integration/test_followup_window.py::test_step1_goes_out_at_24h_in_every_zone"
        ],
    ),
    spec(
        id="F2 completed_at UTC (service)",
        file="web/services/treatment_service.py",
        edits=[
            (
                '{"completed_at": clock.iso_now()}',
                '{"completed_at": __import__("datetime").datetime.now().isoformat()}',
                False,
            )
        ],
        tests=["tests/integration/test_treatment_service.py"],
    ),
    spec(
        id="R3 re-completion",
        file="bot/services/followup_jobs.py",
        edits=[
            (
                'return f"followup:{int(appointment_id)}:{completed_at}"',
                'return f"followup:{int(appointment_id)}"',
                False,
            )
        ],
        tests=[T_FJR + "::test_second_completion_reschedules_the_followup"],
    ),
    spec(
        id="R3 stamp repair",
        file="bot/services/followup_scheduler.py",
        edits=[("if redis_sent:", "if False:", False)],
        tests=[T_FJR + "::test_db_stamp_failure_after_send_is_repaired_without_resending"],
    ),
    spec(
        id="R3 Send Now clears queue",
        file="web/routers/api/treatment.py",
        edits=[("await asyncio.to_thread(clear_pending_recommendations, apt_id)", "pass", False)],
        tests=[T_FJR + "::test_send_now_clears_the_auto_queued_recommendations"],
    ),
    spec(
        id="R3 dead-letter alert",
        file="bot/services/followup_jobs.py",
        edits=[("@default_registry.on_dead(RECOMMENDATIONS_JOB)", "", False)],
        tests=[T_FJR + "::test_recommendation_timeout_on_final_attempt_alerts_once"],
    ),
    spec(
        id="R3 reconcile window",
        file="bot/services/followup_scheduler.py",
        edits=[("clock.hours_ago(fj.FOLLOWUP_EXPIRE_HOURS)", "clock.hours_ago(26)", False)],
        tests=[T_FJR + "::test_reconcile_covers_the_expiry_window_and_skips_bad_rows"],
    ),
    spec(
        id="7.4b label fallback",
        file="bot/interfaces/whatsapp_channel.py",
        edits=[
            (
                "too_long = any(len(label) > MAX_BUTTON_TITLE for label, _d in flat)",
                "too_long = False",
                False,
            )
        ],
        tests=[
            "tests/contract/test_whatsapp_channel.py::test_a_label_wider_than_whatsapp_allows_falls_back_to_text"
        ],
    ),
    spec(
        id="7.4b window on receipt",
        file="web/routers/api/whatsapp.py",
        edits=[
            (
                'await remember_inbound(message.external_user_id, message.message_id or "")',
                'await remember_inbound(message.external_user_id, message.message_id or "", at=message.received_at)',
                False,
            )
        ],
        tests=[
            "tests/integration/test_whatsapp_webhook.py::test_an_inbound_message_opens_the_service_window"
        ],
    ),
    spec(
        id="8.2 ai actor",
        file="bot/services/pipeline_jobs.py",
        edits=[(', audit.acting_as("ai", _model_name()):', ":", False)],
        tests=[
            "tests/integration/test_ai_calls.py::test_a_generation_is_the_ais_work_on_one_appointment"
        ],
    ),
    spec(
        id="11.1 empty name",
        file="web/services/appointment_service.py",
        edits=[
            (
                'apt.get("patient_name") or f"Patient {pid}"',
                'apt.get("patient_name", f"Patient {pid}")',
                False,
            )
        ],
        tests=[
            "tests/unit/test_appointment_aggregation.py::test_name_falls_back_to_patient_id_label"
        ],
    ),
    spec(
        id="11.1 RecursionError",
        file="bot/patient_bot/services/ai_intake.py",
        edits=[
            (
                "except (json.JSONDecodeError, RecursionError):",
                "except json.JSONDecodeError:",
                False,
            )
        ],
        tests=[
            "tests/unit/test_ai_parser_fuzz.py::test_deeply_nested_json_does_not_crash_either_parser"
        ],
    ),
    spec(
        id="11.1 diagnosis dict",
        file="web/routers/api/treatment.py",
        edits=[("if isinstance(obj, dict):", "if True:", True)],
        tests=[
            "tests/unit/test_ai_parser_fuzz.py::test_diagnosis_parser_never_raises_and_bounds_through_the_pipeline"
        ],
    ),
    spec(
        id="Bot Google token from DB",
        file="bot/patient_bot/services/availability.py",
        edits=[("creds = gcal.load_credentials(therapist_id)", "return None", False)],
        tests=["tests/integration/test_bot_google_calendar_token.py"],
    ),
    # ── infra / clock / queue ──
    spec(
        id="R0 .env off in tests",
        file="zenflow/settings.py",
        edits=[('if raw.strip().lower() in ("", "0", "false", "no", "off"):', "if False:", False)],
        tests=[T_REV],
    ),
    spec(
        id="R0 db path from .env",
        file="bot/db.py",
        edits=[
            (
                'os.environ.get("ZENFLOW_DB_PATH") or _settings_db_path()',
                'os.environ.get("ZENFLOW_DB_PATH")',
                False,
            )
        ],
        tests=[
            "tests/unit/test_db.py::test_the_db_path_comes_from_dotenv_settings_when_the_env_var_is_absent"
        ],
    ),
    spec(
        id="R1 clinic date",
        file="zenflow/clock.py",
        edits=[
            (
                "parse_iso(str(value)).astimezone(clinic_tz()).strftime(fmt)",
                "parse_iso(str(value)).strftime(fmt)",
                False,
            )
        ],
        tests=[T_CLK + "::test_format_clinic_gives_the_clinic_local_date"],
    ),
    spec(
        id="R1 date-only left alone",
        file="zenflow/clock.py",
        edits=[("if DATE_ONLY_RE.match(s):", "if False:", False)],
        tests=[T_CLK + "::test_date_only_values_are_classified_as_date_and_left_alone"],
    ),
    spec(
        id="R1 notification time Z",
        file="web/templates/base.html",
        edits=[("? norm : norm + 'Z')", ".x ? norm : norm + 'Z')", False)],
        tests=["tests/unit/test_notification_time.py"],
    ),
    spec(
        id="R2 job owner clause",
        file="zenflow/queue.py",
        edits=[
            ("return \"status='running'\", ()", 'return "1=1", ()', False),
            (
                "return \"status='running' AND locked_by=?\", (worker_id,)",
                'return "1=1", ()',
                False,
            ),
        ],
        tests=[T_QR + "::test_complete_cannot_resurrect_a_cancelled_job"],
    ),
    spec(
        id="R2 retry budget",
        file="zenflow/queue.py",
        edits=[
            (
                "if dead or attempts >= max_attempts:",
                "if dead or (retry_at is None and attempts >= max_attempts):",
                False,
            )
        ],
        tests=[T_QR + "::test_retry_at_does_not_bypass_the_attempt_budget"],
    ),
    spec(
        id="R2 empty idempotency key",
        file="zenflow/queue.py",
        edits=[
            (
                "idempotency_key = idempotency_key or None",
                "idempotency_key = idempotency_key",
                False,
            )
        ],
        tests=[T_QR + "::test_empty_idempotency_key_is_treated_as_none"],
    ),
    spec(
        id="R2 worker schema",
        file="zenflow/worker.py",
        anchor="def main",
        edits=[("dbmod.init_db()", "pass", False)],
        tests=[T_QR + "::test_standalone_worker_main_initialises_the_schema"],
    ),
    spec(
        id="R2 timeout vs lock",
        file="zenflow/worker.py",
        edits=[("if handler_timeout >= LOCK_TIMEOUT_SECONDS:", "if False:", False)],
        tests=[T_QR + "::test_worker_refuses_a_timeout_longer_than_the_lock"],
    ),
    spec(
        id="R2 cancel releases",
        file="zenflow/worker.py",
        edits=[("self.queue.release(job.id, worker_id=self.worker_id)", "pass", False)],
        tests=[T_QR + "::test_cancelled_worker_releases_its_job_immediately"],
    ),
    spec(
        id="4.2a Hebrew colours",
        file="web/static/js/treatment/point-info.js",
        edits=[
            (
                "return getPointInfo(code).channel_en || '';",
                "return getPointInfo(code).channel || '';",
                False,
            )
        ],
        tests=[
            "tests/integration/test_point_cards.py::test_hebrew_cards_keep_their_channel_colours"
        ],
    ),
    spec(
        id="4.2a KI3 alias",
        file="web/static/js/treatment/point-info.js",
        edits=[("return POINT_ALIASES[c] || c;", "return c;", False)],
        tests=[
            "tests/integration/test_point_cards.py::test_the_who_kidney_code_finds_the_reference_data"
        ],
    ),
    spec(
        id="F8 .rdb ignored",
        file=".gitignore",
        edits=[("*.rdb", "*.nothing", True)],
        tests=["tests/unit/test_repo_hygiene.py::test_gitignore_contains_required_rules"],
    ),
    spec(
        id="9.9 erasure deletes channels",
        file="zenflow/patient_erasure.py",
        edits=[
            (
                '"message_log", "notifications", "patient_channels"):',
                '"message_log", "notifications"):',
                False,
            )
        ],
        tests=["tests/integration/test_patient_erasure.py"],
    ),
    spec(
        id="12.2.2 Postgres over TLS",
        file="zenflow/settings.py",
        edits=[('and "sslmode=require" not in db.query', "and False", False)],
        tests=[
            "tests/security/test_transit.py::test_prod_refuses_a_non_local_postgres_without_tls"
        ],
    ),
    spec(
        id="9.9 backup encryption",
        file="zenflow/db_backup.py",
        edits=[("token = fernet.encrypt(mem.serialize())", "token = mem.serialize()", False)],
        tests=["tests/integration/test_encrypted_backups.py"],
    ),
]
