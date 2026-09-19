"""守住 humanize 可解析的选择器语法。

cloakbrowser 的 humanize 层在 CDP 隔离世界里解析选择器，只认 CSS / text= / xpath= /
get_by_* 里除 role 外的几种。`internal:role=`（即 get_by_role）和 `>>` 链式写法会抛
UnsupportedHumanizeSelectorError，而调用点大都被 try/except 兜住，失败是静默的，
只能用测试挡住回归。
"""

from utils.browser import EMAIL_LOGIN_BUTTON_SELECTORS
from utils.popups import _CLOSE_ANNOUNCEMENT, _DISMISS_TODAY

ALL_SELECTORS = (*EMAIL_LOGIN_BUTTON_SELECTORS, _CLOSE_ANNOUNCEMENT, _DISMISS_TODAY)


def test_selectors_stay_resolvable_under_humanize():
	for selector in ALL_SELECTORS:
		assert selector, 'selector must not be empty'
		assert 'internal:' not in selector, f'get_by_role/get_by_text 等引擎 humanize 下不可用: {selector}'
		assert '>>' not in selector, f'链式选择器 humanize 下不可用: {selector}'
		assert selector.startswith('button'), f'必须限定在 button 上，否则 :has-text() 会匹配到祖先节点: {selector}'
