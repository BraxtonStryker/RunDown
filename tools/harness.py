"""v1.0 - drives RunDown in headless Chrome over the DevTools protocol (stdlib only) and checks the live-preview editor.

Usage (from the repo root):  python -I tools/harness.py [--keep] [--shots DIR]

Serves the repo on a local port, starts headless Chrome with a fresh profile, and types into the editor with real key
events (so beforeinput fires as it does for a person). Exit 0 when every check passes. --shots saves screenshots.
The websocket code is the same as QuizQrusher's test-data/harness/cdp_shot.py.
"""
import base64, functools, http.server, json, os, shutil, socket, struct, subprocess, sys, tempfile, threading, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = next((p for p in [r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                           "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"] if os.path.exists(p)), None)


def ws_connect(url):
    host_port, path = url[len("ws://"):].split("/", 1)
    host, port = host_port.split(":")
    s = socket.create_connection((host, int(port)))
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, host_port, key)).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    return s, buf.split(b"\r\n\r\n", 1)[1]


def ws_send(s, text):
    data = text.encode()
    head = bytearray([0x81])
    n = len(data)
    if n < 126:
        head.append(0x80 | n)
    elif n < 65536:
        head.append(0x80 | 126); head += struct.pack(">H", n)
    else:
        head.append(0x80 | 127); head += struct.pack(">Q", n)
    mask = os.urandom(4)
    s.sendall(bytes(head) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


def ws_recv(s, pending):
    buf = pending
    def need(k):
        nonlocal buf
        while len(buf) < k:
            chunk = s.recv(1 << 20)
            if not chunk:
                raise EOFError
            buf += chunk
    message = b""
    while True:
        need(2)
        fin, n = buf[0] & 0x80, buf[1] & 0x7F
        off = 2
        if n == 126:
            need(4); n = struct.unpack(">H", buf[2:4])[0]; off = 4
        elif n == 127:
            need(10); n = struct.unpack(">Q", buf[2:10])[0]; off = 10
        need(off + n)
        message += buf[off:off + n]
        buf = buf[off + n:]
        if fin:
            return message.decode(), buf


class Page:
    def __init__(self, ws_url):
        self.s, self.pending = ws_connect(ws_url)
        self.n = 0
        self.errors = []

    def call(self, method, **params):
        self.n += 1
        ws_send(self.s, json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg, self.pending = ws_recv(self.s, self.pending)
            m = json.loads(msg)
            if m.get("method") == "Runtime.exceptionThrown":
                self.errors.append(m["params"]["exceptionDetails"].get("exception", {}).get("description") or m["params"]["exceptionDetails"].get("text"))
            if m.get("method") == "Runtime.consoleAPICalled" and m["params"].get("type") == "error":
                self.errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in m["params"]["args"]))
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError("%s: %s" % (method, m["error"]))
                return m.get("result", {})

    def ev(self, js):
        r = self.call("Runtime.evaluate", expression=js, returnByValue=True, awaitPromise=True)
        if r.get("exceptionDetails"):
            raise RuntimeError("JS error in %r: %s" % (js[:80], r["exceptionDetails"].get("exception", {}).get("description")))
        return r["result"].get("value")

    def key(self, key, code=None, vk=0, text=None, mods=0):
        p = {"type": "keyDown" if text else "rawKeyDown", "key": key, "code": code or key, "windowsVirtualKeyCode": vk, "modifiers": mods}
        if text:
            p["text"] = text
            p["unmodifiedText"] = text
        self.call("Input.dispatchKeyEvent", **p)
        self.call("Input.dispatchKeyEvent", type="keyUp", key=key, code=code or key, windowsVirtualKeyCode=vk, modifiers=mods)
        time.sleep(0.02)

    def type(self, s):
        for ch in s:
            if ch == "\n":
                self.enter()
                continue
            self.call("Input.dispatchKeyEvent", type="keyDown", key=ch, text=ch, unmodifiedText=ch)
            self.call("Input.dispatchKeyEvent", type="keyUp", key=ch)
        time.sleep(0.03)

    def enter(self, mods=0):
        self.key("Enter", "Enter", 13, "\r", mods)

    def bs(self, n=1, mods=0):
        for _ in range(n):
            self.key("Backspace", "Backspace", 8, None, mods)

    def arrow(self, d, n=1, mods=0):
        vk = {"Left": 37, "Up": 38, "Right": 39, "Down": 40}[d]
        for _ in range(n):
            self.key("Arrow" + d, "Arrow" + d, vk, None, mods)
        time.sleep(0.05)

    def ctrl(self, ch, shift=False, alt=False):
        code = ("Key" + ch.upper()) if ch.isalpha() else ("Digit" + ch if ch.isdigit() else ch)
        vk = ord(ch.upper()) if ch.isalnum() else 0
        self.key(ch, code, vk, None, 2 | (8 if shift else 0) | (1 if alt else 0))

    def click(self, x, y, count=1):
        for t in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", clickCount=count)
        time.sleep(0.08)

    def shot(self, path):
        data = self.call("Page.captureScreenshot", format="png")["data"]
        open(path, "wb").write(base64.b64decode(data))


FAILS = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  -> " + str(detail)))
    if not cond:
        FAILS.append(name)


def main():
    keep = "--keep" in sys.argv
    shots = sys.argv[sys.argv.index("--shots") + 1] if "--shots" in sys.argv else None
    if shots:
        os.makedirs(shots, exist_ok=True)
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=ROOT)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.handle_error = lambda *a: None   # Chrome drops keep-alive connections
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    prof = tempfile.mkdtemp(prefix="rdh")
    dport = 9300 + os.getpid() % 500
    chrome = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=%d" % dport, "--user-data-dir=" + prof, "--no-first-run",
                               "--window-size=1280,900", "--disable-gpu", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/json" % dport).read())
                tab = next(t for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.1)
        p = Page(tab["webSocketDebuggerUrl"])
        p.call("Runtime.enable")
        p.call("Page.enable")
        p.call("Page.navigate", url="http://127.0.0.1:%d/index.html" % port)
        for _ in range(100):
            if p.ev("!!(window.__rd && document.querySelector('#ed .ln'))"):
                break
            time.sleep(0.1)
        time.sleep(0.6)
        run(p, shots)
        check("no script errors", not p.errors, p.errors)
    finally:
        chrome.kill()
        httpd.shutdown()
        if not keep:
            shutil.rmtree(prof, ignore_errors=True)
    print("\n%d failed" % len(FAILS) if FAILS else "\nall checks passed")
    sys.exit(1 if FAILS else 0)


def run(p, shots):
    S = lambda: p.ev("__rd.src")
    SEL = lambda: p.ev("[__rd.sel.a, __rd.sel.f]")
    LN = lambda i: p.ev("(function(){var d=__rd.lineEls()[%d]; return d ? {cls:d.className, text:d.textContent, vis:Array.from(d.querySelectorAll('.mk:not(.x)')).map(function(x){return x.textContent}).join('|'), hid:d.querySelectorAll('.x').length, html:d.innerHTML} : null})()" % i)

    print("Welcome document")
    check("welcome doc loads", "Welcome to RunDown" in S())
    check("heading renders", "h1" in LN(0)["cls"], LN(0))
    time.sleep(1.0)
    check("KaTeX loaded for the tour's math", p.ev("__rd.katex") == 2, p.ev("__rd.katex"))
    check("display math renders", p.ev("!!document.querySelector('#ed .mwid math')"))
    check("inline math renders", p.ev("!!document.querySelector('#ed .mi math')"))
    check("table renders", p.ev("!!document.querySelector('#ed .blk.tbl.on .td')"))
    check("code block renders", p.ev("!!document.querySelector('#ed .blk.code.on')"))
    check("task boxes", p.ev("document.querySelectorAll('#ed .chk').length") == 2)
    if shots:
        p.shot(os.path.join(shots, "welcome.png"))

    print("New document, typing")
    p.ev("document.querySelector('[data-tab=docs]').click()")
    p.ev("document.getElementById('newDocBtn').click()")
    time.sleep(0.2)
    check("new doc is empty and focused", S() == "" and p.ev("document.activeElement.id") == "ed", (S(), p.ev("document.activeElement.id")))
    p.type("Hello **bold")
    check("unclosed bold stays plain", S() == "Hello **bold" and LN(0)["hid"] == 0 and "<span class=\"b\">" not in LN(0)["html"], LN(0))
    p.type("**")
    l0 = LN(0)
    check("closing ** renders bold at once", '<span class="b">' in l0["html"] and l0["hid"] == 2, l0)
    check("cursor after the bold", SEL() == [14, 14], SEL())
    p.type(" world")
    check("typing after bold stays outside", S() == "Hello **bold** world", S())
    check("bold still rendered", '<span class="b">' in LN(0)["html"])
    p.ev("__rd.setSel(9)")
    time.sleep(0.1)
    l0 = LN(0)
    check("cursor inside bold shows plain Markdown", l0["hid"] == 0 and "**" in l0["vis"] and '<span class="b">' not in l0["html"], l0)
    p.ev("__rd.setSel(" + str(len("Hello **bold** world")) + ")")
    time.sleep(0.1)
    check("leaving renders again", LN(0)["hid"] == 2)

    print("Backspace next to hidden markers")
    p.ev("__rd.setSel(14)")
    p.bs()
    check("backspace after bold deletes the last visible letter", S() == "Hello **bol** world", S())
    check("cursor stays after the bold", SEL() == [13, 13], SEL())
    p.ctrl("z")
    check("undo", S() == "Hello **bold** world", S())
    p.ctrl("y")
    check("redo", S() == "Hello **bol** world", S())
    p.ctrl("z")

    print("Headings and lists")
    p.ev("__rd.setSel(__rd.src.length)")
    p.enter()
    p.type("# Title")
    check("heading line stays plain while editing", "h1" not in LN(1)["cls"] and LN(1)["vis"].startswith("#"), LN(1))
    p.enter()
    check("heading renders when the line is left", "h1" in LN(1)["cls"] and LN(1)["hid"] >= 1, LN(1))
    p.type("- one\ntwo\n")
    check("list continues", S().endswith("- one\n- two\n- "), repr(S()[-30:]))
    p.enter()
    check("Enter on an empty item ends the list", S().endswith("- two\n"), repr(S()[-20:]))
    check("list items render", "li" in LN(2)["cls"] and "ul" in LN(2)["cls"], LN(2))
    p.type("1. a\nb\n\n")
    check("numbered list counts up", "1. a\n2. b\n" in S(), repr(S()))
    p.type("- [ ] task\n")
    check("checklist continues with a box", S().endswith("- [ ] task\n- [ ] "), repr(S()[-30:]))
    p.bs()
    check("backspace after a bullet removes it", S().endswith("- [ ] task\n"), repr(S()[-20:]))
    p.type("> quoted\n")
    check("quote continues", S().endswith("> quoted\n> "), repr(S()[-20:]))
    p.enter()
    check("Enter on an empty quote ends it", S().endswith("> quoted\n"), repr(S()[-20:]))

    print("Code fences close themselves")
    p.type("```js\n")
    check("fence auto-closed", S().endswith("```js\n\n```"), repr(S()[-20:]))
    p.type("let x = 1;")
    n = p.ev("__rd.src.split('\\n').length")
    check("code block raw while inside", p.ev("!document.querySelector('#ed .blk.code.on')"))
    p.arrow("Down", 2)
    check("ArrowDown at the end of the document steps out of the block", S().endswith("let x = 1;\n```\n"), repr(S()[-25:]))
    check("code block renders after leaving", p.ev("!!document.querySelector('#ed .blk.code.on')"), p.ev("__rd.sel"))
    check("fence lines hidden", p.ev("document.querySelectorAll('#ed .blk.code.on > .ln.x').length") == 2)

    print("Unclosed fence stays text")
    p.ev("__rd.setSel(__rd.src.length)")
    p.enter(); p.enter()
    p.ev("__rd.setSel(__rd.src.length)")
    p.type("text with $x^2$ math")
    time.sleep(0.2)
    p.enter()
    check("inline math renders after closing $", p.ev("Array.from(document.querySelectorAll('#ed .mi')).length") >= 1)

    print("Toolbar and shortcuts")
    p.type("make this italic")
    end = p.ev("__rd.src.length")
    p.ev("__rd.setSel(%d, %d)" % (end - 6, end))
    p.ctrl("i")
    check("Ctrl+I wraps the selection", S().endswith("make this *italic*"), repr(S()[-25:]))
    p.ev("__rd.setSel(%d, %d)" % (end - 5, end + 1))
    p.ctrl("i")
    check("Ctrl+I again unwraps", S().endswith("make this italic"), repr(S()[-25:]))
    p.ev("__rd.setSel(__rd.src.length)")
    p.ctrl("2", alt=True)
    check("Ctrl+Alt+2 makes a heading", S().endswith("## make this italic"), repr(S()[-25:]))
    p.ctrl("0", alt=True)
    check("Ctrl+Alt+0 back to text", S().endswith("\nmake this italic"), repr(S()[-25:]))

    print("Arrow keys across a rendered rule")
    p.ev("__rd.setSel(__rd.src.length)")
    p.type("\n---\nafter")
    rule_line = p.ev("__rd.src.split('\\n').length") - 2
    check("rule renders", "hr" in LN(rule_line)["cls"], LN(rule_line))
    p.arrow("Up")
    pos = p.ev("__rd.src.lastIndexOf('---')")
    check("ArrowUp stops on the rule and opens it", pos <= SEL()[0] <= pos + 3 and "hr" not in LN(rule_line)["cls"], (SEL(), pos, LN(rule_line)))
    p.arrow("Up")
    check("and moves on past it", SEL()[0] < pos and "hr" in LN(rule_line)["cls"], (SEL(), LN(rule_line)))

    print("Tables")
    p.ev("__rd.setSel(__rd.src.length)")
    p.type("\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\nend")
    check("table renders once you leave it", p.ev("document.querySelectorAll('#ed .blk.tbl.on').length") == 1)
    check("cells", p.ev("Array.from(document.querySelectorAll('#ed .blk.tbl.on .td')).map(function(c){return c.textContent.trim()}).join(',')") == "A,B,1,2")

    check("the | --- | row is hidden", p.ev("Array.from(document.querySelectorAll('#ed .blk.tbl.on > .ln')).filter(function(d){return d.offsetHeight>0}).length") == 2)

    print("Find and replace")
    p.ctrl("h")
    p.ev("var fi=document.getElementById('findInput'); fi.value='world'; fi.dispatchEvent(new Event('input'))")
    check("find counts matches", p.ev("document.getElementById('findCount').textContent").endswith("of 1"), p.ev("document.getElementById('findCount').textContent"))
    p.ev("document.getElementById('replInput').value='there'; document.getElementById('replAll').click()")
    check("replace all", "**bold** there" in S() and "world" not in S(), S()[:40])
    p.key("Escape", "Escape", 27)
    check("Esc closes find and returns to the page", p.ev("document.getElementById('findBar').classList.contains('hidden')") and p.ev("document.activeElement.id") == "ed")

    print("Link dialog")
    p.ev("__rd.setSel(6, 14)")
    p.ctrl("k")
    time.sleep(0.1)
    check("Ctrl+K opens the link dialog with the text", p.ev("document.getElementById('linkDialog').open && document.getElementById('lkText').value") == "**bold**")
    p.ev("document.getElementById('lkUrl').value='example.com/a b'; document.getElementById('lkOk').click()")
    time.sleep(0.1)
    check("link inserted, odd addresses wrapped in <>", S().startswith("Hello [\\*\\*bold\\*\\*](<https://example.com/a b>)") or S().startswith("Hello [**bold**](<https://example.com/a b>)"), S()[:50])
    check("link renders", p.ev("!!document.querySelector('#ed .ln .lk')"))
    p.ctrl("z")
    check("undo the link", S().startswith("Hello **bold** there"), S()[:30])

    print("Pictures")
    p.ev("__rd.setSel(__rd.src.length)")
    p.enter()
    p.ev("(function(){var c=document.createElement('canvas'); c.width=40; c.height=20; var g=c.getContext('2d'); g.fillStyle='#0F766E'; g.fillRect(0,0,40,20);"
         "return new Promise(function(r){c.toBlob(function(b){var dt=new DataTransfer(); dt.items.add(new File([b],'swatch.png',{type:'image/png'}));"
         "document.getElementById('ed').dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true})); setTimeout(r,300)})})})()")
    check("pasted picture becomes an asset", "![swatch](asset:img_" in S(), S()[-40:])
    check("and shows", p.ev("!!document.querySelector('#ed img.im, #ed img.imprev')"))

    print("Speed in a long document")
    p.ev("document.querySelector('[data-tab=docs]').click(); document.getElementById('newDocBtn').click()")
    big = "\\n".join(["## Section %d\\n\\nSome **bold** text, *italic*, `code` and a [link](https://e.com) in paragraph %d.\\n\\n- item\\n- [ ] task\\n" % (i, i) for i in range(400)])
    p.ev("(function(){var dt=new DataTransfer(); dt.setData('text/plain', %s); document.getElementById('ed').dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}))})()" % json.dumps(big.replace("\\n", "\n")))
    lines = p.ev("__rd.src.split('\\n').length")
    p.ev("__rd.setSel(Math.floor(__rd.src.length/2))")
    t0 = time.time()
    p.type("abcdefghijklmnopqrst")
    per = (time.time() - t0) / 20 * 1000
    ms = p.ev("(function(){var t=performance.now(); for (var i=0;i<20;i++){ document.getElementById('ed').dispatchEvent(new InputEvent('beforeinput',{inputType:'insertText',data:'x',cancelable:true,bubbles:true})); } return (performance.now()-t)/20})()")
    check("typing in a %d-line document takes %.1f ms a key" % (lines, ms), ms < 25, ms)

    p.ev("document.querySelector('[data-tab=docs]').click()")
    p.ev("Array.from(document.querySelectorAll('#docList .rowtitle')).filter(function(b){return b.textContent.indexOf('Hello')===0})[0].click()")
    time.sleep(0.2)
    check("back to the first document", S().startswith("Hello **bold** there"), S()[:30])

    print("Clicking")
    # click in the middle of the rendered bold word on line 0
    p.ev("window.scrollTo(0,0)")
    time.sleep(0.1)
    r = p.ev("(function(){var b=document.querySelector('#ed .ln .b'); var rg=document.createRange(); var t=b.childNodes[1]; rg.setStart(t,1); rg.setEnd(t,2); var q=rg.getBoundingClientRect(); return [q.left+1,q.top+q.height/2]})()")
    p.click(r[0], r[1])
    check("click inside bold opens it", LN(0)["hid"] == 0 and "**" in LN(0)["vis"], LN(0))
    check("click lands on the right letter", SEL()[0] in (9, 10), SEL())

    print("Escape renders everything")
    p.key("Escape", "Escape", 27)
    time.sleep(0.1)
    VISMK = "Array.from(document.querySelectorAll('#ed .mk:not(.x)')).filter(function(x){return !x.closest('.ln.x')})"
    check("blur renders the active line", p.ev(VISMK + ".length") == 0, p.ev(VISMK + ".map(function(x){return x.textContent})"))

    print("Views")
    p.ev("document.querySelector('[data-mode=source]').click()")
    time.sleep(0.1)
    check("Source shows every marker", p.ev("document.querySelectorAll('#ed .x').length") == 0 and p.ev("!document.querySelector('#ed .blk.tbl.on')"))
    p.ev("document.querySelector('[data-mode=read]').click()")
    time.sleep(0.1)
    check("Read renders everything and locks the page", p.ev("document.getElementById('ed').getAttribute('contenteditable')") == "false" and p.ev(VISMK + ".length") == 0)
    p.ev("document.querySelector('[data-mode=live]').click()")
    time.sleep(0.1)
    check("Live again", p.ev("document.getElementById('ed').getAttribute('contenteditable')") == "true" and p.ev("document.activeElement.id") == "ed")

    print("Paste")
    p.ev("__rd.setSel(__rd.src.length)")
    p.enter()
    p.ev("(function(){var dt=new DataTransfer(); dt.setData('text/html','<p>From <b>the web</b></p>'); dt.setData('text/plain','From the web'); document.getElementById('ed').dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}))})()")
    check("formatted paste becomes Markdown", S().endswith("From **the web**"), repr(S()[-20:]))
    p.ctrl("z")
    check("paste undoes in one step", not S().endswith("web**"), repr(S()[-20:]))

    print("IME composition")
    p.ev("__rd.setSel(__rd.src.length)")
    p.call("Input.imeSetComposition", text="ni", selectionStart=2, selectionEnd=2)
    p.call("Input.imeSetComposition", text="nih", selectionStart=3, selectionEnd=3)
    p.call("Input.insertText", text="你好")
    time.sleep(0.2)
    check("composed text lands in the document", S().endswith("你好"), repr(S()[-10:]))
    p.type("!")
    check("typing continues after the composition", S().endswith("你好!"), repr(S()[-10:]))

    print("Styled editing")
    p.ev("localStorage.setItem('runDown:settings', JSON.stringify({editStyle:'styled'}))")
    p.ev("document.querySelector('[data-tab=settings]').click()")
    p.ev("document.querySelector('#set_editStyle [data-val=styled]').click()")
    p.ev("document.querySelector('[data-tab=write]').click()")
    p.ev("__rd.setSel(9)")
    time.sleep(0.1)
    l0 = LN(0)
    check("Keep the formatting shows markers on bold text", '<span class="b">' in l0["html"] and l0["hid"] == 0, l0)
    p.ev("document.querySelector('[data-tab=settings]').click()")
    p.ev("document.querySelector('#set_editStyle [data-val=plain]').click()")
    p.ev("document.querySelector('[data-tab=write]').click()")

    print("Conversions")
    md = p.ev("__rd.htmlToMd('<h2>Hi</h2><p>Some <b>bold</b> and <a href=\"https://x.org\">link</a>.</p><ul><li>one</li><li>two<ul><li>deep</li></ul></li></ul>')")
    check("HTML to Markdown", md == "## Hi\n\nSome **bold** and [link](https://x.org).\n\n- one\n- two\n  - deep", repr(md))
    html = p.ev("__rd.mdToHtml('# T\\n\\nA **b** [l](https://e.com)\\n\\n- x\\n  - y\\n- [x] z\\n\\n| a | b |\\n|---|:-:|\\n| 1 | 2 |', true)")
    check("Markdown to HTML", '<h1 id="t">T</h1>' in html and "<strong>b</strong>" in html and '<a href="https://e.com">l</a>' in html and "<ul><li>x<ul><li>y</li></ul></li><li><input" in html and "text-align:center" in html, html)
    check("javascript: links are dropped", 'href' not in p.ev("__rd.mdToHtml('[x](javascript:alert(1))', true)"))

    print("Tabs")
    TABS = lambda: p.ev("[...document.querySelectorAll('#docTabs .dtab')].map(t => (t.classList.contains('cur') ? '*' : '') + t.querySelector('.dtab-name').textContent)")
    before = TABS()
    p.ev("document.getElementById('tabNewBtn').click()"); time.sleep(0.1)
    p.type("# Tab two"); time.sleep(0.8)
    check("+ opens a new tab after the current one", TABS()[-1] == "*Tab two" and len(TABS()) == len(before) + 1, TABS())
    p.type(" x")   # typing in the page, so clicking a tab blurs it and saves on the way (that once swallowed the first click)
    r = p.ev("(() => { const r = document.querySelectorAll('#docTabs .dtab-name')[0].getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()")
    p.click(r[0], r[1]); time.sleep(0.3)
    check("one real click on a tab switches documents", TABS()[0].startswith("*") and not S().startswith("# Tab two"), TABS())
    def click_tab(prefix):
        r = p.ev("(() => { const b = [...document.querySelectorAll('#docTabs .dtab-name')].find(b => b.textContent.startsWith(%r)); const r = b.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()" % prefix)
        p.click(r[0], r[1]); time.sleep(0.4)
    SPOT = lambda: p.ev("(() => { const top = document.getElementById('bars').getBoundingClientRect().bottom; const ls = __rd.lineEls(); for (let i = 0; i < ls.length; i++){ const d = ls[i]; if (!d || !d.getClientRects().length) continue; const r = d.getBoundingClientRect(); if (r.bottom > top) return [i, Math.round(r.top)]; } return null; })()")
    click_tab("Section 0")
    p.ev("window.scrollTo(0, 6000)"); time.sleep(0.3)
    spot = SPOT()
    click_tab("Tab two")
    check("a tab you haven't scrolled opens at the top", p.ev("window.scrollY") < 5, p.ev("window.scrollY"))
    click_tab("Section 0")
    check("switching back returns to the same spot", SPOT() == spot and spot[0] > 20, (SPOT(), spot))
    p.ev("document.querySelector('[data-tab=docs]').click()"); time.sleep(0.2)
    p.ev("document.querySelector('[data-tab=write]').click()"); time.sleep(0.4)
    check("Documents and back to Write returns to the same spot", SPOT() == spot, (SPOT(), spot))
    check("the tabs sit next to the name, in the bar that stays on screen", p.ev("!!document.querySelector('.bars .docbar #docTabs') && !document.querySelector('header #docTabs')"))
    p.ev("[...document.querySelectorAll('#docTabs .dtab-name')].find(b => b.textContent.startsWith('Tab two')).click()"); time.sleep(0.2)
    check("a tab keeps its undo history", S() == "# Tab two x" and p.ev("!document.querySelector('#tbar button').disabled"))
    p.ev("[...document.querySelectorAll('#docTabs .dtab')].find(t => t.classList.contains('cur')).querySelector('.dtab-x').click()"); time.sleep(0.2)
    check("closing a tab opens its neighbor", len(TABS()) == len(before) and any(t.startswith("*") for t in TABS()), TABS())
    p.ev("document.querySelector('[data-tab=docs]').click()")
    check("closing a tab keeps the document", "Tab two" in p.ev("document.getElementById('docList').textContent"))
    p.ev("document.querySelector('[data-tab=write]').click()")
    p.ev("document.getElementById('tabNewBtn').click()"); time.sleep(0.1)
    p.type("| " + " | ".join("Column%dWithAVeryLongHeaderName" % i for i in range(12)) + " |"); p.enter(); p.type("|" + "---|" * 12); p.enter(); p.enter(); p.type("x")
    time.sleep(0.2)
    wide = p.ev("(() => { const t = document.querySelector('#ed .blk.tbl.on'), pg = document.getElementById('page').getBoundingClientRect(); return t ? [t.getBoundingClientRect().right <= pg.right, t.scrollWidth > t.clientWidth] : null; })()")
    check("a wide table scrolls inside the page", wide == [True, True], wide)

    print("Persistence")
    time.sleep(1.0)
    saved = S()
    p.call("Page.reload")
    for _ in range(100):
        if p.ev("!!(window.__rd && document.querySelector('#ed .ln'))"):
            break
        time.sleep(0.1)
    time.sleep(0.5)
    check("document survives a reload", S() == saved, (S()[:60], saved[:60]))
    if shots:
        p.shot(os.path.join(shots, "typed.png"))


if __name__ == "__main__":
    main()
