import os
import ssl
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

# 添加项目根目录到 PATH
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from utils.notify import NotificationKit


@pytest.fixture
def notification_kit(monkeypatch):
	monkeypatch.setenv('EMAIL_USER', 'sender@example.com')
	monkeypatch.setenv('EMAIL_PASS', 'email_password')
	monkeypatch.setenv('EMAIL_TO', 'receiver@example.com')
	monkeypatch.setenv('PUSHPLUS_TOKEN', 'pushplus_token')
	monkeypatch.setenv('SERVERPUSHKEY', 'server_push_key')
	monkeypatch.setenv('DINGDING_WEBHOOK', 'https://oapi.dingtalk.com/robot/send?access_token=test_token')
	monkeypatch.setenv('FEISHU_WEBHOOK', 'https://open.feishu.cn/open-apis/bot/v2/hook/test_token')
	monkeypatch.setenv('WEIXIN_WEBHOOK', 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test_token')
	monkeypatch.setenv('GOTIFY_URL', 'https://gotify.example.com/message')
	monkeypatch.setenv('GOTIFY_TOKEN', 'gotify_token')
	monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'telegram_token')
	monkeypatch.setenv('TELEGRAM_CHAT_ID', 'telegram_chat')
	monkeypatch.setenv('BARK_KEY', 'bark_key')
	return NotificationKit()


@pytest.fixture
def mock_httpx_client():
	with patch('httpx.Client') as mock_client_class:
		mock_response = MagicMock()
		mock_response.status_code = 200
		mock_response.json.return_value = {'code': 200}
		mock_client = MagicMock()
		mock_client.post.return_value = mock_response
		mock_client_class.return_value.__enter__.return_value = mock_client
		yield mock_client, mock_response


def test_real_notification(notification_kit):
	"""真实接口测试，需要配置.env.local文件"""
	if os.getenv('ENABLE_REAL_TEST') != 'true':
		pytest.skip('未启用真实接口测试')

	notification_kit.push_message(
		'测试消息', f'这是一条测试消息\n发送时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}'
	)


@patch('smtplib.SMTP_SSL')
def test_send_email(mock_smtp, notification_kit):
	mock_server = MagicMock()
	mock_smtp.return_value.__enter__.return_value = mock_server

	notification_kit.send_email('测试标题', '测试内容')

	assert mock_smtp.call_args.args == ('smtp.example.com', 465)
	assert mock_smtp.call_args.kwargs['timeout'] == 30
	assert mock_smtp.call_args.kwargs['context'].verify_mode == ssl.CERT_REQUIRED
	assert mock_smtp.call_args.kwargs['context'].check_hostname
	assert mock_server.login.called
	assert mock_server.send_message.called
	message = mock_server.send_message.call_args.args[0]
	assert message.get_content_type() == 'multipart/alternative'
	plain, html = message.get_payload()
	assert plain.get_content_type() == 'text/plain'
	assert plain.get_payload(decode=True).decode('utf-8') == '测试内容'
	assert html.get_content_type() == 'text/html'
	assert '测试标题' in html.get_payload(decode=True).decode('utf-8')


@patch('smtplib.SMTP_SSL')
def test_email_preserves_explicit_html_and_escapes_plain_text(mock_smtp, notification_kit):
	server = mock_smtp.return_value.__enter__.return_value
	notification_kit.send_email('Report <test>', '<script>unsafe()</script>\nNext line')
	html = server.send_message.call_args.args[0].get_payload()[1].get_payload(decode=True).decode('utf-8')
	assert '<script>' not in html and '&lt;script&gt;' in html and '<br>Next line' in html
	notification_kit.send_email('HTML', '<b>Original</b>', 'html')
	message = server.send_message.call_args.args[0]
	assert message.get_content_type() == 'text/html'
	assert message.get_payload(decode=True).decode('utf-8') == '<b>Original</b>'


def test_send_pushplus(mock_httpx_client, notification_kit):
	mock_client, _ = mock_httpx_client

	notification_kit.send_pushplus('测试标题', '测试内容')

	mock_client.post.assert_called_once_with(
		'http://www.pushplus.plus/send',
		json={'token': 'pushplus_token', 'title': '测试标题', 'content': '测试内容', 'template': 'html'},
	)


def test_send_dingtalk(mock_httpx_client, notification_kit):
	mock_client, _ = mock_httpx_client

	notification_kit.send_dingtalk('测试标题', '测试内容')

	expected_webhook = 'https://oapi.dingtalk.com/robot/send?access_token=test_token'
	expected_data = {'msgtype': 'text', 'text': {'content': '测试标题\n测试内容'}}

	mock_client.post.assert_called_once_with(expected_webhook, json=expected_data)


def test_send_feishu(mock_httpx_client, notification_kit):
	mock_client, _ = mock_httpx_client

	notification_kit.send_feishu('测试标题', '测试内容')

	mock_client.post.assert_called_once()
	args = mock_client.post.call_args[1]
	assert 'card' in args['json']


def test_send_wecom(mock_httpx_client, notification_kit):
	mock_client, _ = mock_httpx_client

	notification_kit.send_wecom('测试标题', '测试内容')

	mock_client.post.assert_called_once_with(
		'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test_token',
		json={'msgtype': 'text', 'text': {'content': '测试标题\n测试内容'}},
	)


def test_send_gotify(mock_httpx_client, notification_kit):
	mock_client, _ = mock_httpx_client

	notification_kit.send_gotify('测试标题', '测试内容')

	expected_url = 'https://gotify.example.com/message?token=gotify_token'
	expected_data = {'title': '测试标题', 'message': '测试内容', 'priority': 9}

	mock_client.post.assert_called_once_with(expected_url, json=expected_data)


def test_http_response_error(notification_kit):
	response = httpx.Response(500, text='server error')

	with patch('httpx.Client') as mock_client_class:
		mock_client = MagicMock()
		mock_client.post.return_value = response
		mock_client_class.return_value.__enter__.return_value = mock_client

		with pytest.raises(RuntimeError, match='PushPlus request failed: HTTP 500'):
			notification_kit.send_pushplus('测试', '测试')


def test_http_json_error(notification_kit):
	response = httpx.Response(200, json={'errcode': 40001, 'errmsg': 'invalid token'})

	with patch('httpx.Client') as mock_client_class:
		mock_client = MagicMock()
		mock_client.post.return_value = response
		mock_client_class.return_value.__enter__.return_value = mock_client

		with pytest.raises(RuntimeError, match='PushPlus request failed: invalid token'):
			notification_kit.send_pushplus('测试', '测试')


def test_missing_config(monkeypatch):
	monkeypatch.delenv('EMAIL_USER', raising=False)
	monkeypatch.delenv('EMAIL_PASS', raising=False)
	monkeypatch.delenv('EMAIL_TO', raising=False)
	monkeypatch.delenv('PUSHPLUS_TOKEN', raising=False)
	kit = NotificationKit()

	with pytest.raises(ValueError, match='Email configuration not set'):
		kit.send_email('测试', '测试')

	with pytest.raises(ValueError, match='PushPlus Token not configured'):
		kit.send_pushplus('测试', '测试')


def test_push_message(notification_kit, monkeypatch):
	send_methods = [
		'send_email',
		'send_pushplus',
		'send_serverPush',
		'send_dingtalk',
		'send_feishu',
		'send_wecom',
		'send_gotify',
		'send_telegram',
		'send_bark',
	]
	mocks = {}
	for method_name in send_methods:
		method_mock = MagicMock()
		mocks[method_name] = method_mock
		monkeypatch.setattr(notification_kit, method_name, method_mock)

	notification_kit.push_message('测试标题', '测试内容', email_html='<h1>邮件专用</h1>')

	for method_mock in mocks.values():
		assert method_mock.called
	mocks['send_email'].assert_called_once_with('测试标题', '测试内容', 'text', html_content='<h1>邮件专用</h1>')
	for method_name, method_mock in mocks.items():
		if method_name != 'send_email':
			method_mock.assert_called_once_with('测试标题', '测试内容')
