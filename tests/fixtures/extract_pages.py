"""Lab 5 正文抽取测试语料:20 页本地 HTML,不打外网。"""
from __future__ import annotations

PARA = (
    "正文从这里开始。"
    "我们讨论的是信息采集系统如何把标题变成可供阅读的段落,"
    "而不是再堆一层书签。"
    "抽取器必须能认出目录、页脚和侧栏,只留下真正的叙述。"
    "这段文字重复几次,是为了超过两百字的质量门槛。"
    "港口调度、利率决议、库存周期这些具体事实,都写在段落里,而不是写在菜单上。"
) * 3

SHORT_TEASER = "登录后查看全文。这只是付费墙前的两句预览。"


def _page(title: str, body: str, extra: str = "") -> str:
    return f"""<!doctype html>
<html lang="zh">
<head><meta charset="utf-8"><title>{title}</title></head>
<body>
<nav>首页 登录 注册 下载APP 热门评论 相关推荐</nav>
<article>
<h1>{title}</h1>
<div class="content">{body}</div>
</article>
{extra}
<footer>版权所有 ICP备123456 关于我们 隐私政策</footer>
</body></html>"""


def _weixin(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><title>{title}</title></head>
<body>
<div id="activity-name">{title}</div>
<div id="js_content"><p>{body}</p></div>
<div class="share">分享到 打开APP</div>
</body></html>"""


def _zhihu(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><title>{title}</title></head>
<body>
<h1 class="Post-Title">{title}</h1>
<div class="Post-RichText"><p>{body}</p></div>
<aside>相关推荐 登录 注册</aside>
</body></html>"""


def _thepaper(title: str, body: str) -> str:
    return f"""<!doctype html><html><head><title>{title}</title></head>
<body>
<h1 class="index_title">{title}</h1>
<div class="news_txt"><p>{body}</p></div>
</body></html>"""


def _nav_only(title: str) -> str:
    return f"""<!doctype html><html><head><title>{title}</title></head>
<body>
<nav>首页 登录 注册 下载APP 相关推荐 热门评论 分享到</nav>
<ul>{"".join(f"<li>链接{i}</li>" for i in range(12))}</ul>
<footer>版权所有 关于我们</footer>
</body></html>"""


# (url, html, expect_ok)
PAGES: list[tuple[str, str, bool]] = [
    ("https://news.example.com/a1", _page("新闻一：港口恢复通航", PARA), True),
    ("https://news.example.com/a2", _page("新闻二：利率决议解读", PARA), True),
    ("https://www.thepaper.cn/newsDetail_forward_1", _thepaper("澎湃样例：地方债观察", PARA), True),
    ("https://www.thepaper.cn/newsDetail_forward_2", _thepaper("澎湃样例：产业转移", PARA), True),
    ("https://mp.weixin.qq.com/s/aaa111", _weixin("公众号：读一篇论文的方法", PARA), True),
    ("https://mp.weixin.qq.com/s/bbb222", _weixin("公众号：本周只做三件事", PARA), True),
    ("https://zhuanlan.zhihu.com/p/1001", _zhihu("知乎专栏：向量检索笔记", PARA), True),
    ("https://zhuanlan.zhihu.com/p/1002", _zhihu("知乎专栏：评一份研报", PARA), True),
    ("https://finance.example.com/mkt", _page("财经：北向资金与汇率", PARA), True),
    ("https://finance.example.com/macro", _page("财经：CPI 与基数效应", PARA), True),
    ("https://blog.example.com/sys", _page("博客：WAL 模式为什么能救命", PARA), True),
    ("https://blog.example.com/rss", _page("博客：订阅胜过搜索", PARA), True),
    ("https://en.example.org/wiki/cache", _page("Why HTTP caches exist", PARA), True),
    ("https://en.example.org/wiki/robots", _page("Robots.txt is a hint, not a law", PARA), True),
    ("https://media.example.com/long", _page("深度：产业链上的库存周期", PARA * 2), True),
    ("https://media.example.com/interview", _page("访谈：一个人的报纸系统", PARA), True),
    # 应降级
    ("https://paywall.example.com/pro", _page("会员文", SHORT_TEASER), False),
    ("https://portal.example.com/home", _nav_only("门户首页"), False),
    ("https://mp.weixin.qq.com/s/imgonly", _weixin("纯图片推文", "打开APP 查看"), False),
    ("https://zhihu.com/p/empty", _zhihu("空文", ""), False),
]
