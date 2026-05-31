#!/usr/bin/env python3
"""miromind 模型能力探测脚本（仅用标准库，无需安装依赖）。

用途：在能联通 api.miromind.ai 的网络下运行，探测模型支持哪些能力，
据此决定 Agent 走「结构化输出」还是「手写 JSON 解析」路径。

运行（先确保 .env 里配置了 MIROMIND_API_KEY，或导出该环境变量）：
    python scripts/probe_miromind.py

读取的环境变量（找不到环境变量时会尝试解析项目根目录的 .env）：
    MIROMIND_API_KEY   (必填，无默认值，不硬编码以免泄露)
    MIROMIND_BASE_URL  (默认 https://api.miromind.ai/v1)
    MIROMIND_MODEL     (默认 mirothinker-1-7-deepresearch-mini)

把整段输出贴回给开发同学即可。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


def _load_env_file() -> None:
    """Minimal .env loader (stdlib only) so the probe works without python-dotenv."""
    path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
    except Exception:
        pass


_load_env_file()
API_KEY = os.getenv("MIROMIND_API_KEY", "")
BASE_URL = os.getenv("MIROMIND_BASE_URL", "https://api.miromind.ai/v1").rstrip("/")
MODEL = os.getenv("MIROMIND_MODEL", "mirothinker-1-7-deepresearch-mini")
ENDPOINT = f"{BASE_URL}/chat/completions"
TIMEOUT = 120


def _post(payload: dict, *, stream: bool = False):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=data,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {"ok": True, "status": resp.status, "elapsed": time.time() - t0, "body": raw}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": e.code, "elapsed": time.time() - t0, "body": body}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": None, "elapsed": time.time() - t0, "body": f"{type(e).__name__}: {e}"}


def _short(text: str, n: int = 600) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + f"... (+{len(text) - n} chars)"


def _content(body: str) -> str:
    try:
        obj = json.loads(body)
        return obj["choices"][0]["message"]["content"]
    except Exception:
        return ""


def _usage(body: str) -> str:
    try:
        return json.dumps(json.loads(body).get("usage") or {}, ensure_ascii=False)
    except Exception:
        return "{}"


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main() -> None:
    if not API_KEY:
        print("未找到 MIROMIND_API_KEY。请在项目根目录 .env 中配置，或导出环境变量：")
        print("  export MIROMIND_API_KEY=sk-...")
        print("然后重新运行：python scripts/probe_miromind.py")
        return

    print(f"endpoint = {ENDPOINT}")
    print(f"model    = {MODEL}")
    print(f"key      = {API_KEY[:6]}...{API_KEY[-4:]}")

    # 1) 基本连通性 + 延迟
    section("[1] 基本 chat completion（连通性 / 延迟 / 返回结构）")
    r = _post({"model": MODEL, "messages": [{"role": "user", "content": "Reply with exactly: OK"}]})
    print(f"status={r['status']}  elapsed={r['elapsed']:.1f}s")
    print(f"content = {_short(_content(r['body']))!r}")
    print(f"usage   = {_usage(r['body'])}")
    if not r["ok"]:
        print("原始返回：", _short(r["body"]))
        print("\n基本调用失败，后续测试跳过。请先确认 key / base_url / model / 网络放行。")
        return

    # 2) system message 支持
    section("[2] system message 支持")
    r = _post({"model": MODEL, "messages": [
        {"role": "system", "content": "You are a calculator. Answer with only the number."},
        {"role": "user", "content": "2 + 2 = ?"},
    ]})
    print(f"status={r['status']}  content={_short(_content(r['body']))!r}")

    # 3) JSON 模式 response_format
    section("[3] response_format=json_object 支持（结构化输出关键能力）")
    r = _post({
        "model": MODEL,
        "messages": [{"role": "user", "content": 'Return a JSON object: {"topic": string, "tickers": string array} for NVDA research.'}],
        "response_format": {"type": "json_object"},
    })
    print(f"status={r['status']}")
    print(f"content = {_short(_content(r['body']))!r}")
    if not r["ok"]:
        print("→ 不支持 response_format（预期之内），原始返回：", _short(r["body"], 300))

    # 4) tools / function calling 支持
    section("[4] tools / function calling 支持（决定 with_structured_output 能否用）")
    r = _post({
        "model": MODEL,
        "messages": [{"role": "user", "content": "Call the save_plan tool with topic='NVDA' and tickers=['NVDA']."}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "save_plan",
                "description": "Save a research plan",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string"},
                        "tickers": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["topic", "tickers"],
                },
            },
        }],
        "tool_choice": "auto",
    })
    print(f"status={r['status']}")
    try:
        msg = json.loads(r["body"])["choices"][0]["message"]
        print(f"tool_calls present = {bool(msg.get('tool_calls'))}")
        print(f"message = {_short(json.dumps(msg, ensure_ascii=False))}")
    except Exception:
        print("解析失败，原始返回：", _short(r["body"], 400))

    # 5) streaming 支持
    section("[5] streaming（stream=true）是否可用")
    data = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": "Count 1 to 3."}], "stream": True}).encode()
    req = urllib.request.Request(ENDPOINT, data=data, headers={
        "Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            first = resp.readline().decode("utf-8", errors="replace").strip()
            print(f"status={resp.status}  first_line={_short(first, 200)!r}")
            print("→ 看到 'data:' 前缀即支持 SSE 流式")
    except urllib.error.HTTPError as e:
        print(f"status={e.code}  → 可能不支持流式。body={_short(e.read().decode('utf-8', 'replace'), 200)}")
    except Exception as e:  # noqa: BLE001
        print(f"流式请求异常：{type(e).__name__}: {e}")

    # 6) 朴素 JSON 抽取可靠性（无 response_format，纯 prompt 约束）
    section("[6] 纯 prompt 约束下的 JSON 抽取可靠性（手写解析路径的基础）")
    r = _post({"model": MODEL, "messages": [{"role": "user", "content": (
        "Return ONLY a single JSON object, no markdown, no code fences.\n"
        'Schema: {"verdict": one of "bullish"/"bearish"/"neutral", "confidence": 0-1 number}\n'
        "Question: Is NVDA a buy given strong AI demand?"
    )}]})
    print(f"status={r['status']}  content={_short(_content(r['body']))!r}")

    section("探测完成")
    print("请把以上完整输出贴回。重点看：[3]/[4] 是否支持结构化输出，[1] 延迟，[5] 流式。")


if __name__ == "__main__":
    main()
