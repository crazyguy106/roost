"""Integration tests for AI CDR / WhatsApp / WeChat / scheduler / MCP bridge.

Covers gaps left by tests/test_recipes.py:
  * WhatsApp HMAC signature verification
  * WhatsApp HTTP send paths (mocked via respx)
  * WeChat token cache + refresh
  * WeChat HTTP send paths
  * WhatsApp + WeChat webhook FastAPI endpoints (TestClient)
  * Recipe execution end-to-end (with mocked classify_message)
  * Scheduler cron parsing + dedup
  * MCP execute_recipe / classify_inbound_message bridges
  * GeminiAgent constructor + tool tier edge cases
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as _time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx


# ── 1. WhatsApp signature verification ────────────────────────────


class TestWhatsAppSignature:
    """verify_webhook_signature: HMAC-SHA256 against WHATSAPP_APP_SECRET."""

    def _patch_secret(self, secret: str):
        import roost.extras.messaging_external.services.whatsapp as wa
        wa.WHATSAPP_APP_SECRET = secret

    def test_valid_signature(self):
        self._patch_secret("test_app_secret_123")
        from roost.extras.messaging_external.services.whatsapp import verify_webhook_signature

        body = b'{"object":"whatsapp_business_account","entry":[]}'
        digest = hmac.new(b"test_app_secret_123", body, hashlib.sha256).hexdigest()
        signature = f"sha256={digest}"

        assert verify_webhook_signature(body, signature) is True

    def test_wrong_secret_rejected(self):
        self._patch_secret("real_secret")
        from roost.extras.messaging_external.services.whatsapp import verify_webhook_signature

        body = b'{"object":"whatsapp_business_account"}'
        # Compute with the wrong secret
        bad_digest = hmac.new(b"attacker_secret", body, hashlib.sha256).hexdigest()
        bad_signature = f"sha256={bad_digest}"

        assert verify_webhook_signature(body, bad_signature) is False

    def test_malformed_signature_rejected(self):
        self._patch_secret("real_secret")
        from roost.extras.messaging_external.services.whatsapp import verify_webhook_signature

        body = b'{"foo":"bar"}'
        # Missing the sha256= prefix
        assert verify_webhook_signature(body, "deadbeef") is False
        # Empty
        assert verify_webhook_signature(body, "") is False

    def test_no_secret_configured_rejects(self):
        self._patch_secret("")
        from roost.extras.messaging_external.services.whatsapp import verify_webhook_signature

        body = b'{}'
        assert verify_webhook_signature(body, "sha256=anything") is False


# ── 2. WhatsApp HTTP client (mocked with respx) ───────────────────


class TestWhatsAppHttpClient:
    """send_text_message / send_template_message / mark_as_read with mocked httpx."""

    def _patch_creds(self):
        import roost.extras.messaging_external.services.whatsapp as wa
        wa.WHATSAPP_ACCESS_TOKEN = "EAAtest_token"
        wa.WHATSAPP_PHONE_NUMBER_ID = "1234567890"

    @respx.mock
    def test_send_text_message_success(self):
        self._patch_creds()
        from roost.extras.messaging_external.services.whatsapp import send_text_message

        route = respx.post(
            "https://graph.facebook.com/v21.0/1234567890/messages"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"messages": [{"id": "wamid.SUCCESS123"}]},
            )
        )

        result = send_text_message("+6591234567", "Hello there!")

        assert route.called
        assert result["ok"] is True
        assert result["message_id"] == "wamid.SUCCESS123"

        # Verify the payload Meta received
        sent_payload = json.loads(route.calls.last.request.content)
        assert sent_payload["to"] == "6591234567"  # leading + stripped
        assert sent_payload["type"] == "text"
        assert sent_payload["text"]["body"] == "Hello there!"

    @respx.mock
    def test_send_template_message_success(self):
        self._patch_creds()
        from roost.extras.messaging_external.services.whatsapp import send_template_message

        respx.post(
            "https://graph.facebook.com/v21.0/1234567890/messages"
        ).mock(
            return_value=httpx.Response(
                200, json={"messages": [{"id": "wamid.TPL456"}]}
            )
        )

        result = send_template_message(
            "+6591234567",
            "hello_world",
            language_code="en",
            components=[{"type": "body", "parameters": [{"type": "text", "text": "Alice"}]}],
        )

        assert result["ok"] is True
        assert result["message_id"] == "wamid.TPL456"

    @respx.mock
    def test_mark_as_read(self):
        self._patch_creds()
        from roost.extras.messaging_external.services.whatsapp import mark_as_read

        route = respx.post(
            "https://graph.facebook.com/v21.0/1234567890/messages"
        ).mock(return_value=httpx.Response(200, json={"success": True}))

        result = mark_as_read("wamid.READ123")

        assert route.called
        assert result["ok"] is True
        sent_payload = json.loads(route.calls.last.request.content)
        assert sent_payload["status"] == "read"
        assert sent_payload["message_id"] == "wamid.READ123"

    @respx.mock
    def test_send_text_handles_api_error(self):
        self._patch_creds()
        from roost.extras.messaging_external.services.whatsapp import send_text_message

        respx.post(
            "https://graph.facebook.com/v21.0/1234567890/messages"
        ).mock(
            return_value=httpx.Response(
                400,
                json={"error": {"message": "Invalid recipient", "code": 100}},
            )
        )

        result = send_text_message("+0000000", "test")
        assert "error" in result
        assert "400" in result["error"]
        assert result["details"]["error"]["code"] == 100

    def test_send_text_unconfigured(self):
        import roost.extras.messaging_external.services.whatsapp as wa
        wa.WHATSAPP_ACCESS_TOKEN = ""
        wa.WHATSAPP_PHONE_NUMBER_ID = ""
        from roost.extras.messaging_external.services.whatsapp import send_text_message

        result = send_text_message("+6591234567", "Hi")
        assert "error" in result
        assert "not configured" in result["error"]


# ── 3. WeChat HTTP client (mocked with respx) ─────────────────────


class TestWeChatHttpClient:
    """_get_access_token cache logic + send_text_message with mocked httpx."""

    def _patch_creds(self):
        import roost.extras.messaging_external.services.wechat as wc
        wc.WECHAT_APP_ID = "wxtest_appid"
        wc.WECHAT_APP_SECRET = "wxtest_secret"
        # Reset token cache between tests
        wc._token_cache["token"] = ""
        wc._token_cache["expires_at"] = 0

    @respx.mock
    def test_token_fetch_caches_response(self):
        self._patch_creds()
        from roost.extras.messaging_external.services.wechat import _get_access_token
        import roost.extras.messaging_external.services.wechat as wc

        route = respx.get(
            "https://api.weixin.qq.com/cgi-bin/token"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "wx_token_xyz", "expires_in": 7200},
            )
        )

        token = _get_access_token()
        assert token == "wx_token_xyz"
        assert wc._token_cache["token"] == "wx_token_xyz"
        assert wc._token_cache["expires_at"] > _time.time() + 7000
        assert route.call_count == 1

    @respx.mock
    def test_token_cache_hit_skips_http(self):
        self._patch_creds()
        from roost.extras.messaging_external.services.wechat import _get_access_token
        import roost.extras.messaging_external.services.wechat as wc

        # Pre-populate cache with a long-lived token
        wc._token_cache["token"] = "cached_token"
        wc._token_cache["expires_at"] = _time.time() + 3600  # 1h from now

        route = respx.get(
            "https://api.weixin.qq.com/cgi-bin/token"
        ).mock(
            return_value=httpx.Response(200, json={"access_token": "fresh"})
        )

        token = _get_access_token()
        assert token == "cached_token"
        assert route.call_count == 0  # No HTTP call

    @respx.mock
    def test_token_refresh_near_expiry(self):
        """If token expires in <5min, refresh."""
        self._patch_creds()
        from roost.extras.messaging_external.services.wechat import _get_access_token
        import roost.extras.messaging_external.services.wechat as wc

        # Token expiring in 60 seconds — should trigger refresh
        wc._token_cache["token"] = "stale_token"
        wc._token_cache["expires_at"] = _time.time() + 60

        route = respx.get(
            "https://api.weixin.qq.com/cgi-bin/token"
        ).mock(
            return_value=httpx.Response(
                200, json={"access_token": "fresh_token", "expires_in": 7200}
            )
        )

        token = _get_access_token()
        assert token == "fresh_token"
        assert route.call_count == 1

    @respx.mock
    def test_send_text_message_success(self):
        self._patch_creds()
        import roost.extras.messaging_external.services.wechat as wc
        # Pre-cache a token to skip the token fetch
        wc._token_cache["token"] = "valid_token"
        wc._token_cache["expires_at"] = _time.time() + 3600

        route = respx.post(
            "https://api.weixin.qq.com/cgi-bin/message/custom/send"
        ).mock(
            return_value=httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})
        )

        from roost.extras.messaging_external.services.wechat import send_text_message
        result = send_text_message("oUser_test123", "Hello!")

        assert result["ok"] is True
        assert route.called
        sent_payload = json.loads(route.calls.last.request.content)
        assert sent_payload["touser"] == "oUser_test123"
        assert sent_payload["msgtype"] == "text"
        assert sent_payload["text"]["content"] == "Hello!"


# ── 4. WhatsApp webhook endpoint (FastAPI TestClient) ─────────────


class TestWhatsAppWebhookEndpoint:
    """GET verify + POST inbound on /api/whatsapp/webhook."""

    def _make_app(self):
        # Patch flags before importing the app/router
        import roost.config as cfg
        cfg.WHATSAPP_ENABLED = True
        cfg.WHATSAPP_VERIFY_TOKEN = "verify_secret_123"
        cfg.WHATSAPP_APP_SECRET = "app_secret_xyz"

        # Patch the names imported at module load time
        import roost.extras.messaging_external.web.api_whatsapp as api_wa
        api_wa.WHATSAPP_ENABLED = True
        api_wa.WHATSAPP_VERIFY_TOKEN = "verify_secret_123"

        import roost.extras.messaging_external.services.whatsapp as wa
        wa.WHATSAPP_APP_SECRET = "app_secret_xyz"

        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(api_wa.router)
        return app

    def test_get_verify_challenge_ok(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._make_app())

        resp = client.get(
            "/api/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify_secret_123",
                "hub.challenge": "1234567",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == 1234567

    def test_get_verify_wrong_token_403(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._make_app())

        resp = client.get(
            "/api/whatsapp/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "WRONG_TOKEN",
                "hub.challenge": "1234567",
            },
        )
        assert resp.status_code == 403

    def test_post_signature_reject(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._make_app())

        body = b'{"object":"whatsapp_business_account","entry":[]}'
        resp = client.post(
            "/api/whatsapp/webhook",
            content=body,
            headers={"X-Hub-Signature-256": "sha256=deadbeef"},
        )
        assert resp.status_code == 403

    def test_post_happy_path_no_messages(self):
        """Valid signature, no messages — should return 200 ok."""
        app = self._make_app()
        from fastapi.testclient import TestClient
        client = TestClient(app)

        body = json.dumps(
            {"object": "whatsapp_business_account", "entry": []}
        ).encode("utf-8")
        digest = hmac.new(b"app_secret_xyz", body, hashlib.sha256).hexdigest()

        resp = client.post(
            "/api/whatsapp/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": f"sha256={digest}",
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["processed"] == 0


# ── 5. WeChat webhook endpoint (FastAPI TestClient) ───────────────


class TestWeChatWebhookEndpoint:
    """GET echostr + POST text on /api/wechat/webhook."""

    def _make_app(self):
        import roost.config as cfg
        cfg.WECHAT_ENABLED = True
        cfg.WECHAT_TOKEN = "wechat_test_token"

        import roost.extras.messaging_external.web.api_wechat as api_wc
        api_wc.WECHAT_ENABLED = True

        import roost.extras.messaging_external.services.wechat as wc
        wc.WECHAT_TOKEN = "wechat_test_token"

        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(api_wc.router)
        return app

    def _wechat_signature(self, token: str, timestamp: str, nonce: str) -> str:
        values = sorted([token, timestamp, nonce])
        return hashlib.sha1("".join(values).encode()).hexdigest()

    def test_get_echostr_verification(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._make_app())

        timestamp = "1700000000"
        nonce = "abcd1234"
        echostr = "challenge_payload_xyz"
        signature = self._wechat_signature("wechat_test_token", timestamp, nonce)

        resp = client.get(
            "/api/wechat/webhook",
            params={
                "signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
                "echostr": echostr,
            },
        )
        assert resp.status_code == 200
        assert resp.text == echostr

    def test_post_signature_reject(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._make_app())

        resp = client.post(
            "/api/wechat/webhook",
            params={
                "signature": "wrong_signature",
                "timestamp": "1700000000",
                "nonce": "nonce123",
            },
            content=b"<xml></xml>",
        )
        assert resp.status_code == 403

    def test_post_text_returns_success(self):
        """Valid signature, text msg, no recipe → returns 'success' immediately."""
        app = self._make_app()
        from fastapi.testclient import TestClient
        client = TestClient(app)

        timestamp = "1700000000"
        nonce = "n123"
        signature = self._wechat_signature("wechat_test_token", timestamp, nonce)

        xml_body = (
            b"<xml>"
            b"<ToUserName><![CDATA[gh_official]]></ToUserName>"
            b"<FromUserName><![CDATA[oUser_xyz]]></FromUserName>"
            b"<CreateTime>1700000000</CreateTime>"
            b"<MsgType><![CDATA[text]]></MsgType>"
            b"<Content><![CDATA[I want to know about insurance]]></Content>"
            b"<MsgId>9999</MsgId>"
            b"</xml>"
        )

        resp = client.post(
            "/api/wechat/webhook",
            params={
                "signature": signature,
                "timestamp": timestamp,
                "nonce": nonce,
            },
            content=xml_body,
        )
        # Per WeChat protocol, must reply within 5s — we reply "success"
        assert resp.status_code == 200
        assert resp.text == "success"


# ── 6. Recipe execution end-to-end ────────────────────────────────


class TestRecipeExecutionE2E:
    """Full pipeline: inbound msg → mocked CDR → template select → run."""

    def _fake_classification(self, intent="buying_enquiry", urgency="warm"):
        return {
            "intent": intent,
            "urgency": urgency,
            "extracted_fields": {"name": "Alice", "budget": "500k"},
            "confidence": 0.9,
            "reasoning": "test classification",
        }

    @pytest.mark.asyncio
    async def test_external_write_holds_for_approval(self, db_cleanup):
        from roost.services.response_templates import create_template
        from roost.services.recipes import create_recipe, execute_recipe, list_runs

        tmpl = create_template(
            name="e2e_buyer",
            body="Hi {{name}}, your budget of {{budget}} is noted.",
            category="qualification",
            intent_tags=["buying_enquiry"],
        )
        recipe = create_recipe(
            name="e2e_external",
            instructions="Classify WhatsApp leads",
            risk_tier="external_write",
            template_ids=[tmpl["id"]],
        )

        with patch(
            "roost.extras.messaging_external.services.ai_cdr.classify_message",
            new=AsyncMock(return_value=self._fake_classification()),
        ):
            result = await execute_recipe(
                recipe_id=recipe["id"],
                message="I want to buy",
                sender="Alice",
            )

        assert result["status"] == "awaiting_approval"
        assert result["risk_tier"] == "external_write"
        assert "Alice" in result["draft"]
        assert "500k" in result["draft"]

        runs = list_runs(recipe_id=recipe["id"])
        assert runs[0]["status"] == "awaiting_approval"

    @pytest.mark.asyncio
    async def test_internal_write_auto_completes(self, db_cleanup):
        from roost.services.response_templates import create_template, get_template
        from roost.services.recipes import create_recipe, execute_recipe

        tmpl = create_template(
            name="e2e_internal",
            body="Logged {{name}}: {{topic}}",
            category="general",
            intent_tags=["follow_up"],
        )
        recipe = create_recipe(
            name="e2e_internal_recipe",
            instructions="Auto-log follow-ups",
            risk_tier="internal_write",
            template_ids=[tmpl["id"]],
        )

        with patch(
            "roost.extras.messaging_external.services.ai_cdr.classify_message",
            new=AsyncMock(
                return_value=self._fake_classification(intent="follow_up")
            ),
        ):
            result = await execute_recipe(
                recipe_id=recipe["id"],
                message="Following up",
                sender="Alice",
            )

        assert result["status"] == "completed"
        assert result["risk_tier"] == "internal_write"

        # Template usage incremented
        fetched = get_template(tmpl["id"])
        assert fetched["usage_count"] == 1

    @pytest.mark.asyncio
    async def test_read_only_just_logs(self, db_cleanup):
        from roost.services.recipes import create_recipe, execute_recipe, get_recipe

        recipe = create_recipe(
            name="e2e_readonly",
            instructions="Read only",
            risk_tier="read_only",
        )

        with patch(
            "roost.extras.messaging_external.services.ai_cdr.classify_message",
            new=AsyncMock(return_value=self._fake_classification()),
        ):
            result = await execute_recipe(
                recipe_id=recipe["id"],
                message="hi",
                sender="Bob",
            )

        assert result["status"] == "completed"
        assert result["risk_tier"] == "read_only"

        updated = get_recipe(recipe["id"])
        assert updated["run_count"] == 1

    @pytest.mark.asyncio
    async def test_missing_recipe_returns_error(self, db_cleanup):
        from roost.services.recipes import execute_recipe

        result = await execute_recipe(
            recipe_id=999999,
            message="x",
        )
        assert "error" in result


# ── 7. Scheduler cron parsing ─────────────────────────────────────


class TestSchedulerCronParsing:
    """_run_cron_recipes time-matching, dedup, day-of-week filtering."""

    @pytest.mark.asyncio
    async def test_hhmm_match_executes(self, db_cleanup):
        from datetime import datetime
        from roost.services.recipes import create_recipe
        import roost.bot.scheduler as sched

        recipe = create_recipe(
            name="cron_match",
            instructions="Daily at 09:30",
            trigger_type="cron",
            trigger_config="09:30",
            risk_tier="read_only",
        )

        fake_now = datetime(2026, 4, 10, 9, 30, 15)
        executed = []

        async def fake_execute(**kwargs):
            executed.append(kwargs["recipe_id"])
            return {"status": "completed"}

        ctx = MagicMock()
        ctx.bot.send_message = AsyncMock()

        with patch.object(sched, "datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            with patch("roost.services.recipes.execute_recipe", new=fake_execute):
                await sched._run_cron_recipes(ctx)

        assert recipe["id"] in executed

    @pytest.mark.asyncio
    async def test_no_match_skipped(self, db_cleanup):
        from datetime import datetime
        from roost.services.recipes import create_recipe
        import roost.bot.scheduler as sched

        recipe = create_recipe(
            name="cron_nomatch",
            instructions="Daily at 14:00",
            trigger_type="cron",
            trigger_config="14:00",
            risk_tier="read_only",
        )

        fake_now = datetime(2026, 4, 10, 9, 30, 15)
        executed = []

        async def fake_execute(**kwargs):
            executed.append(kwargs["recipe_id"])
            return {"status": "completed"}

        ctx = MagicMock()
        ctx.bot.send_message = AsyncMock()

        with patch.object(sched, "datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            with patch("roost.services.recipes.execute_recipe", new=fake_execute):
                await sched._run_cron_recipes(ctx)

        assert recipe["id"] not in executed

    @pytest.mark.asyncio
    async def test_weekdays_filter(self, db_cleanup):
        """09:00:weekdays runs Mon-Fri but skips weekends."""
        from datetime import datetime
        from roost.services.recipes import create_recipe
        import roost.bot.scheduler as sched

        recipe = create_recipe(
            name="cron_weekdays",
            instructions="Weekday 09:00",
            trigger_type="cron",
            trigger_config="09:00:weekdays",
            risk_tier="read_only",
        )

        # 2026-04-11 is a Saturday
        sat = datetime(2026, 4, 11, 9, 0, 0)
        executed = []

        async def fake_execute(**kwargs):
            executed.append(kwargs["recipe_id"])
            return {"status": "completed"}

        ctx = MagicMock()
        ctx.bot.send_message = AsyncMock()

        with patch.object(sched, "datetime") as mock_dt:
            mock_dt.now.return_value = sat
            with patch("roost.services.recipes.execute_recipe", new=fake_execute):
                await sched._run_cron_recipes(ctx)

        assert recipe["id"] not in executed  # Saturday excluded

        # Now try a Friday (2026-04-10)
        fri = datetime(2026, 4, 10, 9, 0, 0)
        executed.clear()
        with patch.object(sched, "datetime") as mock_dt:
            mock_dt.now.return_value = fri
            with patch("roost.services.recipes.execute_recipe", new=fake_execute):
                await sched._run_cron_recipes(ctx)

        assert recipe["id"] in executed

    @pytest.mark.asyncio
    async def test_specific_days_filter(self, db_cleanup):
        """09:00:0,2,4 runs Mon, Wed, Fri only."""
        from datetime import datetime
        from roost.services.recipes import create_recipe
        import roost.bot.scheduler as sched

        recipe = create_recipe(
            name="cron_mwf",
            instructions="MWF 09:00",
            trigger_type="cron",
            trigger_config="09:00:0,2,4",
            risk_tier="read_only",
        )

        ctx = MagicMock()
        ctx.bot.send_message = AsyncMock()

        async def collect_run(now):
            executed = []

            async def fake_execute(**kwargs):
                executed.append(kwargs["recipe_id"])
                return {"status": "completed"}

            with patch.object(sched, "datetime") as mock_dt:
                mock_dt.now.return_value = now
                with patch("roost.services.recipes.execute_recipe", new=fake_execute):
                    await sched._run_cron_recipes(ctx)
            return executed

        # Tuesday (2026-04-07) — should NOT run
        tue = datetime(2026, 4, 7, 9, 0, 0)
        assert recipe["id"] not in await collect_run(tue)

        # Wednesday (2026-04-08) — SHOULD run
        wed = datetime(2026, 4, 8, 9, 0, 0)
        assert recipe["id"] in await collect_run(wed)

    @pytest.mark.asyncio
    async def test_dedup_within_same_minute(self, db_cleanup):
        """If last_run is the same minute, don't re-execute."""
        from datetime import datetime
        from roost.services.recipes import create_recipe, update_recipe
        import roost.bot.scheduler as sched

        recipe = create_recipe(
            name="cron_dedup",
            instructions="Daily 10:00",
            trigger_type="cron",
            trigger_config="10:00",
            risk_tier="read_only",
        )

        # Mark recipe as already run at the exact target minute
        update_recipe(recipe["id"], last_run="2026-04-10T10:00:30Z")

        fake_now = datetime(2026, 4, 10, 10, 0, 45)
        executed = []

        async def fake_execute(**kwargs):
            executed.append(kwargs["recipe_id"])
            return {"status": "completed"}

        ctx = MagicMock()
        ctx.bot.send_message = AsyncMock()

        with patch.object(sched, "datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            with patch("roost.services.recipes.execute_recipe", new=fake_execute):
                await sched._run_cron_recipes(ctx)

        assert recipe["id"] not in executed  # Skipped due to dedup


# ── 8. MCP tool bridge ────────────────────────────────────────────


class TestMcpToolBridge:
    """MCP tools bridge sync ↔ async correctly."""

    def test_classify_inbound_message_sync_bridge(self):
        """classify_inbound_message MCP tool works from sync context."""
        from roost.mcp import tools_recipes

        with patch(
            "roost.extras.messaging_external.services.ai_cdr.classify_message",
            new=AsyncMock(
                return_value={
                    "intent": "buying_enquiry",
                    "urgency": "warm",
                    "extracted_fields": {"name": "Test"},
                    "confidence": 0.8,
                    "reasoning": "test",
                }
            ),
        ):
            # The tool is decorated, so call the underlying function via .fn
            tool = tools_recipes.classify_inbound_message
            fn = getattr(tool, "fn", tool)  # FastMCP exposes original as .fn
            result = fn(message="I want to buy a flat", sender="Alice")

        assert result["intent"] == "buying_enquiry"
        assert result["urgency"] == "warm"

    def test_execute_recipe_sync_bridge(self, db_cleanup):
        """execute_recipe MCP tool runs the async pipeline from sync context."""
        from roost.services.recipes import create_recipe
        from roost.mcp import tools_recipes

        recipe = create_recipe(
            name="mcp_bridge_test",
            instructions="Test bridge",
            risk_tier="read_only",
        )

        with patch(
            "roost.extras.messaging_external.services.ai_cdr.classify_message",
            new=AsyncMock(
                return_value={
                    "intent": "general",
                    "urgency": "cold",
                    "extracted_fields": {},
                    "confidence": 0.5,
                    "reasoning": "test",
                }
            ),
        ):
            tool = tools_recipes.execute_recipe
            fn = getattr(tool, "fn", tool)
            result = fn(recipe_id=recipe["id"], message="hi", sender="Bob")

        assert "error" not in result
        assert result["status"] == "completed"
        assert result["risk_tier"] == "read_only"


# ── 9. GeminiAgent constructor + tier edge cases ──────────────────


class TestGeminiAgentClass:
    """GeminiAgent constructor honors tool_scope; edge cases for _execute_tool."""

    def test_init_tier_none_disables_tools(self):
        # GeminiAgent.__init__ instantiates genai.Client(api_key=...) which
        # validates the key. Patch at the source module.
        with patch("roost.gemini_agent.genai.Client", return_value=MagicMock()):
            from roost.gemini_agent import GeminiAgent, TIER_NONE
            agent = GeminiAgent(tool_scope=TIER_NONE)
        assert agent.tool_scope == TIER_NONE
        assert agent.tools is None  # No tools registered with the model

    def test_init_tier_read_only_stores_scope(self):
        with patch("roost.gemini_agent.genai.Client", return_value=MagicMock()):
            from roost.gemini_agent import GeminiAgent, TIER_READ_ONLY
            agent = GeminiAgent(tool_scope=TIER_READ_ONLY)
        assert agent.tool_scope == TIER_READ_ONLY
        # tools is set (not None) for non-NONE tiers
        assert agent.tools is not None

    def test_internal_write_blocks_send_email(self):
        """send_email is external — must be blocked at INTERNAL_WRITE."""
        from roost.gemini_agent import _execute_tool, TIER_INTERNAL_WRITE
        result = _execute_tool(
            "send_email",
            {"to": "a@b.com", "subject": "x", "body": "y"},
            tool_scope=TIER_INTERNAL_WRITE,
        )
        assert "error" in result
        assert "not available" in result["error"]
