"""用独立无头 Chrome 验证真实页面与消息链路，并保存原始截图。

需本机 Chrome、websockets、httpx。不会提交退款或开票申请。
"""
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

import httpx
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/screenshots"


class Browser:
    def __enter__(self):
        chrome = os.environ.get("CHROME_PATH") or shutil.which("chrome") or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.profile = tempfile.TemporaryDirectory(prefix="legal-browser-")
        self.proc = subprocess.Popen([chrome, "--headless=new", "--disable-gpu", "--no-proxy-server",
                                      f"--remote-debugging-port={self.port}", "--window-size=1440,960",
                                      "--hide-scrollbars", "--user-data-dir=" + self.profile.name, "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.client = httpx.Client(trust_env=False, timeout=5)
        for _ in range(60):
            try:
                pages = self.client.get(f"http://127.0.0.1:{self.port}/json/list").json()
                url = next(p["webSocketDebuggerUrl"] for p in pages if p["type"] == "page")
                break
            except (httpx.HTTPError, StopIteration):
                time.sleep(.25)
        self.ws = connect(url, max_size=50*1024*1024)
        self.mid = 0
        self.call("Page.enable")
        self.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 960, "deviceScaleFactor": 1, "mobile": False})
        return self

    def call(self, method, params=None):
        self.mid += 1
        self.ws.send(json.dumps({"id": self.mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv(timeout=15))
            if msg.get("id") == self.mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def js(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if result.get("exceptionDetails"):
            raise RuntimeError(result["exceptionDetails"])
        return result.get("result", {}).get("value")

    def wait(self, expression, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.js(expression):
                return
            time.sleep(.4)
        raise AssertionError("页面等待超时: " + expression)

    def navigate(self, url, text):
        self.call("Page.navigate", {"url": url})
        self.wait("document.readyState === 'complete' && document.body.innerText.includes(" + json.dumps(text) + ")")
        time.sleep(1)
        assert not self.js("Boolean(document.querySelector('[role=alert]'))"), self.js("document.body.innerText")

    def click(self, text):
        self.js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===" + json.dumps(text) + ").click()")

    def shot(self, name):
        path = OUT / name
        path.write_bytes(base64.b64decode(self.call("Page.captureScreenshot", {"format": "png"})["data"]))
        print("screenshot", name, flush=True)

    def __exit__(self, *_):
        self.ws.close()
        self.client.close()
        self.proc.terminate()
        self.proc.wait(timeout=10)
        # Chrome 子进程退出稍晚；临时 profile 不进入交付目录。
        try:
            self.profile.cleanup()
        except PermissionError:
            pass


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(trust_env=False, timeout=10) as client, Browser() as browser:
        browser.navigate("http://127.0.0.1:4173/chat", "智能客服")
        browser.wait("Boolean(document.querySelector('textarea')) && document.body.innerText.includes('已连接')")
        token = browser.js("localStorage.getItem('legal_customer_token')")
        headers = {"Authorization": "Bearer " + token}
        before = client.get("http://127.0.0.1:8010/api/v1/chat/history", headers=headers).raise_for_status().json()
        known = {m["message_id"] for m in before}
        text = "请查询订单 ORDER_002 的合同审核办理进度"
        browser.js("document.querySelector('textarea').value=" + json.dumps(text) + ";document.querySelector('textarea').dispatchEvent(new Event('input',{bubbles:true}))")
        time.sleep(.3)
        browser.click("发送")
        deadline = time.monotonic()+75
        reply = None
        while time.monotonic() < deadline:
            messages = client.get("http://127.0.0.1:8010/api/v1/chat/history", headers=headers).raise_for_status().json()
            reply = next((m for m in reversed(messages) if m["message_id"] not in known and m["role"]=="ai"), None)
            if reply:
                break
            time.sleep(1)
        assert reply and not reply["content"].get("fallback"), reply
        # 等待同一条回复经 WebSocket 出现在 DOM，证明不是仅 API 返回。
        reply_text = reply["content"]["text"]
        browser.wait("document.body.innerText.includes(" + json.dumps(reply_text[:30]) + ")")
        browser.shot("01-DeepSeek客服对话.png")
        for url, title, name in [
            ("/plans", "服务套餐", "02-服务套餐.png"),
            ("/me", "我的订单", "03-我的订单.png"),
            ("/me/orders/ORDER_001", "订单详情", "04-订单详情.png"),
            ("/me/orders/ORDER_001/refund", "退款 / 售后申请", "05-退款申请页.png"),
            ("/me/orders/ORDER_001/invoice", "开票申请", "06-开票申请页.png"),
        ]:
            browser.navigate("http://127.0.0.1:4173" + url, title)
            browser.shot(name)
        browser.navigate("http://127.0.0.1:4174", "人工工作台")
        browser.js("localStorage.setItem('legal_staff_id','admin_001');localStorage.removeItem('legal_staff_token')")
        browser.navigate("http://127.0.0.1:4174", "AI 运行记录")
        browser.wait("document.body.innerText.includes('DEEPSEEK') && document.body.innerText.includes('deepseek-flash')")
        browser.shot("07-DeepSeek运行审计.png")
        browser.click("人工工作台")
        time.sleep(1)
        browser.shot("08-人工工作台.png")
    report = dict(passed=True, path="Browser UI → Customer API → Turn Worker → DeepSeek → 工具 → 结算 → Outbox → WebSocket → DOM",
                  user_message=text, ai_reply=reply_text, screenshots=sorted(p.name for p in OUT.glob("*.png")))
    (ROOT/"reports/browser-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("浏览器收发消息与实时推送验收 PASS")


if __name__ == "__main__":
    main()
