from payradar.ptt import parse_board

HTML = """
<div class="r-ent"><div class="nrec"><span class="hl f3">34</span></div>
<div class="title"><a href="/bbs/MobilePay/M.1.A.html">[情報] 全支付 地價稅開繳</a></div>
<div class="meta"><div class="date">10/29</div></div></div>
<div class="r-ent"><div class="nrec"></div><div class="title">(本文已被刪除) [x]</div></div>
<div class="r-ent"><div class="nrec">5</div>
<div class="title"><a href="/bbs/MobilePay/M.2.A.html">[閒聊] 今天天氣</a></div></div>
"""


def test_parse_board_keeps_relevant_posts_only() -> None:
    posts = parse_board(HTML, "MobilePay")
    assert [p["title"] for p in posts] == ["[情報] 全支付 地價稅開繳"]
    assert posts[0]["url"] == "https://www.ptt.cc/bbs/MobilePay/M.1.A.html"
    assert posts[0]["push"] == "34" and posts[0]["date"] == "10/29"
