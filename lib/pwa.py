"""PWA（ホーム画面に追加）対応。Streamlitページの親headへ apple用メタ・アイコン・
manifest を差し込み、iOS/Androidで『ホーム画面に追加』したときにアプリ風に開けるようにする。

Streamlitは<head>を直接編集できないため、components.html(iframe)から
window.parent.document.head に差し込む。アイコンは assets/appicon.png を data URI 化。
"""
from __future__ import annotations

import base64
import json
import os

import streamlit.components.v1 as components

_ICON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "appicon.png"
)

_TPL = """
<script>
(function(){
  try {
    var doc = window.parent.document, head = doc.head;
    function meta(n,c){var m=doc.querySelector('meta[name="'+n+'"]');
      if(!m){m=doc.createElement('meta');m.setAttribute('name',n);head.appendChild(m);}
      m.setAttribute('content',c);}
    meta('apple-mobile-web-app-capable','yes');
    meta('mobile-web-app-capable','yes');
    meta('apple-mobile-web-app-status-bar-style','black-translucent');
    meta('apple-mobile-web-app-title', __TITLE__);
    meta('theme-color','#0f172a');
    function link(rel,href){var l=doc.querySelector('link[data-pwa="'+rel+'"]');
      if(!l){l=doc.createElement('link');l.setAttribute('rel',rel);l.setAttribute('data-pwa',rel);head.appendChild(l);}
      l.setAttribute('href',href);}
    var ICON = __ICON__;
    link('apple-touch-icon', ICON);
    link('icon', ICON);
    var man = {name:__TITLE__, short_name:__TITLE__, display:'standalone',
      background_color:'#0f172a', theme_color:'#0f172a',
      start_url: window.parent.location.pathname + window.parent.location.search,
      icons:[{src:ICON, sizes:'512x512', type:'image/png', purpose:'any maskable'}]};
    var blob = new Blob([JSON.stringify(man)], {type:'application/manifest+json'});
    link('manifest', URL.createObjectURL(blob));
  } catch(e) {}
})();
</script>
"""


def add_to_home_screen(title: str = "投稿チェック"):
    """ページ内で1回呼ぶ。ホーム画面追加でアプリ風に開けるようメタ/アイコン/manifestを注入。"""
    try:
        b64 = base64.b64encode(open(_ICON_PATH, "rb").read()).decode()
    except Exception:
        return
    icon = "data:image/png;base64," + b64
    html = _TPL.replace("__TITLE__", json.dumps(title)).replace("__ICON__", json.dumps(icon))
    components.html(html, height=0)
